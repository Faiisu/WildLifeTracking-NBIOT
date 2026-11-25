# /// script
# requires-python = ">=3.10"
# dependencies = ["pygdbmi>=0.11"]
# ///

"""GDB debug CLI and HTTP server for nRF Connect SDK (NCS) / Zephyr.

This module provides a command-line interface and a local HTTP server that
drive a GDB debug session against Nordic Semiconductor hardware using the
current NCS/Zephyr toolchain (``west`` + ``nrfutil`` + ``arm-zephyr-eabi-gdb``).

The server holds a single, long-lived GDB session so that the debug state
(breakpoints, the selected frame, the program counter) persists across
individual HTTP requests. GDB is driven through its Machine Interface
(``--interpreter=mi2``) via :mod:`pygdbmi`, which yields structured records
instead of scraped console text. Device-level operations (reset, halt, go)
shell out to ``nrfutil device``.

Architecture::

    Agent --HTTP--> gdb_server.py --pygdbmi(MI2)--> arm-zephyr-eabi-gdb
                                                        |
                                                target remote :2331
                                                        |
                                    JLinkGDBServer (via `west debugserver`)
                                                        |
                                                      SWD -> nRF target

The GDB session, once running, holds an exclusive lock on the J-Link probe.
While the server is up no other tool can use the probe, so the server must be
stopped explicitly (``gdb_server.py stop``) when debugging is finished.

Requirements:
    pip install pygdbmi

PEP 723 invocation (preferred):
    The script declares its dependencies inline in the PEP 723 header above, so
    ``uv run`` and ``pipx run`` install pygdbmi automatically on first use::

        uv run scripts/gdb_server.py status --port-num 8090
        uv run scripts/gdb_server.py serve --build-dir build --port-num 8090

license: "SPDX-License-Identifier: LicenseRef-Nordic-5-Clause"
metadata:
    version: 0.0.1
    Copyright: "(c) 2026 Nordic Semiconductor ASA"
"""

import argparse
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

try:  # pygdbmi is a PEP 723 runtime dependency; keep the module importable without it.
    from pygdbmi.constants import GdbTimeoutError  # type: ignore[import-not-found, import-untyped]
    from pygdbmi.gdbcontroller import GdbController  # type: ignore[import-not-found, import-untyped]
except ImportError:  # pragma: no cover - exercised only when the dep is absent
    GdbController = None  # type: ignore[assignment,misc]

    class GdbTimeoutError(Exception):  # type: ignore[no-redef]  # noqa: N818
        """Fallback when pygdbmi is unavailable (never raised in that case)."""


# --- Constants ---

DEFAULT_HTTP_PORT = 8090
DEFAULT_GDB_SERVER_PORT = 2331  # JLinkGDBServer default when launched via `west debug`.
COREDUMP_GDB_PORT = 1234  # Fixed port used by Zephyr's coredump_gdbserver.py.
DEFAULT_RTT_TELNET_PORT = 19021  # JLinkGDBServer streams SEGGER RTT channel 0 here.
DEFAULT_CORE = "app"  # Default core name for a single-core session / the app core.
STACK_SCAN_CAP = 0x10000  # Max bytes read per thread stack for high-water scanning (64 KiB).
DEFAULT_GDB_BINARY = "arm-zephyr-eabi-gdb"  # Zephyr SDK GDB shipped with NCS (Arm cores).
DEFAULT_RISCV_GDB = "riscv64-zephyr-elf-gdb"  # Zephyr SDK GDB for RISC-V cores (e.g. nRF54L FLPR).
DEFAULT_RUNNER = "jlink"
MI_TIMEOUT_SEC = 10.0

# Resource bounds for the local HTTP server (defensive limits).
MAX_MEMORY_READ_BYTES = 0x10000  # Cap a single /memory read (64 KiB): protects the probe and response.
MAX_REQUEST_BODY_BYTES = 1 << 20  # Cap a POST body (1 MiB): prevents memory exhaustion.
TOKEN_HEADER = "X-GDB-Token"
STATE_FILE_PREFIX = "server-"

# Only Zephyr SDK GDB binaries may be launched from an HTTP /connect request.
# The gdb binary is chosen by arch auto-detection; the HTTP override, if given,
# must resolve to one of these basenames (a full path is allowed on the CLI,
# which is the trusted operator's own machine).
ALLOWED_GDB_BINARIES = frozenset({DEFAULT_GDB_BINARY, DEFAULT_RISCV_GDB})

# Token-aware output: responses larger than this many characters (~4 chars/token,
# so ~10k tokens) are spilled to a file to protect the agent's context window.
DEFAULT_RESPONSE_LIMIT_CHARS = 40000

# Reset kinds accepted by `nrfutil device reset --reset-kind`.
RESET_KINDS = frozenset({
    "RESET_SYSTEM",
    "RESET_SOFT",
    "RESET_PIN",
    "RESET_HARD",
    "RESET_DEFAULT",
    "RESET_VIA_SECDOM",
})


# --- Errors ---


class GdbSessionError(RuntimeError):
    """Raised when a GDB session operation cannot be completed."""


# --- Pure MI response helpers (hardware-independent, unit-testable) ---


def find_result_record(responses: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return the ``result`` record from a list of pygdbmi response dicts.

    :param responses: List of response dicts as produced by
        :meth:`pygdbmi.gdbcontroller.GdbController.write`.
    :returns: The first record whose ``type`` is ``"result"``, or ``None`` if
        no result record is present.
    """
    for record in responses:
        if record.get("type") == "result":
            return record
    return None


def result_status(responses: list[dict[str, Any]]) -> str:
    """Return the MI result class (e.g. ``done``, ``running``, ``error``).

    :param responses: List of pygdbmi response dicts.
    :returns: The ``message`` of the result record, or ``"unknown"`` when no
        result record is found.
    """
    record = find_result_record(responses)
    if record is None:
        return "unknown"
    return str(record.get("message") or "unknown")


def raise_on_error(responses: list[dict[str, Any]]) -> None:
    """Raise :class:`GdbSessionError` if the MI response reports an error.

    :param responses: List of pygdbmi response dicts.
    :raises GdbSessionError: if a result record has message ``"error"``.
    """
    record = find_result_record(responses)
    if record is not None and record.get("message") == "error":
        payload = record.get("payload") or {}
        msg = payload.get("msg") if isinstance(payload, dict) else None
        raise GdbSessionError(str(msg or "GDB reported an error"))


def _result_payload(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the payload dict of the MI result record (empty dict if absent)."""
    record = find_result_record(responses)
    if record is None:
        return {}
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def parse_break_insert(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the breakpoint description from a ``-break-insert`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: The ``bkpt`` payload dict (number, addr, func, file, line, ...),
        or an empty dict if not present.
    """
    payload = _result_payload(responses)
    bkpt = payload.get("bkpt")
    return bkpt if isinstance(bkpt, dict) else {}


def parse_break_list(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract the list of breakpoints from a ``-break-list`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: A list of breakpoint dicts. Handles the nested
        ``BreakpointTable.body`` shape emitted by GDB/MI.
    """
    payload = _result_payload(responses)
    table = payload.get("BreakpointTable")
    if isinstance(table, dict):
        body = table.get("body")
        if isinstance(body, list):
            return [b for b in body if isinstance(b, dict)]
    return []


def parse_stack_frames(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract stack frames from a ``-stack-list-frames`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: A list of frame dicts (level, addr, func, file, line).
    """
    payload = _result_payload(responses)
    stack = payload.get("stack")
    frames: list[dict[str, Any]] = []
    if isinstance(stack, list):
        for item in stack:
            # GDB/MI represents each entry as {"frame": {...}} or as the frame dict.
            if isinstance(item, dict):
                frame = item.get("frame") if "frame" in item else item
                if isinstance(frame, dict):
                    frames.append(frame)
    return frames


def parse_variables(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract variables from a ``-stack-list-variables`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: A list of variable dicts (name, value).
    """
    payload = _result_payload(responses)
    variables = payload.get("variables")
    if isinstance(variables, list):
        return [v for v in variables if isinstance(v, dict)]
    return []


def parse_register_values(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract register values from a ``-data-list-register-values`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: A list of ``{"number": ..., "value": ...}`` dicts.
    """
    payload = _result_payload(responses)
    values = payload.get("register-values")
    if isinstance(values, list):
        return [v for v in values if isinstance(v, dict)]
    return []


def parse_register_names(responses: list[dict[str, Any]]) -> list[str]:
    """Extract register names from a ``-data-list-register-names`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: A list of register-name strings (index == register number).
    """
    payload = _result_payload(responses)
    names = payload.get("register-names")
    if isinstance(names, list):
        return [str(n) for n in names]
    return []


def name_registers(values: list[dict[str, Any]], names: list[str]) -> dict[str, str]:
    """Map register values to their names using the register-number index.

    :param values: Register-value dicts from :func:`parse_register_values`.
    :param names: Register-name list from :func:`parse_register_names`.
    :returns: A dict mapping register name to its (string) value. Registers
        with an empty name or an out-of-range number are skipped.
    """
    named: dict[str, str] = {}
    for entry in values:
        try:
            number = int(entry.get("number", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= number < len(names) and names[number]:
            named[names[number]] = str(entry.get("value", ""))
    return named


def parse_evaluate(responses: list[dict[str, Any]]) -> Optional[str]:
    """Extract the value from a ``-data-evaluate-expression`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: The evaluated value as a string, or ``None`` if absent.
    """
    payload = _result_payload(responses)
    value = payload.get("value")
    return None if value is None else str(value)


def parse_memory(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract memory contents from a ``-data-read-memory-bytes`` response.

    :param responses: List of pygdbmi response dicts.
    :returns: A list of memory-region dicts (begin, offset, end, contents).
    """
    payload = _result_payload(responses)
    memory = payload.get("memory")
    if isinstance(memory, list):
        return [m for m in memory if isinstance(m, dict)]
    return []


def extract_stop_event(responses: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return the payload of the last ``*stopped`` async record, if any.

    GDB/MI emits an out-of-band ``*stopped`` notification whenever the inferior
    halts (breakpoint hit, step complete, signal received, exit). This returns
    that payload so callers can learn *why* execution stopped.

    :param responses: List of pygdbmi response dicts.
    :returns: The payload dict of the last ``stopped`` notification, or
        ``None`` if the inferior did not stop in this batch of records.
    """
    stop: Optional[dict[str, Any]] = None
    for record in responses:
        if record.get("type") == "notify" and record.get("message") == "stopped":
            payload = record.get("payload")
            if isinstance(payload, dict):
                stop = payload
    return stop


def summarize_stop(stop: Optional[dict[str, Any]]) -> str:
    """Build a short human-readable summary of a ``*stopped`` payload.

    :param stop: A stop-event payload from :func:`extract_stop_event`, or
        ``None`` when execution is still running.
    :returns: A concise, single-line description of the stop reason and
        location.
    """
    if not stop:
        return "Running (no stop event yet)."

    reason = str(stop.get("reason", "unknown"))
    frame_raw = stop.get("frame")
    frame = frame_raw if isinstance(frame_raw, dict) else {}
    func = frame.get("func", "??")
    where = ""
    if frame.get("file") and frame.get("line"):
        where = f" at {frame['file']}:{frame['line']}"
    elif frame.get("addr"):
        where = f" at {frame['addr']}"

    if reason == "breakpoint-hit":
        return f"Hit breakpoint {stop.get('bkptno', '?')} in {func}{where}."
    if reason == "end-stepping-range":
        return f"Stepped to {func}{where}."
    if reason == "function-finished":
        return f"Function finished; now in {func}{where}."
    if reason in ("signal-received", "exited-signalled"):
        signame = stop.get("signal-name", "signal")
        return f"Stopped by {signame} in {func}{where}."
    if reason.startswith("exited"):
        return f"Inferior exited (reason: {reason}, code: {stop.get('exit-code', '?')})."
    return f"Stopped ({reason}) in {func}{where}."


def summarize_crash(
    stop: Optional[dict[str, Any]],
    frames: list[dict[str, Any]],
    variables: list[dict[str, Any]],
    registers: dict[str, str],
) -> dict[str, Any]:
    """Assemble a structured crash/state analysis from collected MI data.

    :param stop: The stop-event payload (:func:`extract_stop_event`).
    :param frames: Stack frames (:func:`parse_stack_frames`).
    :param variables: Local variables of the current frame
        (:func:`parse_variables`).
    :param registers: Named register values (:func:`name_registers`).
    :returns: A JSON-serializable dict with a ``summary`` line, the reason, a
        compact backtrace, the fault-relevant core registers, and locals.
    """
    fault_regs = {
        key: registers[key]
        for key in ("pc", "lr", "sp", "msp", "psp", "xpsr", "r0", "r1", "r2", "r3")
        if key in registers
    }
    backtrace = [
        {
            "level": f.get("level"),
            "func": f.get("func"),
            "file": f.get("file"),
            "line": f.get("line"),
            "addr": f.get("addr"),
        }
        for f in frames
    ]
    return {
        "summary": summarize_stop(stop),
        "reason": (stop or {}).get("reason"),
        "signal": (stop or {}).get("signal-name"),
        "backtrace": backtrace,
        "fault_registers": fault_regs,
        "locals": [{"name": v.get("name"), "value": v.get("value")} for v in variables],
    }


# Cortex-M System Control Block fault registers (memory-mapped).
SCB_CFSR = 0xE000ED28  # Configurable Fault Status Register (MMFSR|BFSR|UFSR)
SCB_HFSR = 0xE000ED2C  # HardFault Status Register
SCB_MMFAR = 0xE000ED34  # MemManage Fault Address Register
SCB_BFAR = 0xE000ED38  # BusFault Address Register

# (bit, name, human description) for each Configurable Fault Status sub-register.
_CFSR_BITS = [
    # MemManage (MMFSR, bits 0-7)
    (0, "IACCVIOL", "Instruction access violation"),
    (1, "DACCVIOL", "Data access violation (MMFAR valid)"),
    (3, "MUNSTKERR", "MemManage fault on exception return unstacking"),
    (4, "MSTKERR", "MemManage fault on exception entry stacking"),
    (5, "MLSPERR", "MemManage fault during lazy FP state preservation"),
    (7, "MMARVALID", "MMFAR holds a valid fault address"),
    # BusFault (BFSR, bits 8-15)
    (8, "IBUSERR", "Instruction bus error"),
    (9, "PRECISERR", "Precise data bus error (BFAR valid)"),
    (10, "IMPRECISERR", "Imprecise data bus error"),
    (11, "UNSTKERR", "Bus fault on exception return unstacking"),
    (12, "STKERR", "Bus fault on exception entry stacking"),
    (13, "LSPERR", "Bus fault during lazy FP state preservation"),
    (15, "BFARVALID", "BFAR holds a valid fault address"),
    # UsageFault (UFSR, bits 16-31)
    (16, "UNDEFINSTR", "Undefined instruction"),
    (17, "INVSTATE", "Invalid state (illegal EPSR/Thumb)"),
    (18, "INVPC", "Invalid PC load (bad EXC_RETURN)"),
    (19, "NOCP", "No coprocessor / coprocessor disabled"),
    (20, "STKOF", "Stack overflow (Armv8-M)"),
    (24, "UNALIGNED", "Unaligned memory access"),
    (25, "DIVBYZERO", "Divide by zero"),
]

_HFSR_BITS = [
    (1, "VECTTBL", "Vector table read fault"),
    (30, "FORCED", "Fault escalated to HardFault"),
    (31, "DEBUGEVT", "Debug event"),
]


def decode_fault(cfsr: int, hfsr: int, bfar: int, mmfar: int) -> dict[str, Any]:
    """Decode the Cortex-M fault status registers into human-readable form.

    :param cfsr: Configurable Fault Status Register value.
    :param hfsr: HardFault Status Register value.
    :param bfar: BusFault Address Register value.
    :param mmfar: MemManage Fault Address Register value.
    :returns: A dict with the raw register values (hex), the list of active
        fault flags (name + description), the resolved fault address (if the
        relevant ``*ARVALID`` bit is set), and a one-line ``summary``.
    """
    cfsr_flags = [{"name": name, "description": desc} for bit, name, desc in _CFSR_BITS if cfsr & (1 << bit)]
    hfsr_flags = [{"name": name, "description": desc} for bit, name, desc in _HFSR_BITS if hfsr & (1 << bit)]

    active = {f["name"] for f in cfsr_flags}
    fault_address: Optional[str] = None
    if "BFARVALID" in active:
        fault_address = f"0x{bfar:08x}"
    elif "MMARVALID" in active:
        fault_address = f"0x{mmfar:08x}"

    # Build a concise, prioritized summary.
    if "PRECISERR" in active:
        summary = f"Precise data bus fault accessing {fault_address} (bad pointer / unmapped address)."
    elif "IMPRECISERR" in active:
        summary = "Imprecise data bus fault (a buffered write to a bad address; address not captured)."
    elif "IBUSERR" in active:
        summary = "Instruction bus error (executing from an invalid address, e.g. corrupted return/function pointer)."
    elif "DACCVIOL" in active:
        summary = f"MPU data access violation at {fault_address}."
    elif "IACCVIOL" in active:
        summary = "MPU instruction access violation (executed from a no-execute region)."
    elif "STKOF" in active:
        summary = "Stack overflow (thread stack exhausted)."
    elif "UNDEFINSTR" in active:
        summary = "Undefined instruction (corrupted PC or wrong ISA state)."
    elif "DIVBYZERO" in active:
        summary = "Integer divide by zero."
    elif "UNALIGNED" in active:
        summary = "Unaligned memory access."
    elif cfsr_flags:
        summary = "Configurable fault: " + ", ".join(f["name"] for f in cfsr_flags)
    elif hfsr & (1 << 1):
        summary = "HardFault reading the vector table."
    else:
        summary = "No configurable fault bits set (fault may be a plain HardFault or already cleared)."

    return {
        "cfsr": f"0x{cfsr:08x}",
        "hfsr": f"0x{hfsr:08x}",
        "bfar": f"0x{bfar:08x}",
        "mmfar": f"0x{mmfar:08x}",
        "flags": cfsr_flags + hfsr_flags,
        "fault_address": fault_address,
        "summary": summary,
    }


# RISC-V machine-mode trap cause codes (mcause, when the interrupt bit is clear).
_RISCV_EXCEPTIONS = {
    0: "Instruction address misaligned",
    1: "Instruction access fault",
    2: "Illegal instruction",
    3: "Breakpoint",
    4: "Load address misaligned",
    5: "Load access fault",
    6: "Store/AMO address misaligned",
    7: "Store/AMO access fault",
    8: "Environment call from U-mode",
    9: "Environment call from S-mode",
    11: "Environment call from M-mode",
    12: "Instruction page fault",
    13: "Load page fault",
    15: "Store/AMO page fault",
}

# Codes for which mtval holds a meaningful faulting address.
_RISCV_MTVAL_CODES = {1, 2, 4, 5, 6, 7, 12, 13, 15}


def infer_arch(gdb_binary: str) -> str:
    """Infer the target architecture from the GDB binary name.

    :param gdb_binary: The configured GDB executable name.
    :returns: ``"riscv"`` if the name indicates a RISC-V GDB, else ``"arm"``.
    """
    name = gdb_binary.lower()
    if "riscv" in name or "risc-v" in name or "riscv64" in name:
        return "riscv"
    return "arm"


# ELF e_machine values (offset 0x12 in the header) for the arches we support.
_EM_ARM = 40
_EM_RISCV = 243


def detect_arch_from_elf(elf_path: str) -> Optional[str]:
    """Determine the target architecture from an ELF file's ``e_machine`` field.

    This is the device-agnostic, future-proof way to pick the arch: any new Arm
    or RISC-V Nordic core is classified from its build artifact, with no device
    table and no reliance on the GDB binary name.

    :param elf_path: Path to the ELF (e.g. ``build/zephyr/zephyr.elf``).
    :returns: ``"arm"``, ``"riscv"``, or ``None`` if the file is missing,
        unreadable, or not a recognized ELF.
    """
    try:
        with open(elf_path, "rb") as f:
            header = f.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    little_endian = header[5] != 2  # EI_DATA: 1=LE, 2=BE
    e_machine = int.from_bytes(header[18:20], "little" if little_endian else "big")
    if e_machine == _EM_ARM:
        return "arm"
    if e_machine == _EM_RISCV:
        return "riscv"
    return None


def resolve_arch(arch: Optional[str], elf_path: str, gdb: Optional[str]) -> str:
    """Resolve the target architecture from the strongest available signal.

    Precedence: explicit *arch* > ELF ``e_machine`` > GDB binary name > ``arm``.

    :param arch: Explicit architecture override, or ``None``.
    :param elf_path: ELF path used for ``e_machine`` detection.
    :param gdb: GDB binary name (used only as a fallback hint), or ``None``.
    :returns: ``"arm"`` or ``"riscv"``.
    """
    if arch:
        return arch
    from_elf = detect_arch_from_elf(elf_path)
    if from_elf:
        return from_elf
    if gdb:
        return infer_arch(gdb)
    return "arm"


def decode_riscv_trap(mcause: int, mepc: int, mtval: int) -> dict[str, Any]:
    """Decode RISC-V machine-mode trap CSRs into human-readable form.

    :param mcause: ``mcause`` CSR value (bit 31 = interrupt flag).
    :param mepc: ``mepc`` CSR value (PC at the trap).
    :param mtval: ``mtval`` CSR value (faulting address for access faults).
    :returns: A dict mirroring :func:`decode_fault`: raw values, the decoded
        cause, the resolved fault address (when applicable), and a ``summary``.
    """
    is_interrupt = bool(mcause & (1 << 31))
    code = mcause & 0x7FFFFFFF
    if is_interrupt:
        cause = f"Interrupt (code {code})"
        summary = f"RISC-V interrupt {code} at pc 0x{mepc:08x}."
        fault_address = None
    else:
        cause = _RISCV_EXCEPTIONS.get(code, f"Exception (code {code})")
        fault_address = f"0x{mtval:08x}" if code in _RISCV_MTVAL_CODES else None
        summary = f"RISC-V {cause.lower()} at pc 0x{mepc:08x}"
        summary += f", faulting address 0x{mtval:08x}." if fault_address else "."
    return {
        "arch": "riscv",
        "mcause": f"0x{mcause:08x}",
        "mepc": f"0x{mepc:08x}",
        "mtval": f"0x{mtval:08x}",
        "interrupt": is_interrupt,
        "code": code,
        "cause": cause,
        "fault_address": fault_address,
        "summary": summary,
    }


# Cortex-M exception stack frame: {r0,r1,r2,r3,r12,LR,PC,xPSR}. The stacked
# xPSR always has the Thumb bit set, which anchors the frame in a raw dump.
_XPSR_THUMB_BIT = 1 << 24


def find_exception_pc(words: list[int], resolver: Callable[[int], Optional[str]]) -> Optional[dict[str, Any]]:
    """Locate the faulting PC/LR in a raw Cortex-M exception stack dump.

    GDB frequently cannot unwind through a Cortex-M exception, so this scans a
    window of stacked words for the exception frame: a word with the xPSR Thumb
    bit set whose preceding word (the stacked PC) resolves to a code symbol.

    :param words: Consecutive 32-bit words read from the exception stack pointer.
    :param resolver: Maps an address to a ``func+offset`` symbol, or ``None``.
    :returns: ``{pc, pc_symbol, lr, lr_symbol}`` for the faulting site, or
        ``None`` if no plausible frame was found.
    """
    for i in range(2, len(words)):
        if not (words[i] & _XPSR_THUMB_BIT):
            continue
        pc = words[i - 1]
        pc_symbol = resolver(pc)
        if pc_symbol:
            lr = words[i - 2]
            return {"pc": f"0x{pc:08x}", "pc_symbol": pc_symbol, "lr": f"0x{lr:08x}", "lr_symbol": resolver(lr)}
    return None


# Zephyr thread descriptor, e.g. "worker_blocked_id PENDING PRIO 5" (often inside
# the parentheses of an -thread-info target-id like "Thread 536871800 (…)").
_THREAD_DESC_RE = re.compile(r"([A-Za-z_][\w]*)\s+(.+?)\s+PRIO\s+(-?\d+)")


def parse_thread_descriptor(text: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Extract ``(name, state, prio)`` from a Zephyr thread descriptor string.

    Handles both the bare form (``worker_blocked_id PENDING PRIO 5``) and the
    parenthesized target-id form (``Thread 536871800 (worker_blocked_id PENDING
    PRIO 5)``). Multi-word states such as ``NOT STARTED`` are preserved.

    :param text: The descriptor text from GDB/MI ``-thread-info``.
    :returns: ``(name, state, prio)``; any element is ``None`` if not parseable.
    """
    if not text:
        return (None, None, None)
    inner = text
    if "(" in text and ")" in text:
        inner = text[text.rindex("(") + 1 : text.rindex(")")]
    match = _THREAD_DESC_RE.search(inner)
    if match:
        prio: Optional[int]
        try:
            prio = int(match.group(3))
        except ValueError:
            prio = None
        return (match.group(1), match.group(2).strip(), prio)
    tokens = inner.split()
    return (tokens[0] if tokens else None, None, None)


def parse_svd_field(field_el: "ET.Element") -> dict[str, Any]:
    """Parse one CMSIS-SVD ``<field>`` element into a normalized dict.

    Supports the three SVD bit-position encodings: ``bitRange`` (``[msb:lsb]``),
    ``bitOffset``/``bitWidth``, and separate ``lsb``/``msb``. Also collects any
    ``enumeratedValues``.

    :param field_el: The ``<field>`` XML element.
    :returns: ``{name, lsb, width, description, enums}``.
    """
    lsb: Optional[int] = None
    width: Optional[int] = None
    bit_range = field_el.findtext("bitRange")
    bit_offset = field_el.findtext("bitOffset")
    if bit_range:
        msb_s, lsb_s = bit_range.strip().strip("[]").split(":")
        msb, lsb = int(msb_s), int(lsb_s)
        width = msb - lsb + 1
    elif bit_offset is not None:
        lsb = int(bit_offset, 0)
        bw = field_el.findtext("bitWidth")
        width = int(bw, 0) if bw is not None else 1
    else:
        lsb_t, msb_t = field_el.findtext("lsb"), field_el.findtext("msb")
        if lsb_t is not None and msb_t is not None:
            lsb, width = int(lsb_t, 0), int(msb_t, 0) - int(lsb_t, 0) + 1

    enums: dict[int, str] = {}
    ev = field_el.find("enumeratedValues")
    if ev is not None:
        for e in ev.findall("enumeratedValue"):
            raw = e.findtext("value")
            name = e.findtext("name")
            if raw is not None and name is not None:
                try:
                    enums[int(raw, 0)] = name
                except ValueError:
                    continue

    return {
        "name": field_el.findtext("name"),
        "lsb": lsb if lsb is not None else 0,
        "width": width if width is not None else 1,
        "description": (field_el.findtext("description") or "").strip() or None,
        "enums": enums,
    }


def svd_dim_indices(dim_index: Optional[str], dim: int) -> list[str]:
    """Compute the index labels for an SVD ``<dim>`` register/cluster array.

    :param dim_index: The ``<dimIndex>`` text (``"0-31"``, ``"A,B,C"``), or
        ``None`` to use ``0..dim-1``.
    :param dim: The ``<dim>`` count.
    :returns: A list of ``dim`` index label strings.
    """
    if dim_index:
        text = dim_index.strip()
        if "-" in text:
            start, end = text.split("-", 1)
            try:
                return [str(i) for i in range(int(start), int(end) + 1)]
            except ValueError:
                pass
        elif "," in text:
            return [p.strip() for p in text.split(",")]
    return [str(i) for i in range(dim)]


def _svd_register_name(name: str, index: str) -> str:
    """Substitute an SVD array placeholder (``[%s]`` or ``%s``) with *index*."""
    if "[%s]" in name:
        return name.replace("[%s]", f"[{index}]")
    if "%s" in name:
        return name.replace("%s", index)
    return f"{name}{index}"


def parse_svd(xml_text: str) -> dict[str, dict[str, Any]]:
    """Parse a CMSIS-SVD document into a peripheral/register/field map.

    Resolves ``derivedFrom`` peripherals (which inherit registers and, when
    omitted, the base address of their parent).

    :param xml_text: The SVD file contents.
    :returns: ``{PERIPHERAL: {name, base, registers: {REG: {offset, description,
        fields: [...]}}}}``.
    :raises GdbSessionError: if the XML cannot be parsed.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise GdbSessionError(f"Invalid SVD XML: {e}") from e

    raw: dict[str, ET.Element] = {}
    for p in root.iter("peripheral"):
        name = p.findtext("name")
        if name:
            raw[name] = p

    peripherals: dict[str, dict[str, Any]] = {}
    for name, p in raw.items():
        derived_from = p.get("derivedFrom")
        parent = raw.get(derived_from) if derived_from else None

        base_text = p.findtext("baseAddress") or (parent.findtext("baseAddress") if parent is not None else None)
        base = int(base_text, 0) if base_text else None

        registers_el = p.find("registers")
        if registers_el is None and parent is not None:
            registers_el = parent.find("registers")

        registers: dict[str, dict[str, Any]] = {}
        if registers_el is not None:
            for r in registers_el.findall("register"):
                rname = r.findtext("name")
                if not rname:
                    continue
                offset_text = r.findtext("addressOffset")
                base_offset = int(offset_text, 0) if offset_text else 0
                fields_el = r.find("fields")
                fields = [parse_svd_field(f) for f in fields_el.findall("field")] if fields_el is not None else []
                description = (r.findtext("description") or "").strip() or None

                dim_text = r.findtext("dim")
                if dim_text:
                    # Register array (e.g. PIN_CNF[%s]): expand into dim entries.
                    dim = int(dim_text, 0)
                    inc_text = r.findtext("dimIncrement")
                    increment = int(inc_text, 0) if inc_text else 4
                    for i, index in enumerate(svd_dim_indices(r.findtext("dimIndex"), dim)):
                        registers[_svd_register_name(rname, index)] = {
                            "offset": base_offset + i * increment,
                            "description": description,
                            "fields": fields,
                        }
                else:
                    registers[rname] = {"offset": base_offset, "description": description, "fields": fields}
        peripherals[name] = {"name": name, "base": base, "registers": registers}

    return peripherals


def decode_register(value: int, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode a raw register value into its named SVD fields.

    :param value: The 32-bit register value.
    :param fields: Field descriptors from :func:`parse_svd_field`.
    :returns: A list of ``{name, bits, value, hex, enum?}`` in field order.
    """
    decoded = []
    for f in fields:
        width = f["width"]
        lsb = f["lsb"]
        field_value = (value >> lsb) & ((1 << width) - 1)
        bits = str(lsb) if width == 1 else f"{lsb + width - 1}:{lsb}"
        entry: dict[str, Any] = {"name": f["name"], "bits": bits, "value": field_value, "hex": f"0x{field_value:x}"}
        enum_name = f.get("enums", {}).get(field_value)
        if enum_name:
            entry["enum"] = enum_name
        decoded.append(entry)
    return decoded


def child_env_without_venv() -> dict[str, str]:
    """Return a copy of the environment with any active venv (e.g. uv) removed.

    When this script runs under ``uv run`` (to obtain ``pygdbmi``), uv activates
    an isolated virtual environment via ``VIRTUAL_ENV`` and by prepending its
    ``Scripts``/``bin`` directory to ``PATH``. A child ``west`` invocation would
    then resolve to that venv's Python, which does not contain the ``west``
    package (``ModuleNotFoundError: No module named 'west'``). Stripping the venv
    from the child environment lets ``west`` use the NCS toolchain Python again.
    Native executables (GDB, JLinkGDBServer) are unaffected. ``PYTHONHOME`` and
    ``PYTHONPATH`` are cleared too, since they would otherwise point ``west`` at
    the wrong interpreter. This only sanitizes the child environment; if those
    vars leak into *this* script's interpreter the launcher must clear them
    before ``uv run`` (see ``SKILL.md``).

    :returns: A sanitized copy of ``os.environ``.
    """
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if venv:
        venv_norm = os.path.normcase(os.path.abspath(venv))
        kept = []
        for part in env.get("PATH", "").split(os.pathsep):
            if not part:
                continue
            if os.path.normcase(os.path.abspath(part)).startswith(venv_norm):
                continue
            kept.append(part)
        env["PATH"] = os.pathsep.join(kept)
    return env


def mi_quote(value: str) -> str:
    r"""Quote a value as a GDB/MI c-string argument.

    GDB/MI splits a command line into whitespace-separated tokens; any argument
    that may contain spaces or special characters must be a double-quoted
    c-string with ``\\`` and ``"`` escaped. GDB strips this *transport* quoting
    before the command evaluates the token, so quoting is both safe and required
    for file paths, linespecs, and expressions (e.g. ``arr[i + 1]``). Applying it
    uniformly avoids breakage on paths with spaces or malformed input.

    :param value: The raw argument value.
    :returns: The value as a quoted, escaped MI c-string.
    """
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def is_allowed_gdb_binary(gdb: str) -> bool:
    """Return whether *gdb* is an allowed Zephyr SDK GDB binary (by basename).

    Guards the HTTP ``/connect`` path against launching an arbitrary executable
    via the request's ``gdb`` field. A full path is accepted as long as its
    basename (minus a trailing ``.exe``) is in :data:`ALLOWED_GDB_BINARIES`.

    :param gdb: The requested GDB binary name or path.
    :returns: ``True`` if the basename is allowlisted.
    """
    base = os.path.basename(str(gdb)).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base in ALLOWED_GDB_BINARIES


def _resolve_sysbuild_elf(build_dir: str) -> Optional[str]:
    """Resolve the default image's ELF in a **sysbuild** build tree, if present.

    :param build_dir: Path to the top-level west/CMake build directory.
    :returns: The default image's ELF path, or ``None`` when this is not a
        sysbuild tree (no ``domains.yaml``) or it cannot be parsed.
    """
    domains_file = Path(build_dir) / "domains.yaml"
    if not domains_file.is_file():
        return None
    try:
        lines = domains_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    def _unquote(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
            return value[1:-1]
        return value

    default_name: Optional[str] = None
    domain_build_dirs: dict[str, str] = {}
    pending_name: Optional[str] = None
    for raw in lines:
        stripped = raw.strip()
        # Top-level ``default:`` (no leading indent) names the primary image.
        if not raw[:1].isspace() and stripped.startswith("default:"):
            default_name = _unquote(stripped.split(":", 1)[1]) or None
            continue
        name_match = re.match(r"-?\s*name:\s*(.+)$", stripped)
        if name_match:
            pending_name = _unquote(name_match.group(1))
            continue
        # A ``build_dir:`` following a ``- name:`` belongs to that image; the
        # top-level one precedes any list entry, so pending_name is None there.
        build_match = re.match(r"build_dir:\s*(.+)$", stripped)
        if build_match and pending_name is not None:
            domain_build_dirs[pending_name] = _unquote(build_match.group(1))
            pending_name = None

    if not default_name:
        return None
    image_dir = domain_build_dirs.get(default_name) or str(Path(build_dir) / default_name)
    return str(Path(image_dir) / "zephyr" / "zephyr.elf")


def _resolve_elf(build_dir: str, elf: Optional[str]) -> str:
    """Resolve the ELF path for a build, defaulting to the Zephyr output.

    :param build_dir: Path to the west/CMake build directory.
    :param elf: Explicit ELF path, or ``None`` to derive the default.
    :returns: The resolved ELF path as a string, using forward slashes.
        When *elf* is ``None`` this auto-detects a **sysbuild** layout via
        ``<build_dir>/domains.yaml`` (returning the default image's
        ``<image>/zephyr/zephyr.elf``) and otherwise falls back to the flat
        ``<build_dir>/zephyr/zephyr.elf``. Backslashes are normalized to forward
        slashes because GDB's MI interpreter treats a backslash as an escape
        character (so a raw Windows path such as ``C:/build`` written with
        backslashes would be mangled).
    """
    if elf:
        return elf.replace("\\", "/")
    sysbuild_elf = _resolve_sysbuild_elf(build_dir)
    if sysbuild_elf:
        return sysbuild_elf.replace("\\", "/")
    return str(Path(build_dir) / "zephyr" / "zephyr.elf").replace("\\", "/")


def resolve_coredump_gdbserver(explicit: Optional[str], west_topdir: Optional[str]) -> Optional[str]:
    """Locate Zephyr's ``coredump_gdbserver.py`` script.

    :param explicit: An explicit path to the script, or ``None``.
    :param west_topdir: NCS workspace topdir; the script is expected at
        ``<topdir>/zephyr/scripts/coredump/coredump_gdbserver.py``.
    :returns: The resolved script path, or ``None`` if it cannot be determined.
    """
    if explicit:
        return explicit
    if west_topdir:
        return str(Path(west_topdir) / "zephyr" / "scripts" / "coredump" / "coredump_gdbserver.py")
    return None


# Fallback when no candidate resolves on PATH, so the runner still emits a
# clear "not found" error.
DEFAULT_JLINK_GDBSERVER = "JLinkGDBServerCL"


def resolve_jlink_gdbserver_exe(path: Optional[str] = None) -> str:
    """Return the name of the headless J-Link GDB server available on *path*.

    :param path: ``PATH`` string to search; pass the child environment's
        ``PATH`` so resolution matches what ``west debugserver`` sees.
    :returns: The J-Link GDB server executable name to hand to the runner.
    """
    if os.name == "nt":
        candidates = ("JLinkGDBServerCL", "JLinkGDBServer")
    else:
        candidates = ("JLinkGDBServerCLExe", "JLinkGDBServerCL", "JLinkGDBServer")
    for name in candidates:
        if shutil.which(name, path=path) is not None:
            return name
    return DEFAULT_JLINK_GDBSERVER


def spill_if_large(data: Any, limit_chars: int, spill_dir: str) -> tuple[Any, bool]:
    """Spill an oversized JSON response to a file, returning a small envelope.

    Protects the agent's context window: responses whose serialized length
    exceeds *limit_chars* are written to *spill_dir* and replaced with an
    envelope carrying the path, size, hash, and a short preview. The agent can
    read the file on demand instead of ingesting the whole payload.

    :param data: The JSON-serializable response payload.
    :param limit_chars: Threshold in characters; ``0`` disables spilling.
    :param spill_dir: Directory to write spill files into.
    :returns: ``(payload, spilled)`` — either the original data, or an envelope.
    """
    if limit_chars <= 0:
        return data, False
    body = json.dumps(data)
    if len(body) <= limit_chars:
        return data, False
    try:
        os.makedirs(spill_dir, exist_ok=True)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        path = os.path.join(spill_dir, f"gdb-response-{digest[:12]}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError as e:
        # If we cannot spill, fall back to a truncated preview so we still cap size.
        return {
            "spilled": False,
            "error": f"response too large ({len(body)} chars) and spill failed: {e}",
            "preview": body[:limit_chars],
        }, True
    envelope = {
        "spilled": True,
        "reason": (
            f"Response is {len(body)} chars (limit {limit_chars}); the full JSON was written "
            "to a file to protect context. Read the file only if you need the full payload."
        ),
        "path": path,
        "bytes": len(body),
        "sha256": digest,
        "preview": body[:1000],
    }
    return envelope, True


# --- GDB session ---


def stack_usage_from_fill(raw: bytes, fill_pattern: int = 0xAA, total_size: Optional[int] = None) -> dict[str, Any]:
    """Compute stack usage from a stack region's bytes via the fill pattern.

    Zephyr paints unused stack with a fill byte (``0xAA``) when
    ``CONFIG_INIT_STACKS=y``. The stack grows down from the high end toward
    ``stack_info.start`` (the low address), so the *unused* bytes are the leading
    run of fill bytes from the start of the region. High-water usage is the rest.

    ``raw`` may be only the low portion of a larger stack (a bounded read).
    ``total_size`` is the declared stack size; when it exceeds ``len(raw)`` the
    measurement is *partial*: ``free`` (headroom, the leading fill run) is still
    exact as long as the used/free boundary falls inside ``raw``. If the entire
    read window is fill, the boundary lies beyond it, so only a lower bound on
    ``free`` is known and ``used``/``used_pct`` are reported as ``None``.

    :param raw: The stack region bytes, ordered from ``stack_info.start`` upward.
    :param fill_pattern: The unused-stack fill byte.
    :param total_size: The full stack size; defaults to ``len(raw)`` (no cap).
    :returns: ``{size, used, free, used_pct, fill_seen, partial}``. When no fill
        byte is present (``CONFIG_INIT_STACKS`` likely off), ``used``/``free`` are
        ``None`` and ``fill_seen`` is ``False``.
    """
    scanned = len(raw)
    total = scanned if total_size is None else total_size
    fill = fill_pattern & 0xFF
    if total == 0:
        return {"size": 0, "used": 0, "free": 0, "used_pct": 0.0, "fill_seen": False, "partial": False}
    partial = scanned < total
    free = 0
    for byte in raw:
        if byte != fill:
            break
        free += 1
    fill_seen = free > 0
    if not fill_seen:
        return {"size": total, "used": None, "free": None, "used_pct": None, "fill_seen": False, "partial": partial}
    if partial and free == scanned:
        # The whole read window is fill: the used/free boundary is above it, so
        # free is only a lower bound and usage cannot be computed.
        return {"size": total, "used": None, "free": free, "used_pct": None, "fill_seen": True, "partial": True}
    used = total - free
    return {
        "size": total,
        "used": used,
        "free": free,
        "used_pct": round(100.0 * used / total, 1),
        "fill_seen": True,
        "partial": partial,
    }


def decode_rtt_bytes(raw: bytes) -> str:
    """Decode raw SEGGER RTT bytes into text.

    RTT channel 0 carries the target's console/log output as UTF-8-ish bytes.
    Decode leniently (never raise on a malformed byte) and normalize CRLF so the
    result is clean multi-line text.

    :param raw: Bytes read from the J-Link RTT telnet stream.
    :returns: Decoded, newline-normalized text.
    """
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


class GdbSession:
    """A long-lived MI2 GDB session connected to a J-Link GDB server.

    The session optionally launches ``west debugserver`` (which starts
    JLinkGDBServer) and then drives ``arm-zephyr-eabi-gdb --interpreter=mi2``
    through :mod:`pygdbmi`. All execution/inspection helpers return normalized,
    JSON-serializable data suitable for the HTTP layer.
    """

    def __init__(
        self,
        build_dir: str,
        *,
        elf: Optional[str] = None,
        gdb: Optional[str] = None,
        runner: str = DEFAULT_RUNNER,
        gdb_server_port: int = DEFAULT_GDB_SERVER_PORT,
        use_west: bool = True,
        west_topdir: Optional[str] = None,
        arch: Optional[str] = None,
        svd: Optional[str] = None,
        coredump: Optional[str] = None,
        coredump_gdbserver: Optional[str] = None,
        rtt_host: str = "127.0.0.1",
        rtt_port: int = DEFAULT_RTT_TELNET_PORT,
        core: str = DEFAULT_CORE,
        dev_id: Optional[str] = None,
        gdbserver_exe: Optional[str] = None,
    ) -> None:
        """Initialize session parameters (no processes are started yet).

        :param build_dir: west/CMake build directory used for ``west
            debugserver`` and to locate the ELF file.
        :param elf: Explicit ELF path; defaults to
            ``<build_dir>/zephyr/zephyr.elf``.
        :param gdb: GDB binary to launch (Zephyr SDK ``arm-zephyr-eabi-gdb``).
        :param runner: west runner used for ``west debugserver`` (``jlink``).
        :param gdb_server_port: TCP port the GDB server listens on.
        :param use_west: When ``True`` launch ``west debugserver``; when
            ``False`` assume a GDB server is already listening on the port.
        :param west_topdir: Working directory for ``west debugserver``. Zephyr
            extension commands are workspace-bound, so this must be the NCS
            workspace topdir (e.g. ``C:/ncs/v3.2.0``) when *use_west* is true and
            the server is not started from within the workspace.
        :param arch: Target architecture, ``"arm"`` or ``"riscv"``. Defaults to a
            value inferred from *gdb* (``riscv`` for ``riscv*-gdb``).
        :param svd: Path to a CMSIS-SVD file used by ``/peripheral`` to decode
            peripheral registers by name. Not hardcoded per device — supply the
            SVD for the target (Nordic SVDs ship in the NCS MDK folder).
        :param coredump: Path to a Zephyr coredump ``.bin`` for **post-mortem**
            (offline) debugging. When set, the session drives GDB against
            ``coredump_gdbserver.py`` instead of a live J-Link target.
        :param coredump_gdbserver: Explicit path to ``coredump_gdbserver.py``;
            defaults to ``<west_topdir>/zephyr/scripts/coredump/``.
        :param gdbserver_exe: Explicit J-Link GDB server executable for the
            ``jlink`` runner; ``None`` auto-resolves the headless build per
            platform at launch (see :func:`resolve_jlink_gdbserver_exe`).
        """
        self.build_dir = build_dir
        self.core = core
        self.elf = _resolve_elf(build_dir, elf)
        self.runner = runner
        self.use_west = use_west
        self.west_topdir = west_topdir
        self.coredump = coredump
        self.coredump_gdbserver = resolve_coredump_gdbserver(coredump_gdbserver, west_topdir)
        # A coredump is served by coredump_gdbserver.py on its fixed port 1234.
        self.gdb_server_port = COREDUMP_GDB_PORT if coredump else gdb_server_port
        # Resolve arch (explicit > ELF e_machine > GDB name > arm), then pick the
        # matching GDB binary if one was not explicitly provided. This makes new
        # Arm/RISC-V Nordic cores work with no device table and no --arch.
        self.arch = resolve_arch(arch, self.elf, gdb)
        self.gdb = gdb or (DEFAULT_RISCV_GDB if self.arch == "riscv" else DEFAULT_GDB_BINARY)
        self.svd_path = svd
        self._svd_cache: Optional[dict[str, dict[str, Any]]] = None
        # SEGGER RTT: JLinkGDBServer streams channel 0 on a telnet port (19021).
        self.rtt_host = rtt_host
        self.rtt_port = rtt_port
        # J-Link probe serial. Needed to bind the right probe when two GDB
        # servers (e.g. app + net core) share one physical J-Link.
        self.dev_id = dev_id
        # Headless J-Link GDB server executable (avoids the GUI's blocking
        # modals). ``None`` auto-resolves the per-platform name at launch.
        self.gdbserver_exe = gdbserver_exe
        self.debugserver_startup_s = 5.0
        # First target attach can be slow (multi-core CoreSight bring-up, post-recover).
        self.connect_timeout_s = 30.0
        # Overall time to wait for an async ``*stopped`` after an execution command.
        self.exec_timeout_s = 20.0

        self._controller: Any = None
        self._debugserver: Optional[subprocess.Popen[bytes]] = None
        self._debugserver_log: Optional[str] = None
        self.connected = False
        self.attached = False
        self.last_stop: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None

    # -- lifecycle --

    def _start_debugserver(self) -> None:
        """Launch ``west debugserver`` as a background subprocess.

        :raises GdbSessionError: if the ``west`` executable is not on PATH.
        """
        if shutil.which("west") is None:
            raise GdbSessionError("`west` not found on PATH. Is the NCS environment activated?")
        # Build the child environment first so the GDB server name resolves
        # against the same PATH the spawned `west debugserver` sees.
        clean_env = child_env_without_venv()
        cmd = ["west", "debugserver", "--runner", self.runner, "--build-dir", self.build_dir]
        # --dev-id binds a specific probe; essential when two GDB servers (app +
        # net) run against the same physical J-Link.
        if self.dev_id:
            cmd += ["--dev-id", str(self.dev_id)]
        if self.runner == "jlink":
            # Pass per-core GDB and RTT ports so a second core can run its own
            # JLinkGDBServer on distinct ports (multi-core), and force the
            # headless server (the GUI variant pops blocking modal dialogs).
            # Auto-detect the per-platform name unless the caller pinned it.
            gdbserver_exe = self.gdbserver_exe or resolve_jlink_gdbserver_exe(clean_env.get("PATH"))
            runner_opts = ["--gdb-port", str(self.gdb_server_port), "--rtt-port", str(self.rtt_port)]
            if gdbserver_exe:
                runner_opts += ["--gdbserver", gdbserver_exe]
            cmd += ["--", *runner_opts]
        # Route output to a log file (NOT an undrained PIPE, which would deadlock
        # once JLinkGDBServer fills the OS pipe buffer). The log also lets us
        # explain a startup failure.
        self._debugserver_log = str(Path(self.build_dir) / "gdb_debugserver.log")
        log = open(self._debugserver_log, "wb")
        self._debugserver = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=clean_env,
            cwd=self.west_topdir,
            # POSIX: put west in its own session so terminating the process group
            # also stops the JLinkGDBServer child (Windows uses taskkill /T).
            start_new_session=sys.platform != "win32",
        )
        # Give JLinkGDBServer time to bind the port before GDB connects.
        time.sleep(self.debugserver_startup_s)
        # Fail fast with a useful message if the debugserver already died (a very
        # common case is SEGGER's JLinkGDBServer not being on PATH).
        if self._debugserver.poll() is not None:
            tail = ""
            try:
                with open(self._debugserver_log, encoding="utf-8", errors="replace") as f:
                    tail = "".join(f.readlines()[-8:]).strip()
            except OSError:
                pass
            raise GdbSessionError(
                "`west debugserver` exited before the GDB server came up. This is "
                "usually because SEGGER JLinkGDBServer is not installed or not on "
                f"PATH. Details:\n{tail}"
            )

    def _start_coredump_gdbserver(self) -> None:
        """Launch Zephyr's ``coredump_gdbserver.py`` for offline post-mortem.

        :raises GdbSessionError: if the script or coredump cannot be located.
        """
        if not self.coredump_gdbserver:
            raise GdbSessionError(
                "coredump_gdbserver.py not found. Pass --coredump-gdbserver <path> or "
                "--west-topdir so it can be located at <topdir>/zephyr/scripts/coredump/."
            )
        if not os.path.isfile(self.coredump_gdbserver):
            raise GdbSessionError(f"coredump_gdbserver.py not found at {self.coredump_gdbserver!r}.")
        assert self.coredump is not None
        if not os.path.isfile(self.coredump):
            raise GdbSessionError(f"Coredump file not found at {self.coredump!r}.")
        # Argument order is <elf> <coredump.bin>; it listens on port 1234.
        # Run with a PATH Python (the NCS toolchain Python has pyelftools, which
        # the script needs) rather than sys.executable — under `uv run` the
        # latter is an isolated interpreter that only has this script's deps.
        clean_env = child_env_without_venv()
        clean_path = clean_env.get("PATH")
        python = shutil.which("python", path=clean_path) or shutil.which("python3", path=clean_path) or sys.executable
        cmd = [python, self.coredump_gdbserver, self.elf, self.coredump]
        self._debugserver_log = str(Path(self.build_dir) / "coredump_gdbserver.log")
        try:
            log = open(self._debugserver_log, "wb")
        except OSError:
            log = subprocess.DEVNULL  # type: ignore[assignment]
        self._debugserver = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=clean_env,
            start_new_session=sys.platform != "win32",
        )
        time.sleep(2.0)
        if self._debugserver.poll() is not None:
            tail = ""
            try:
                with open(self._debugserver_log, encoding="utf-8", errors="replace") as f:
                    tail = "".join(f.readlines()[-8:]).strip()
            except OSError:
                pass
            raise GdbSessionError(f"coredump_gdbserver.py exited before binding. Details:\n{tail}")

    def connect(self, *, do_load: bool = False, attach: bool = False) -> dict[str, Any]:
        """Start GDB, connect to the target, load symbols, and optionally flash.

        :param do_load: When ``True`` download the ELF to the target (flash)
            after connecting. Ignored when *attach* is ``True``.
        :param attach: When ``True`` attach to a running core without resetting
            or flashing (equivalent to ``west attach``).
        :returns: A status dict describing the resulting session state.
        :raises GdbSessionError: if pygdbmi is unavailable or GDB reports an
            error while connecting.
        """
        if GdbController is None:
            raise GdbSessionError(
                "pygdbmi is not installed. Run this script via `uv run` so the PEP 723 "
                "dependency is resolved automatically."
            )
        if self.coredump:
            self._start_coredump_gdbserver()
        elif self.use_west:
            self._start_debugserver()

        self._controller = GdbController(command=[self.gdb, "--interpreter=mi2"])
        # A coredump is a read-only post-mortem snapshot: treat it as attached.
        self.attached = attach or bool(self.coredump)

        # Configure GDB for programmatic control:
        # - mi-async on: GDB keeps accepting commands (e.g. -exec-interrupt)
        #   while the target runs, and delivers `*stopped` asynchronously.
        #   Without it, GDB blocks after -exec-continue until the target halts.
        # - pagination/confirm off: never wait for interactive input, which
        #   would otherwise hang the MI channel.
        self.write("-gdb-set pagination off")
        self.write("-gdb-set confirm off")
        self.write("-gdb-set mi-async on")

        # Load symbols so breakpoints and backtraces resolve to source.
        raise_on_error(self.write(f"-file-exec-and-symbols {mi_quote(self.elf)}"))
        # Connect to the GDB server (J-Link, or coredump_gdbserver.py for post-mortem).
        # The first J-Link attach can be slow (CoreSight bring-up on multi-core
        # SoCs like the nRF5340, especially right after a recover), so allow more
        # time than the default per-command MI timeout.
        raise_on_error(
            self.write(
                f"-target-select remote localhost:{self.gdb_server_port}",
                timeout_sec=self.connect_timeout_s,
            )
        )

        # A coredump target is already halted at the crash; no reset/flash/monitor.
        if not attach and not self.coredump:
            self.console("monitor reset halt", timeout_sec=self.connect_timeout_s)
            if do_load:
                raise_on_error(self.write("-target-download", timeout_sec=self.connect_timeout_s))

        self.connected = True
        return self.status()

    def write(self, command: str, timeout_sec: float = MI_TIMEOUT_SEC) -> list[dict[str, Any]]:
        """Send a raw MI command and return the parsed response records.

        Also records the most recent ``*stopped`` async event, if present.

        :param command: The MI command string (e.g. ``-exec-continue``).
        :param timeout_sec: Maximum time to wait for GDB output.
        :returns: The list of pygdbmi response dicts.
        :raises GdbSessionError: if the session has no active controller.
        """
        if self._controller is None:
            raise GdbSessionError("GDB session is not connected.")
        responses = self._controller.write(command, timeout_sec=timeout_sec)
        stop = extract_stop_event(responses)
        if stop is not None:
            self.last_stop = stop
        return responses

    def console(self, command: str, timeout_sec: float = MI_TIMEOUT_SEC) -> list[dict[str, Any]]:
        """Run a CLI (console) command through the MI ``interpreter-exec``.

        :param command: The GDB CLI command (e.g. ``monitor reset halt``).
        :param timeout_sec: Maximum time to wait for GDB output.
        :returns: The list of pygdbmi response dicts.
        """
        return self.write(f"-interpreter-exec console {mi_quote(command)}", timeout_sec)

    def disconnect(self) -> None:
        """Detach GDB and terminate the GDB server subprocess, if any."""
        if self._controller is not None:
            try:
                self._controller.exit()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._controller = None
        if self._debugserver is not None:
            self._terminate_debugserver_tree(self._debugserver)
            self._debugserver = None
        self.connected = False

    @staticmethod
    def _terminate_debugserver_tree(proc: "subprocess.Popen[bytes]") -> None:
        """Terminate ``west debugserver`` **and its JLinkGDBServer child**.

        ``west`` spawns ``JLinkGDBServerCL`` as a child; terminating only the
        ``west`` process can leave that child running and holding the probe (and
        its TCP port). Kill the whole tree/group so the probe is released.
        """
        if sys.platform == "win32":
            # Windows: taskkill /T terminates the whole process tree.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
                proc.wait(timeout=5.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
            return

        # POSIX: west was started in its own session (start_new_session), so kill
        # the whole process group with SIGTERM, then SIGKILL if it does not exit.
        # This stops the JLinkGDBServer child too, which proc.terminate() alone
        # (only the west process) would leave running and holding the probe.
        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = None
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=5.0)
        except (subprocess.TimeoutExpired, OSError):
            try:
                if pgid is not None:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.kill()
            except OSError:
                pass

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current session state.

        :returns: A JSON-serializable status dict.
        """
        return {
            "connected": self.connected,
            "attached": self.attached,
            "core": self.core,
            "build_dir": self.build_dir,
            "elf": self.elf,
            "gdb": self.gdb,
            "arch": self.arch,
            "runner": self.runner,
            "gdb_server_port": self.gdb_server_port,
            "rtt_port": self.rtt_port,
            "dev_id": self.dev_id,
            "svd": self.svd_path,
            "mode": "coredump" if self.coredump else "live",
            "coredump": self.coredump,
            "last_stop": summarize_stop(self.last_stop),
            "error": self.error,
        }

    # -- execution control --

    def _wait_for_stop(self, overall_timeout: float) -> Optional[dict[str, Any]]:
        """Poll GDB for an asynchronous ``*stopped`` record after resuming.

        MI execution commands reply ``^running`` immediately; the inferior's
        actual halt (breakpoint, step complete, signal) arrives later as an
        out-of-band ``*stopped`` notification. This drains GDB output until such
        a record appears or *overall_timeout* elapses.

        :param overall_timeout: Maximum seconds to wait for the halt.
        :returns: The stop-event payload, or ``None`` if still running.
        """
        deadline = time.time() + overall_timeout
        while time.time() < deadline:
            try:
                responses = self._controller.get_gdb_response(timeout_sec=0.5, raise_error_on_timeout=False)
            except Exception:  # noqa: BLE001 - transient read timeout, keep polling
                responses = []
            stop = extract_stop_event(responses)
            if stop is not None:
                self.last_stop = stop
                return stop
        return None

    def _exec(self, mi_command: str, *, expect_async_stop: bool = False) -> dict[str, Any]:
        """Run an execution MI command and summarize the resulting stop.

        :param mi_command: An ``-exec-*`` MI command.
        :param expect_async_stop: Wait for a ``*stopped`` even when the result
            class is not ``running``. GDB documents ``^done`` and ``^running`` as
            equivalent, and ``-exec-interrupt`` replies ``^done`` before the halt
            arrives asynchronously, so waiting only on ``status == "running"``
            could return before the target actually stops.
        :returns: A dict with the MI status and a stop-event summary.
        """
        responses = self.write(mi_command)
        raise_on_error(responses)
        status = result_status(responses)
        stop = extract_stop_event(responses)
        # ``^running`` resumes; ``-exec-interrupt`` returns ``^done`` then halts.
        # In both cases the ``*stopped`` is delivered asynchronously.
        if stop is None and (status == "running" or expect_async_stop):
            stop = self._wait_for_stop(self.exec_timeout_s)
        if stop is not None:
            reason = str(stop.get("reason", ""))
            if reason.startswith("signal") or reason == "exited-signalled":
                actions = ["GET /crash-report for one-shot fault triage", "GET /backtrace", "GET /registers"]
            else:
                actions = ["GET /backtrace", "GET /locals", "GET /analyze"]
        else:
            # Resumed but never halted within the timeout: the target is still
            # running. A breakpoint that was expected but not hit means the code
            # path was NOT reached.
            actions = [
                "POST /interrupt to halt the core, then inspect where it is",
                "if a breakpoint was expected but not hit, the path was NOT reached: "
                "GET /breakpoints to check hit counts, or set an earlier breakpoint",
            ]
        return {
            "status": status,
            "stopped": stop is not None,
            "stop": summarize_stop(stop if stop is not None else self.last_stop),
            "stop_event": stop,
            "suggested_next_actions": actions,
        }

    def run(self) -> dict[str, Any]:
        """Start the inferior from the beginning (``-exec-run``)."""
        return self._exec("-exec-run")

    def cont(self) -> dict[str, Any]:
        """Resume execution (``-exec-continue``)."""
        return self._exec("-exec-continue")

    def interrupt(self) -> dict[str, Any]:
        """Halt a running inferior (``-exec-interrupt``).

        ``-exec-interrupt`` returns ``^done`` and the halt arrives later as an
        async ``*stopped``, so wait for that stop rather than the result class.
        """
        return self._exec("-exec-interrupt", expect_async_stop=True)

    def resume_nowait(self) -> dict[str, Any]:
        """Resume the target and return immediately (no wait for a stop).

        Backs the device ``/go`` endpoint when a live session exists: it resumes
        through GDB/MI so GDB's own view of the target becomes *running* (keeping
        MI state coherent with the hardware), without blocking on an async stop
        that may never arrive (e.g. resuming so RTT can stream).
        """
        responses = self.write("-exec-continue")
        raise_on_error(responses)
        # -exec-continue yields ^running and no *stopped; clear the stale stop so
        # status reflects that the core is now running.
        self.last_stop = None
        return {"status": result_status(responses), "running": True, "stopped": False}

    def step(self) -> dict[str, Any]:
        """Step one source line, into calls (``-exec-step``)."""
        return self._exec("-exec-step")

    def next(self) -> dict[str, Any]:
        """Step one source line, over calls (``-exec-next``)."""
        return self._exec("-exec-next")

    def stepi(self) -> dict[str, Any]:
        """Step one machine instruction (``-exec-step-instruction``)."""
        return self._exec("-exec-step-instruction")

    def finish(self) -> dict[str, Any]:
        """Run until the current function returns (``-exec-finish``)."""
        return self._exec("-exec-finish")

    def until(self, location: Optional[str]) -> dict[str, Any]:
        """Run until a source location is reached (``-exec-until``).

        :param location: A ``file:line`` or function target; ``None`` runs
            until the current frame reaches a line past the current one.
        """
        cmd = "-exec-until" if not location else f"-exec-until {mi_quote(location)}"
        return self._exec(cmd)

    # -- breakpoints / watchpoints --

    def set_breakpoint(
        self, location: str, *, condition: Optional[str] = None, temporary: bool = False
    ) -> dict[str, Any]:
        """Insert a breakpoint (``-break-insert``).

        :param location: ``file:line``, function name, or ``*address``.
        :param condition: Optional condition expression.
        :param temporary: When ``True`` create a one-shot (temporary) breakpoint.
        :returns: The inserted breakpoint description.
        """
        args = ["-break-insert"]
        if temporary:
            args.append("-t")
        if condition:
            args.append(f"-c {mi_quote(condition)}")
        args.append(mi_quote(location))
        responses = self.write(" ".join(args))
        raise_on_error(responses)
        return parse_break_insert(responses)

    def set_watchpoint(self, expression: str, *, mode: str = "write") -> dict[str, Any]:
        """Insert a watchpoint (``-break-watch``).

        :param expression: The variable or memory expression to watch.
        :param mode: ``write`` (default), ``read`` (``-r``), or ``access``
            (``-a``). Hardware watchpoints are limited by the target's debug
            comparators.
        :returns: The MI result payload for the watchpoint.
        """
        flag = {"write": "", "read": "-r", "access": "-a"}.get(mode, "")
        parts = ["-break-watch", *([flag] if flag else []), mi_quote(expression)]
        responses = self.write(" ".join(parts))
        raise_on_error(responses)
        return _result_payload(responses)

    def list_breakpoints(self) -> list[dict[str, Any]]:
        """Return all breakpoints/watchpoints (``-break-list``)."""
        responses = self.write("-break-list")
        raise_on_error(responses)
        return parse_break_list(responses)

    def delete_breakpoint(self, number: int) -> None:
        """Delete a breakpoint by number (``-break-delete``)."""
        raise_on_error(self.write(f"-break-delete {number}"))

    def set_breakpoint_enabled(self, number: int, enabled: bool) -> None:
        """Enable or disable a breakpoint by number.

        :param number: The breakpoint number.
        :param enabled: ``True`` to enable (``-break-enable``), ``False`` to
            disable (``-break-disable``).
        """
        verb = "-break-enable" if enabled else "-break-disable"
        raise_on_error(self.write(f"{verb} {number}"))

    # -- inspection --

    def backtrace(self) -> list[dict[str, Any]]:
        """Return the current call stack (``-stack-list-frames``)."""
        responses = self.write("-stack-list-frames")
        raise_on_error(responses)
        return parse_stack_frames(responses)

    def select_frame(self, level: int) -> None:
        """Select a stack frame by level (``-stack-select-frame``)."""
        raise_on_error(self.write(f"-stack-select-frame {level}"))

    def locals(self) -> list[dict[str, Any]]:
        """Return locals/args of the current frame (``-stack-list-variables``)."""
        responses = self.write("-stack-list-variables --all-values")
        raise_on_error(responses)
        return parse_variables(responses)

    def evaluate(self, expression: str) -> Optional[str]:
        """Evaluate an expression (``-data-evaluate-expression``).

        :param expression: The C expression to evaluate.
        :returns: The value as a string, or ``None``.
        """
        responses = self.write(f"-data-evaluate-expression {mi_quote(expression)}")
        raise_on_error(responses)
        return parse_evaluate(responses)

    def set_variable(self, expression: str) -> None:
        """Assign to a variable or memory location.

        :param expression: A full assignment expression, e.g. ``my_var=0x1``.
        """
        escaped = expression.replace('"', '\\"')
        raise_on_error(self.write(f"-gdb-set var {escaped}"))

    def registers(self) -> dict[str, str]:
        """Return named CPU register values.

        :returns: A dict mapping register name to its string value.
        """
        names = parse_register_names(self.write("-data-list-register-names"))
        values = parse_register_values(self.write("-data-list-register-values x"))
        return name_registers(values, names)

    def read_memory(self, address: str, count: int) -> list[dict[str, Any]]:
        """Read raw memory (``-data-read-memory-bytes``).

        :param address: Start address (expression or hex literal).
        :param count: Number of bytes to read.
        :returns: A list of memory-region dicts.
        """
        responses = self.write(f"-data-read-memory-bytes {mi_quote(address)} {count}")
        raise_on_error(responses)
        return parse_memory(responses)

    def read_bytes(self, address: int, count: int) -> bytes:
        """Read *count* bytes starting at *address* and return them as ``bytes``.

        Wraps :meth:`read_memory` and decodes the MI hex ``contents`` fields into
        a contiguous byte string ordered by ascending address.

        :param address: Absolute start address.
        :param count: Number of bytes to read.
        :returns: The raw bytes (may be shorter than *count* if a region is
            unreadable).
        """
        regions = self.read_memory(f"0x{address:08x}", count)
        ordered = sorted(regions, key=lambda m: int(str(m.get("begin", "0")), 0))
        out = bytearray()
        for region in ordered:
            contents = region.get("contents")
            if isinstance(contents, str):
                try:
                    out += bytes.fromhex(contents)
                except ValueError:
                    continue
        return bytes(out)

    def stack_usage(self) -> dict[str, Any]:
        """Report per-thread stack high-water usage (proactive overflow check).

        Walks the Zephyr kernel thread list (``_kernel.threads`` /
        ``next_thread``) and, for each thread with ``stack_info``, reads the stack
        region and measures the fill-pattern high-water mark. This catches threads
        that are *close* to overflow before they fault.

        Requires ``CONFIG_THREAD_STACK_INFO=y`` (stack bounds),
        ``CONFIG_THREAD_MONITOR=y`` (thread list traversal) and, for usage
        numbers, ``CONFIG_INIT_STACKS=y`` (fill pattern). Degrades gracefully:
        threads without a readable fill pattern report ``used=None``.

        :returns: ``{threads: [{name, start, size, used, free, used_pct,
            fill_seen}], notes?}``.
        :raises GdbSessionError: if the kernel thread list is not introspectable.
        """
        head = self._eval_u32("(unsigned int)&_kernel ? (unsigned int)_kernel.threads : 0")
        if not head:
            raise GdbSessionError(
                "Cannot walk the Zephyr thread list. Build with CONFIG_THREAD_MONITOR=y "
                "(thread list) and CONFIG_THREAD_STACK_INFO=y (stack bounds); "
                "CONFIG_INIT_STACKS=y adds the fill pattern used to measure high-water usage."
            )
        threads: list[dict[str, Any]] = []
        ptr = head
        seen: set[int] = set()
        while ptr and ptr not in seen and len(seen) < 64:
            seen.add(ptr)
            tcb = f"((struct k_thread *)0x{ptr:08x})"
            start = self._eval_u32(f"(unsigned int){tcb}->stack_info.start")
            size = self._eval_u32(f"(unsigned int){tcb}->stack_info.size")
            name = self._thread_name(tcb, ptr)
            entry: dict[str, Any] = {"name": name, "start": f"0x{start:08x}", "size": size}
            if start and size:
                # Bound the read for pathological stacks; total_size lets the
                # helper report accurate usage (or mark the result partial when
                # the stack is larger than the scan window).
                scan = min(size, STACK_SCAN_CAP)
                usage = stack_usage_from_fill(self.read_bytes(start, scan), total_size=size)
                entry.update(usage)
            else:
                entry.update({"used": None, "free": None, "used_pct": None, "fill_seen": False, "partial": False})
            threads.append(entry)
            ptr = self._eval_u32(f"(unsigned int){tcb}->next_thread")

        result: dict[str, Any] = {"threads": threads}
        notes = []
        if any(t.get("fill_seen") is False for t in threads):
            notes.append(
                "Threads with fill_seen=false have no stack fill pattern; build with "
                "CONFIG_INIT_STACKS=y to measure high-water usage."
            )
        if any(t.get("partial") for t in threads):
            notes.append(
                f"Threads with partial=true exceed the {STACK_SCAN_CAP}-byte scan window; "
                "'free' is a lower bound and 'used'/'used_pct' are unavailable."
            )
        if notes:
            result["notes"] = " ".join(notes)
        near = [t["name"] for t in threads if isinstance(t.get("used_pct"), (int, float)) and t["used_pct"] >= 90.0]
        if near:
            result["suggested_next_actions"] = [
                f"Threads near stack exhaustion (>=90%): {', '.join(str(n) for n in near)}. "
                "Increase the relevant stack size (e.g. CONFIG_MAIN_STACK_SIZE, the K_THREAD_STACK "
                "size, or CONFIG_ISR_STACK_SIZE)."
            ]
        return result

    def _thread_name(self, tcb_expr: str, ptr: int) -> str:
        """Best-effort thread name from a ``k_thread`` pointer expression.

        Falls back to the TCB address when the name cannot be read (e.g. the
        build has no ``CONFIG_THREAD_NAME`` so ``k_thread`` has no ``name``
        member, which makes the evaluate raise).
        """
        try:
            raw = self.evaluate(f"{tcb_expr}->name")
        except GdbSessionError:
            return f"0x{ptr:08x}"
        if raw:
            match = re.search(r'"([^"]*)"', raw)
            if match and match.group(1):
                return match.group(1)
        return f"0x{ptr:08x}"

    def rtt(self, seconds: float = 1.0, channel: int = 0) -> dict[str, Any]:
        """Capture SEGGER RTT output from the J-Link GDB server.

        JLinkGDBServer auto-detects the ``_SEGGER_RTT`` control block and streams
        channel 0 (the standard console/log channel) on a telnet port (default
        19021). This connects to that port and reads for *seconds*.

        RTT flows only while the **core is running** and after the application has
        initialized the RTT control block, so a halted or freshly-reset target
        yields no bytes — resume with ``/go`` or ``/continue`` first.

        :param seconds: How long to read from the RTT stream.
        :param channel: RTT up-channel index (0 = console/log; the J-Link telnet
            port carries channel 0).
        :returns: ``{channel, port, bytes, text, suggested_next_actions?}``.
        :raises GdbSessionError: for a coredump (no live probe) session, or if the
            RTT telnet port cannot be reached.
        """
        if self.coredump:
            raise GdbSessionError("RTT is a live-probe feature; it is not available in coredump mode.")
        if channel != 0:
            # The J-Link GDB server telnet port carries only up-channel 0
            # (console/log). Reject other channels rather than silently returning
            # channel-0 data mislabeled as another channel.
            raise GdbSessionError(
                f"RTT channel {channel} is not supported: the J-Link GDB server exposes only "
                "channel 0 (console/log) on its telnet port. Request channel 0."
            )
        import socket  # noqa: PLC0415 - only needed for the RTT path

        deadline = time.time() + max(0.1, float(seconds))
        buf = bytearray()
        try:
            with socket.create_connection((self.rtt_host, self.rtt_port), timeout=3.0) as sock:
                sock.settimeout(0.3)
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            raise GdbSessionError(
                f"Could not connect to the J-Link RTT telnet port {self.rtt_host}:{self.rtt_port} "
                f"({e}). The port opens once JLinkGDBServer locates the RTT control block, which "
                "requires the app to be running and built with RTT (e.g. CONFIG_USE_SEGGER_RTT / "
                "CONFIG_RTT_CONSOLE or the RTT log backend)."
            ) from e
        text = decode_rtt_bytes(bytes(buf))
        result: dict[str, Any] = {
            "channel": channel,
            "port": self.rtt_port,
            "bytes": len(buf),
            "text": text,
        }
        if not buf:
            result["suggested_next_actions"] = [
                "No RTT bytes captured. RTT streams only while the core runs — POST /go (or /continue) and retry.",
                "Confirm the firmware enables RTT (CONFIG_USE_SEGGER_RTT=y with CONFIG_RTT_CONSOLE=y "
                "or CONFIG_LOG_BACKEND_RTT=y).",
            ]
        return result

    def monitor(self, command: str) -> dict[str, Any]:
        """Send a ``monitor`` (remote/JLink) command such as ``reset halt``.

        Useful for embedded control that is not part of core GDB, e.g.
        ``reset halt`` to re-hold the CPU at the reset vector so a breakpoint
        placed on an early function (like ``main``) can be caught on the next
        ``continue``.

        :param command: The monitor subcommand (without the ``monitor`` prefix).
        :returns: A dict with the MI status and any console text returned.
        """
        responses = self.console(f"monitor {command}")
        raise_on_error(responses)
        text = "".join(str(r.get("payload", "")) for r in responses if r.get("type") == "console")
        return {"status": result_status(responses), "output": text}

    def _eval_u32(self, expression: str) -> int:
        """Evaluate an expression and coerce its result to a 32-bit int.

        :param expression: A GDB expression (memory deref, ``$register``, ...).
        :returns: The value, or 0 if it could not be read/parsed.
        """
        value = self.evaluate(expression)
        if value is None:
            return 0
        try:
            return int(value.strip().split()[0], 0) & 0xFFFFFFFF
        except (ValueError, IndexError):
            return 0

    def _read_u32(self, address: int) -> int:
        """Read a 32-bit word from a memory-mapped address via GDB.

        :param address: The absolute address to read.
        :returns: The 32-bit value, or 0 if it could not be read.
        """
        return self._eval_u32(f"*(unsigned int *)0x{address:08x}")

    def resolve_symbol(self, address: int) -> Optional[str]:
        """Resolve an address to a ``func+offset`` symbol via GDB, if any.

        :param address: The address to resolve.
        :returns: The symbol text inside GDB's ``<...>`` annotation (e.g.
            ``sensor_write+4``), or ``None`` when the address is not in a symbol.
        """
        if not address:
            return None
        value = self.evaluate(f"(void(*)())0x{address:08x}")
        if value and "<" in value and ">" in value:
            return value[value.index("<") + 1 : value.index(">")]
        return None

    def fault(self) -> dict[str, Any]:
        """Read and decode the target's fault status, per architecture.

        The target must be halted (typically at the Zephyr fault handler, e.g. a
        breakpoint on ``z_arm_fault``/``k_sys_fatal_error_handler``). For Arm
        this decodes the Cortex-M SCB fault registers; for RISC-V it decodes the
        machine-mode trap CSRs (``mcause``/``mepc``/``mtval``). Combines the
        decoded fault with the current backtrace.

        :returns: The decoded fault (:func:`decode_fault` /
            :func:`decode_riscv_trap`) plus a ``backtrace``.
        """
        if self.arch == "riscv":
            decoded = decode_riscv_trap(self._eval_u32("$mcause"), self._eval_u32("$mepc"), self._eval_u32("$mtval"))
        else:
            decoded = decode_fault(
                self._read_u32(SCB_CFSR),
                self._read_u32(SCB_HFSR),
                self._read_u32(SCB_BFAR),
                self._read_u32(SCB_MMFAR),
            )
        decoded["backtrace"] = [
            {"level": f.get("level"), "func": f.get("func"), "file": f.get("file"), "line": f.get("line")}
            for f in self.backtrace()
        ]
        return decoded

    def recover_exception_context(self) -> Optional[dict[str, Any]]:
        """Recover the faulting PC/LR from the Cortex-M exception stack frame.

        GDB usually cannot unwind through the exception, so this reads a window
        of the exception stack (PSP for a thread, MSP for ISR context) and finds
        the stacked frame (see :func:`find_exception_pc`).

        :returns: ``{pc, pc_symbol, lr, lr_symbol}`` or ``None``. Arm-only.
        """
        if self.arch != "arm":
            return None
        for sp_expr in ("$psp", "$msp", "$sp"):
            base = self._eval_u32(sp_expr)
            if not base:
                continue
            words = [self._read_u32(base + 4 * i) for i in range(24)]
            ctx = find_exception_pc(words, self.resolve_symbol)
            if ctx:
                return ctx
        return None

    def crash_report(self) -> dict[str, Any]:
        """One-shot crash triage: halt, decode the fault, and locate the site.

        Interrupts a running target, decodes the fault (arch-aware), recovers
        the faulting instruction (exception frame on Arm, ``mepc`` on RISC-V),
        resolves it to a symbol, and composes a plain-language summary.

        :returns: ``{fault, fault_site?, summary}``.
        """
        try:
            self.interrupt()
        except GdbSessionError:
            pass

        fault = self.fault()
        report: dict[str, Any] = {"fault": fault}

        site: Optional[dict[str, Any]] = None
        if self.arch == "arm":
            site = self.recover_exception_context()
        else:
            mepc = fault.get("mepc")
            if mepc:
                pc = int(mepc, 0)
                site = {"pc": mepc, "pc_symbol": self.resolve_symbol(pc)}
        if site:
            report["fault_site"] = site

        # Stack overflow is a distinct, common class: surface it explicitly and
        # point at the offending thread so the fix (bigger stack) is obvious.
        fault_flags = {f.get("name") for f in fault.get("flags", [])}
        if "STKOF" in fault_flags:
            report["stack_overflow"] = True
            offender = self._current_thread_name()
            report["suggested_next_actions"] = [
                (f"Stack overflow in thread '{offender}'. " if offender else "Stack overflow. ")
                + "Increase that thread's stack size (CONFIG_MAIN_STACK_SIZE, the K_THREAD_STACK "
                "size, or CONFIG_ISR_STACK_SIZE) and check GET /stack-usage for high-water marks.",
            ]

        parts = [str(fault.get("summary", "")).rstrip(".")]
        if report.get("stack_overflow") and (offender := self._current_thread_name()):
            parts.append(f"overflowing thread '{offender}'")
        if site and site.get("pc_symbol"):
            parts.append(f"faulting instruction at {site['pc_symbol']}")
        if site and site.get("lr_symbol"):
            parts.append(f"called from {site['lr_symbol']}")
        report["summary"] = ". ".join(p for p in parts if p) + "."
        return report

    def _current_thread_name(self) -> Optional[str]:
        """Best-effort name of the currently-running Zephyr thread."""
        try:
            cur = self._eval_u32("(unsigned int)k_current_get()")
        except GdbSessionError:
            return None
        if not cur:
            return None
        name = self._thread_name(f"((struct k_thread *)0x{cur:08x})", cur)
        return name if not name.startswith("0x") else None

    def _load_svd(self) -> dict[str, dict[str, Any]]:
        """Load and cache the configured CMSIS-SVD peripheral map.

        :returns: The parsed peripheral map (see :func:`parse_svd`).
        :raises GdbSessionError: if no SVD is configured or it cannot be read.
        """
        if self._svd_cache is not None:
            return self._svd_cache
        if not self.svd_path:
            raise GdbSessionError(
                'No SVD file configured. Pass --svd <path> (serve) or {"svd": ...} to /connect. '
                "Nordic SVD files ship in the NCS MDK folder "
                "(modules/hal/nordic/nrfx/mdk/*.svd); resolve the exact file for the SoC via "
                "nordicsemi_search_sources."
            )
        try:
            with open(self.svd_path, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise GdbSessionError(f"Cannot read SVD file {self.svd_path!r}: {e}") from e
        self._svd_cache = parse_svd(text)
        return self._svd_cache

    def peripheral(self, name: str, register: Optional[str] = None) -> dict[str, Any]:
        """Read and field-decode peripheral registers using the SVD.

        :param name: Peripheral name (e.g. ``GPIO0``), case-insensitive.
        :param register: Optional single register name; otherwise all registers
            of the peripheral are read.
        :returns: ``{peripheral, base, registers: [{register, address, value,
            fields}]}``.
        :raises GdbSessionError: if the peripheral/register is unknown or unreadable.
        """
        svd = self._load_svd()
        periph = svd.get(name) or svd.get(name.upper())
        if periph is None:
            raise GdbSessionError(f"Peripheral {name!r} not found in SVD.")
        if periph["base"] is None:
            raise GdbSessionError(f"Peripheral {name!r} has no base address in SVD.")

        registers = periph["registers"]
        if register:
            reg = registers.get(register) or registers.get(register.upper())
            if reg is None:
                raise GdbSessionError(f"Register {register!r} not found in peripheral {name!r}.")
            selected = [(register, reg)]
        else:
            selected = list(registers.items())

        results = []
        for rname, reg in selected:
            addr = periph["base"] + reg["offset"]
            value = self._read_u32(addr)
            results.append({
                "register": rname,
                "address": f"0x{addr:08x}",
                "value": f"0x{value:08x}",
                "description": reg["description"],
                "fields": decode_register(value, reg["fields"]),
            })
        return {"peripheral": periph["name"], "base": f"0x{periph['base']:08x}", "registers": results}

    def _thread_backtrace(self, thread_id: str) -> list[dict[str, Any]]:
        """Select a thread and return its backtrace frames."""
        self.write(f"-thread-select {thread_id}")
        responses = self.write("-stack-list-frames")
        return parse_stack_frames(responses)

    def threads(self, with_backtrace: bool = False) -> dict[str, Any]:
        """List RTOS threads (name, state, frame) via GDB's Zephyr awareness.

        Relies on the J-Link runner's ``RTOSPlugin_Zephyr`` (loaded automatically
        by ``west debugserver``) plus ``CONFIG_DEBUG_THREAD_INFO=y``. No manual
        stack reconstruction is needed — GDB enumerates threads itself.

        :param with_backtrace: When ``True`` also collect each thread's backtrace
            (selects each thread in turn, then restores the current thread).
        :returns: ``{current, threads: [{id, name, state, prio, frame, current,
            backtrace?}]}``.
        """
        responses = self.write("-thread-info")
        raise_on_error(responses)
        payload = _result_payload(responses)
        current = payload.get("current-thread-id")
        raw = payload.get("threads", [])

        threads: list[dict[str, Any]] = []
        for th in raw if isinstance(raw, list) else []:
            if not isinstance(th, dict):
                continue
            tid = th.get("id")
            descriptor = th.get("name") or th.get("details") or th.get("target-id") or ""
            name, state, prio = parse_thread_descriptor(descriptor)
            frame_raw = th.get("frame")
            frame = frame_raw if isinstance(frame_raw, dict) else {}
            threads.append({
                "id": tid,
                "name": name or th.get("name"),
                "state": state,
                "prio": prio,
                "target_id": th.get("target-id"),
                "frame": {
                    "func": frame.get("func"),
                    "file": frame.get("file"),
                    "line": frame.get("line"),
                    "addr": frame.get("addr"),
                },
                "current": tid == current,
            })

        if with_backtrace:
            for entry in threads:
                if entry["id"]:
                    entry["backtrace"] = self._thread_backtrace(str(entry["id"]))
            if current:
                self.write(f"-thread-select {current}")

        return {"current": current, "threads": threads}

    def analyze(self) -> dict[str, Any]:
        """Collect a structured crash/state analysis at the current stop.

        Halts the inferior if it is running, then gathers the backtrace, the
        locals of the innermost frame, and the fault-relevant core registers.

        :returns: The structured analysis from :func:`summarize_crash`.
        """
        # Best-effort halt so we can inspect state.
        try:
            self.interrupt()
        except GdbSessionError:
            pass
        frames = self.backtrace()
        try:
            self.select_frame(0)
        except GdbSessionError:
            pass
        variables = self.locals()
        registers = self.registers()
        return summarize_crash(self.last_stop, frames, variables, registers)


# --- nrfutil device operations ---


def nrfutil_device(args: list[str]) -> dict[str, Any]:
    """Run an ``nrfutil device`` subcommand and return a structured result.

    :param args: Arguments following ``nrfutil device`` (e.g.
        ``["list", "--traits", "jlink"]``).
    :returns: A dict with ``returncode``, ``stdout``, and ``stderr``. When the
        output is JSON (``--json`` requests) the parsed value is under
        ``json``.
    :raises GdbSessionError: if the ``nrfutil`` executable is not on PATH.
    """
    if shutil.which("nrfutil") is None:
        raise GdbSessionError("`nrfutil` not found on PATH.")
    proc = subprocess.run(
        ["nrfutil", "device", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    stripped = proc.stdout.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            result["json"] = json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return result


def nrfutil_ok(result: dict[str, Any]) -> bool:
    """Return whether an :func:`nrfutil_device` result reported success.

    :param result: The dict returned by :func:`nrfutil_device`.
    :returns: ``True`` when ``returncode`` is 0.
    """
    return result.get("returncode", 0) == 0


def _json_bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    """Read a strict JSON boolean from a request body.

    Rejects truthy strings such as ``"false"`` (which would otherwise enable a
    destructive or target-changing operation by accident). A missing key returns
    *default*; a present non-boolean raises :class:`ValueError` (mapped to 400).

    :param data: The parsed request body.
    :param key: The field to read.
    :param default: Value returned when *key* is absent.
    :returns: The boolean value.
    :raises ValueError: if *key* is present but not a JSON boolean.
    """
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be a JSON boolean (true/false), not {type(value).__name__}")
    return value


def _is_windows() -> bool:
    """Return whether Windows storage and permission semantics apply.

    :returns: ``True`` when the script is running on Windows.
    """
    return os.name == "nt"


def _runtime_dir() -> Path:
    """Return the private runtime directory used for GDB server records.

    :returns: The private per-user runtime directory.
    :raises RuntimeError: If a safe runtime directory cannot be created or verified.
    """
    if _is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        directory = base / "Nordic Semiconductor" / "Nordic MCP" / "runtime" / "gdb"
    else:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        user_id = str(os.getuid())
        base = Path(xdg_runtime) if xdg_runtime else Path(tempfile.gettempdir()) / f"gdb-{user_id}"
        directory = base / "nordic-gdb" if xdg_runtime else base
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
    except OSError as exc:
        raise RuntimeError("Cannot create the private GDB runtime directory") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("The GDB runtime path is not a directory")
    if not _is_windows():
        if info.st_uid != os.getuid():
            raise RuntimeError("The GDB runtime directory is not owned by the current user")
        try:
            os.chmod(directory, 0o700)
            info = directory.lstat()
        except OSError as exc:
            raise RuntimeError("Cannot secure the private GDB runtime directory") from exc
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("The GDB runtime directory permissions are unsafe")
    return directory


def _state_path(port: int) -> Path:
    """Return the state-file path for a server port.

    :param port: TCP port identifying the server record.
    :returns: The expected state-file path.
    :raises ValueError: If *port* is outside the TCP port range.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer from 1 to 65535")
    return _runtime_dir() / f"{STATE_FILE_PREFIX}{port}.json"


def _write_state(record: dict[str, Any]) -> Path:
    """Atomically persist a private server state record.

    :param record: State record containing process and authentication details.
    :returns: The final state-file path.
    :raises RuntimeError: If the record cannot be written safely.
    """
    path = _state_path(record["port"])
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("The GDB server state is not a regular file")
        if not _is_windows():
            if info.st_uid != os.getuid():
                raise RuntimeError("The GDB server state is not owned by the current user")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError("The GDB server state permissions are unsafe")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    payload = json.dumps(record, separators=(",", ":")).encode("utf-8")
    fd = -1
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if not _is_windows():
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except OSError:
            pass
        raise RuntimeError("Cannot write the private GDB server state") from exc
    return path


def _read_state(port: int) -> dict[str, Any]:
    """Read and validate a private server state record.

    :param port: TCP port identifying the record.
    :returns: The validated state record.
    :raises RuntimeError: If the record is absent, unsafe, or malformed.
    """
    path = _state_path(port)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("The GDB server state is not a regular file")
        if not _is_windows():
            if info.st_uid != os.getuid():
                raise RuntimeError("The GDB server state is not owned by the current user")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError("The GDB server state permissions are unsafe")
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"No GDB server state found for port {port}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The GDB server state is unreadable") from exc
    if not isinstance(record, dict):
        raise RuntimeError("The GDB server state is malformed")
    required = {"host": str, "port": int, "started_at": str, "token": str}
    if any(
        isinstance(record.get(key), bool) or not isinstance(record.get(key), kind) for key, kind in required.items()
    ):
        raise RuntimeError("The GDB server state is malformed")
    if record["port"] != port or not record["token"]:
        raise RuntimeError("The GDB server state is malformed")
    return record


def _is_loopback(host: str) -> bool:
    """Determine whether a bind host resolves exclusively to loopback.

    :param host: Host name or IP address.
    :returns: ``True`` only when every resolved address is loopback.
    """
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
        return bool(addresses) and all(ipaddress.ip_address(address).is_loopback for address in addresses)
    except (socket.gaierror, ValueError):
        return False


def _endpoint_url(host: str, port: int, path: str) -> str:
    """Build a client URL for a bound server address.

    :param host: Bound server host.
    :param port: Bound server TCP port.
    :param path: Absolute endpoint path.
    :returns: An HTTP URL suitable for a local client connection.
    """
    connect_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    return f"http://{connect_host}:{port}{path}"


def _request(url: str, token: str, method: str = "GET", data: Optional[bytes] = None) -> Any:
    """Perform an authenticated request to a GDB server.

    :param url: Endpoint URL without authentication material.
    :param token: Process-scoped authentication token.
    :param method: HTTP method.
    :param data: Optional encoded JSON request body.
    :returns: The decoded JSON response.
    :raises urllib.error.URLError: If the server cannot be reached.
    :raises ValueError: If the response is not valid JSON.
    """
    headers = {TOKEN_HEADER: token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _state_is_live(record: dict[str, Any]) -> bool:
    """Verify a state record using an authenticated status probe.

    A GDB HTTP server is live even when no debug session is attached.

    :param record: Validated state record.
    :returns: ``True`` when authenticated server status responds.
    """
    try:
        result = _request(_endpoint_url(record["host"], record["port"], "/status"), record["token"])
    except (urllib.error.URLError, ValueError, TimeoutError):
        return False
    return isinstance(result, dict) and isinstance(result.get("connected"), bool)


def _remove_state_if_owned(path: Path, token: str) -> None:
    """Remove a state record only if it still belongs to the expected server.

    :param path: State-file path to remove.
    :param token: Expected process token.
    :returns: None
    """
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return
        if not _is_windows() and info.st_uid != os.getuid():
            return
        record = json.loads(path.read_text(encoding="utf-8"))
        stored_token = str(record.get("token", "")).encode("utf-8")
        if hmac.compare_digest(stored_token, token.encode("utf-8")):
            path.unlink()
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError):
        return


# --- HTTP server ---

# Multi-core: sessions are held in a registry keyed by core name (e.g. "app",
# "net"). A single-core session lives under its core name ("app" by default),
# so the common one-core path is unchanged. Requests select a core via a
# ``core`` query param (GET) or body field (POST); with one session, selection
# is implicit.
server_sessions: dict[str, "GdbSession"] = {}
http_server: Optional[Any] = None
server_response_limit: int = DEFAULT_RESPONSE_LIMIT_CHARS
server_spill_dir: str = os.path.join(tempfile.gettempdir(), "gdb_mcp_spill")
# Destructive device operations (/erase, /recover) are refused unless the human
# operator explicitly enabled them at server launch (--allow-destructive). A
# request-body "confirm" flag alone is NOT sufficient — an autonomous agent
# could set it, so the gate must be out-of-band from the agent.
server_allow_destructive: bool = False
server_token: Optional[str] = None


def resolve_session(core: Optional[str]) -> Optional["GdbSession"]:
    """Return the session for *core*, or the implicit one when unambiguous.

    :param core: Explicit core name, or ``None`` to auto-select.
    :returns: The matching :class:`GdbSession`, or ``None`` if there is no such
        session (or the choice is ambiguous with no default core present).
    """
    if core:
        return server_sessions.get(core)
    if len(server_sessions) == 1:
        return next(iter(server_sessions.values()))
    return server_sessions.get(DEFAULT_CORE)


# Imported lazily so the pure helpers above stay importable in minimal envs.
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class GdbRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler exposing GDB control/inspection endpoints as JSON."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - match BaseHTTPRequestHandler
        """Silence default request logging so credentials can never be logged.

        :param format: Default handler log format, unused.
        :param args: Default handler log arguments, unused.
        :returns: None
        """

    def _authenticated(self) -> bool:
        """Authenticate the request using the process-scoped token.

        :returns: ``True`` when the supplied header matches the server token.
        """
        supplied = self.headers.get(TOKEN_HEADER)
        if supplied is None:
            self.send_json({"error": "Authentication required"}, 401)
            return False
        token_matches = server_token is not None and hmac.compare_digest(
            supplied.encode("utf-8"),
            server_token.encode("utf-8"),
        )
        if not token_matches:
            self.send_json({"error": "Authentication failed"}, 403)
            return False
        return True

    def send_json(self, data: Any, status: int = 200) -> None:
        """Serialize *data* as JSON and write it as the response body.

        :param data: JSON-serializable payload to send.
        :param status: HTTP status code to respond with.
        :returns: None
        """
        # Token-aware output: spill oversized successful responses to a file so a
        # single large read cannot blow the agent's context window.
        if status == 200:
            data, _ = spill_if_large(data, server_response_limit, server_spill_dir)
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_gdb_error(self, e: "GdbSessionError") -> None:
        """Send a GDB error (409), enriched with a next-step hint when useful.

        The most common recoverable case is reading state while the core is
        running ("Selected thread is running.") — guide the agent to halt first.
        """
        msg = str(e)
        payload: dict[str, Any] = {"error": msg}
        if "running" in msg.lower():
            payload["suggested_next_actions"] = [
                "POST /interrupt to halt the core, then retry — memory/registers/"
                "backtrace/threads reads require a halted core",
            ]
        self.send_json(payload, 409)

    # -- GET --

    def do_GET(self) -> None:
        """Dispatch authenticated ``GET`` requests to inspection/status endpoints.

        :returns: None
        """
        if not self._authenticated():
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/status":
            core = qs.get("core", [None])[0]
            session = resolve_session(core)
            if session is None:
                self.send_json({"connected": False, "cores": sorted(server_sessions)})
            else:
                status = session.status()
                if len(server_sessions) > 1:
                    status["cores"] = sorted(server_sessions)
                self.send_json(status)
            return

        if parsed.path == "/sessions":
            self.send_json({
                "cores": sorted(server_sessions),
                "sessions": {name: s.status() for name, s in server_sessions.items()},
            })
            return

        if parsed.path == "/devices":
            try:
                self.send_json(nrfutil_device(["list", "--traits", "jlink"]))
            except GdbSessionError as e:
                self.send_json({"error": str(e)}, 503)
            return

        session = resolve_session(qs.get("core", [None])[0])
        if session is None or not session.connected:
            self.send_json(self._no_session_error(qs.get("core", [None])[0]), 503)
            return

        try:
            self._dispatch_get(session, parsed.path, qs)
        except GdbTimeoutError as e:
            # GdbTimeoutError subclasses ValueError, so it must be caught first;
            # a GDB hang is a gateway timeout, not a bad client request.
            self.send_json({"error": f"GDB timed out: {e}"}, 504)
        except GdbSessionError as e:
            self.send_gdb_error(e)
        except (ValueError, KeyError) as e:
            self.send_json({"error": f"Invalid request: {e}"}, 400)
        except Exception as e:  # noqa: BLE001 - never close the connection without JSON
            self.send_json({"error": f"Unexpected server error: {e}"}, 503)

    def _no_session_error(self, core: Optional[str]) -> dict[str, Any]:
        """Build the 503 body when no session matches the (optional) core."""
        if core and server_sessions:
            return {
                "error": f"No session for core {core!r}. Connected cores: {sorted(server_sessions)}.",
            }
        return {"error": "No active GDB session. POST /connect first."}

    def _dispatch_get(self, session: "GdbSession", path: str, qs: dict[str, list[str]]) -> None:
        """Route an inspection ``GET`` request to the matching session method."""
        if path == "/backtrace":
            self.send_json({"frames": session.backtrace()})
        elif path == "/locals":
            self.send_json({"variables": session.locals()})
        elif path == "/registers":
            self.send_json({"registers": session.registers()})
        elif path == "/breakpoints":
            self.send_json({"breakpoints": session.list_breakpoints()})
        elif path == "/stop-event":
            self.send_json({
                "stop": summarize_stop(session.last_stop),
                "stop_event": session.last_stop,
            })
        elif path == "/analyze":
            self.send_json(session.analyze())
        elif path == "/fault":
            self.send_json(session.fault())
        elif path == "/crash-report":
            self.send_json(session.crash_report())
        elif path == "/threads":
            with_bt = qs.get("backtrace", ["false"])[0].lower() == "true"
            self.send_json(session.threads(with_backtrace=with_bt))
        elif path == "/peripheral":
            name = qs.get("peripheral", [""])[0]
            if not name:
                raise ValueError("'peripheral' query parameter is required")
            register = qs.get("register", [None])[0]
            self.send_json(session.peripheral(name, register))
        elif path == "/memory":
            address = qs.get("addr", [""])[0]
            if not address:
                raise ValueError("'addr' query parameter is required")
            # Clamp the length: an unbounded read can stall the probe (slow SWD)
            # or produce a huge payload.
            count = max(1, min(int(qs.get("len", ["16"])[0]), MAX_MEMORY_READ_BYTES))
            self.send_json({"memory": session.read_memory(address, count)})
        elif path == "/rtt":
            seconds = min(30.0, max(0.1, float(qs.get("seconds", ["1"])[0])))
            channel = int(qs.get("channel", ["0"])[0])
            self.send_json(session.rtt(seconds=seconds, channel=channel))
        elif path == "/stack-usage":
            self.send_json(session.stack_usage())
        else:
            self.send_json({"error": "Not found"}, 404)

    # -- POST --

    def do_POST(self) -> None:
        """Authenticate and dispatch a JSON ``POST`` request.

        :returns: None
        """
        if not self._authenticated():
            self.close_connection = True
            return
        parsed = urlparse(self.path)
        # Parse Content-Length defensively and cap the body: a malformed header
        # must not raise, and a huge body must not exhaust memory.
        try:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self.close_connection = True
            self.send_json({"error": "Invalid Content-Length header"}, 400)
            return
        if content_length < 0:
            self.close_connection = True
            self.send_json({"error": "Invalid Content-Length header"}, 400)
            return
        if content_length > MAX_REQUEST_BODY_BYTES:
            self.close_connection = True
            self.send_json({"error": f"Request body too large (> {MAX_REQUEST_BODY_BYTES} bytes)"}, 413)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self.close_connection = True
            self.send_json({"error": "Content-Type must be application/json"}, 415)
            return
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"error": "Malformed JSON request body"}, 400)
            return
        if not isinstance(data, dict):
            self.send_json({"error": "Request body must be a JSON object"}, 400)
            return

        try:
            self._dispatch_post(parsed.path, data)
        except GdbTimeoutError as e:
            # GdbTimeoutError subclasses ValueError, so it must be caught first;
            # a GDB hang is a gateway timeout, not a bad client request.
            self.send_json({"error": f"GDB timed out: {e}"}, 504)
        except GdbSessionError as e:
            self.send_gdb_error(e)
        except (TypeError, ValueError, KeyError) as e:
            self.send_json({"error": f"Invalid request: {e}"}, 400)
        except Exception:  # noqa: BLE001 - preserve a JSON response at the HTTP boundary
            self.send_json({"error": "Unexpected server error"}, 503)

    def _dispatch_post(self, path: str, data: dict[str, Any]) -> None:
        """Route a validated ``POST`` body to the matching handler."""
        if path == "/connect":
            self._handle_connect(data)
            return
        if path == "/shutdown":
            self.send_json({"status": "stopping"})
            if http_server is not None:
                threading.Thread(target=http_server.shutdown, daemon=True).start()
            return

        # Device operations do not require an attached GDB session.
        if path in ("/reset", "/halt", "/go", "/erase", "/recover"):
            self._handle_device_op(path, data)
            return

        core = data.get("core")
        session = resolve_session(core if isinstance(core, str) else None)
        if session is None or not session.connected:
            self.send_json(self._no_session_error(core if isinstance(core, str) else None), 503)
            return

        self._dispatch_session_post(session, path, data)

    def _handle_connect(self, data: dict[str, Any]) -> None:
        """Create and connect a new :class:`GdbSession` from request fields.

        Multiple cores can be connected concurrently by giving each a distinct
        ``core`` name and its own ``gdb_server_port`` (each core is served by its
        own GDB server, e.g. a second ``west debugserver`` for the network core).
        """
        # Guard against launching an arbitrary executable via the request. The
        # GDB binary is normally chosen by arch auto-detection; if the request
        # overrides it, restrict to the known Zephyr SDK GDB binaries.
        requested_gdb = data.get("gdb")
        if requested_gdb is not None and not is_allowed_gdb_binary(str(requested_gdb)):
            self.send_json(
                {
                    "error": (
                        f"gdb {requested_gdb!r} is not allowed over HTTP. Omit 'gdb' to "
                        f"auto-select, or use one of {sorted(ALLOWED_GDB_BINARIES)}."
                    )
                },
                400,
            )
            return

        core = str(data.get("core", DEFAULT_CORE))
        existing = server_sessions.get(core)
        if existing is not None and existing.connected:
            self.send_json(
                {"error": f"Core {core!r} is already connected. POST /disconnect (core={core}) first."},
                409,
            )
            return
        build_dir = data.get("build_dir", "build")
        session = GdbSession(
            build_dir=build_dir,
            elf=data.get("elf"),
            gdb=data.get("gdb"),  # None -> resolved from arch / ELF e_machine
            runner=data.get("runner", DEFAULT_RUNNER),
            gdb_server_port=int(data.get("gdb_server_port", DEFAULT_GDB_SERVER_PORT)),
            use_west=bool(data.get("use_west", True)),
            west_topdir=data.get("west_topdir"),
            arch=data.get("arch"),  # None -> auto-detected from the ELF
            svd=data.get("svd"),
            coredump=data.get("coredump"),
            coredump_gdbserver=data.get("coredump_gdbserver"),
            rtt_port=int(data.get("rtt_port", DEFAULT_RTT_TELNET_PORT)),
            core=core,
            dev_id=data.get("dev_id"),
            gdbserver_exe=data.get("gdbserver_exe"),
        )
        try:
            status = session.connect(
                do_load=_json_bool(data, "load"),
                attach=_json_bool(data, "attach"),
            )
        except Exception:
            # Connect may have already spawned the GDB server / taken the probe
            # lock before failing. Tear it down so the probe is freed and no
            # orphaned JLinkGDBServer lingers, then surface the original error.
            session.disconnect()
            raise
        server_sessions[core] = session
        self.send_json(status)

    def _dispatch_session_post(self, session: "GdbSession", path: str, data: dict[str, Any]) -> None:
        """Route execution/breakpoint ``POST`` requests to the resolved session."""
        if path == "/disconnect":
            session.disconnect()
            server_sessions.pop(session.core, None)
            self.send_json({"status": "disconnected", "core": session.core})
        elif path == "/run":
            self.send_json(session.run())
        elif path == "/continue":
            self.send_json(session.cont())
        elif path == "/interrupt":
            self.send_json(session.interrupt())
        elif path == "/step":
            self.send_json(session.step())
        elif path == "/next":
            self.send_json(session.next())
        elif path == "/stepi":
            self.send_json(session.stepi())
        elif path == "/finish":
            self.send_json(session.finish())
        elif path == "/until":
            self.send_json(session.until(data.get("location")))
        elif path == "/monitor":
            command = data.get("command")
            if not command:
                raise ValueError("'command' is required")
            self.send_json(session.monitor(str(command)))
        elif path == "/breakpoints":
            location = data.get("location")
            if not location:
                raise ValueError("'location' is required")
            self.send_json(
                session.set_breakpoint(
                    str(location),
                    condition=data.get("condition"),
                    temporary=bool(data.get("temporary", False)),
                )
            )
        elif path == "/breakpoints/delete":
            session.delete_breakpoint(int(data["number"]))
            self.send_json({"status": "deleted", "number": data["number"]})
        elif path == "/breakpoints/enable":
            session.set_breakpoint_enabled(int(data["number"]), True)
            self.send_json({"status": "enabled", "number": data["number"]})
        elif path == "/breakpoints/disable":
            session.set_breakpoint_enabled(int(data["number"]), False)
            self.send_json({"status": "disabled", "number": data["number"]})
        elif path == "/watchpoints":
            expression = data.get("expression")
            if not expression:
                raise ValueError("'expression' is required")
            self.send_json(session.set_watchpoint(str(expression), mode=data.get("mode", "write")))
        elif path == "/frame":
            session.select_frame(int(data["level"]))
            self.send_json({"status": "ok", "level": data["level"]})
        elif path == "/evaluate":
            expression = data.get("expression")
            if not expression:
                raise ValueError("'expression' is required")
            self.send_json({"value": session.evaluate(str(expression))})
        elif path == "/variable":
            expression = data.get("expression")
            if not expression:
                raise ValueError("'expression' is required")
            session.set_variable(str(expression))
            self.send_json({"status": "ok"})
        else:
            self.send_json({"error": "Not found"}, 404)

    def _send_nrfutil_result(self, result: dict[str, Any]) -> None:
        """Send an nrfutil result, mapping a nonzero exit to HTTP 500.

        A failed device operation must not be reported as success; return 500
        with the structured result (stdout/stderr preserved) so the agent (and a
        scripting client) can detect the failure.
        """
        self.send_json(result, 200 if nrfutil_ok(result) else 500)

    def _handle_device_op(self, path: str, data: dict[str, Any]) -> None:
        """Route device operations, bound to the selected session's target.

        Halt/go are routed through the session's GDB/MI when one is connected, so
        GDB's execution state stays coherent with the hardware and the operation
        cannot hit a different probe than the one being debugged. Other
        operations default their probe to the session's ``dev_id`` and refuse to
        guess when a session is active but its probe is unknown.
        """
        core = data.get("core")
        session = resolve_session(core if isinstance(core, str) else None)

        # Prefer GDB/MI for execution control while a live session exists: this
        # keeps the MI stop-state coherent and targets the debugged core.
        if path == "/halt" and session is not None and session.connected:
            self.send_json(session.interrupt())
            return
        if path == "/go" and session is not None and session.connected:
            self.send_json(session.resume_nowait())
            return

        # Fall back to nrfutil, defaulting to the session's probe. Refuse to
        # operate blindly on a possibly-different target when a session is active
        # but we cannot identify its probe.
        serial = data.get("serial_number") or (session.dev_id if session is not None else None)
        if serial is None and session is not None and session.connected:
            self.send_json(
                {
                    "error": (
                        "An active debug session's probe is unknown, so this device operation "
                        "could hit a different target. Provide 'serial_number' explicitly, or "
                        "reconnect the session with 'dev_id' set."
                    )
                },
                409,
            )
            return
        base: list[str] = ["--serial-number", str(serial)] if serial else []

        if path == "/reset":
            args = ["reset", *base]
            reset_kind = data.get("reset_kind")
            warning = None
            if reset_kind:
                # RESET_KINDS lists the kinds we know today; do NOT hard-reject
                # others — new devices may add kinds. Pass through and let
                # nrfutil validate, surfacing a heads-up for unknown values.
                if reset_kind not in RESET_KINDS:
                    warning = (
                        f"reset_kind {reset_kind!r} is not in the known set "
                        f"{sorted(RESET_KINDS)}; passing through to nrfutil."
                    )
                args += ["--reset-kind", str(reset_kind)]
            result = nrfutil_device(args)
            if warning:
                result["warning"] = warning
            # A device reset out from under an attached session invalidates GDB's
            # view; flag it so the agent reconnects rather than trusting stale state.
            if session is not None and session.connected:
                result["warning"] = (
                    (result.get("warning", "") + " ") if result.get("warning") else ""
                ) + "Reset while a session is attached: GDB state is now stale, POST /disconnect and reconnect."
            self._send_nrfutil_result(result)
        elif path == "/halt":
            self._send_nrfutil_result(nrfutil_device(["halt", *base]))
        elif path == "/go":
            args = ["go", *base]
            if core:
                args += ["--core", str(core)]
            self._send_nrfutil_result(nrfutil_device(args))
        elif path in ("/erase", "/recover"):
            # Destructive: an agent-settable "confirm" flag is NOT a human gate,
            # so require the operator to have enabled destructive ops out-of-band
            # at server launch (--allow-destructive) AND a confirm in the request.
            if not server_allow_destructive:
                self.send_json(
                    {
                        "error": (
                            f"{path} is destructive and disabled. The operator must start the "
                            "server with --allow-destructive to permit erase/recover; a request "
                            "'confirm' flag alone is not sufficient."
                        )
                    },
                    403,
                )
                return
            if _json_bool(data, "confirm") is not True:
                self.send_json(
                    {
                        "error": (
                            f"{path} is destructive and requires explicit user confirmation. "
                            'Re-send with {"confirm": true} only after the user approves.'
                        )
                    },
                    409,
                )
                return
            op = path.lstrip("/")
            self._send_nrfutil_result(nrfutil_device([op, *base]))
        else:
            self.send_json({"error": "Not found"}, 404)


# --- CLI ---


def cmd_serve(args: argparse.Namespace) -> None:
    """Run the HTTP control server, optionally auto-connecting a session.

    :param args: Parsed CLI arguments.
    :returns: None
    """
    global http_server, server_response_limit, server_allow_destructive, server_token

    if not _is_loopback(args.host):
        if not args.allow_non_loopback:
            print("Error: non-loopback binding requires --allow-non-loopback", file=sys.stderr)
            raise SystemExit(2)
        print(
            "WARNING: GDB is binding to a non-loopback address; network clients can reach the authenticated API.",
            file=sys.stderr,
        )

    state_path = _state_path(args.port_num)
    try:
        previous = _read_state(args.port_num) if state_path.exists() else None
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if previous is not None:
        if _state_is_live(previous):
            print(f"Error: a GDB server is already active on port {args.port_num}", file=sys.stderr)
            raise SystemExit(1)
        _remove_state_if_owned(state_path, previous["token"])
        if state_path.exists():
            print("Error: stale GDB state could not be removed safely", file=sys.stderr)
            raise SystemExit(1)

    server_response_limit = args.max_response_chars
    server_allow_destructive = args.allow_destructive
    server_token = secrets.token_urlsafe(32)

    # Bind the HTTP server BEFORE acquiring the probe. If the bind fails (e.g.
    # the port is already in use) we must not have launched GDB/J-Link yet, and
    # the whole startup runs under one try/finally so any later failure still
    # releases every session's probe.
    http_server = HTTPServer((args.host, args.port_num), GdbRequestHandler)
    record_path: Optional[Path] = None

    def sigterm_handler(_signum: int, _frame: Any) -> None:
        """Shut down the HTTP server gracefully on SIGTERM.

        :param _signum: Signal number received, unused.
        :param _frame: Current stack frame, unused.
        :returns: None
        """
        if http_server is not None:
            threading.Thread(target=http_server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        record_path = _write_state({
            "host": args.host,
            "port": args.port_num,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "token": server_token,
        })
        if args.autostart:
            session = GdbSession(
                build_dir=args.build_dir,
                elf=args.elf,
                gdb=args.gdb,  # None -> resolved from arch / ELF e_machine
                runner=args.runner,
                gdb_server_port=args.gdb_server_port,
                use_west=not args.no_west,
                west_topdir=args.west_topdir,
                arch=args.arch,
                svd=args.svd,
                coredump=args.coredump,
                coredump_gdbserver=args.coredump_gdbserver,
                rtt_port=args.rtt_port,
                core=args.core,
                dev_id=args.dev_id,
                gdbserver_exe=args.gdbserver_exe,
            )
            server_sessions[session.core] = session
            try:
                session.connect(do_load=args.load, attach=args.attach)
                print("Autoconnected GDB session.")
            except Exception as e:  # noqa: BLE001 - keep the server up so /status reports the error
                print(f"Error connecting session: {e}")
                session.error = str(e)
                # Release the probe (kill the debugserver) since the session is unusable.
                session.disconnect()

        # Readiness token consumed by the skill (grep -m1 "ready").
        print(f"GDB HTTP server ready on {args.host}:{args.port_num}", flush=True)

        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        for session in list(server_sessions.values()):
            session.disconnect()
        server_sessions.clear()
        http_server.server_close()
        if record_path is not None and server_token is not None:
            _remove_state_if_owned(record_path, server_token)


def _explicit_or_state_token(args: argparse.Namespace) -> tuple[str, str, int]:
    """Resolve CLI credentials from explicit input or verified server state.

    :param args: Parsed CLI arguments.
    :returns: A tuple containing token, host, and port.
    :raises RuntimeError: If credentials are unavailable, unsafe, or stale.
    """
    explicit = getattr(args, "token", None)
    token_file = getattr(args, "token_file", None)
    if explicit and token_file:
        raise RuntimeError("Use only one of --token and --token-file")
    if token_file:
        try:
            explicit = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Cannot read the token file") from exc
        if not explicit:
            raise RuntimeError("The token file is empty")
    if explicit:
        return explicit, args.host, args.port_num
    record = _read_state(args.port_num)
    if not _state_is_live(record):
        raise RuntimeError(f"Stale GDB server state found for port {args.port_num}")
    return record["token"], record["host"], record["port"]


def cmd_token(args: argparse.Namespace) -> None:
    """Print credentials for a verified active GDB server.

    :param args: Parsed CLI arguments.
    :returns: None
    """
    try:
        record = _read_state(args.port_num)
        if not _state_is_live(record):
            raise RuntimeError(f"Stale GDB server state found for port {args.port_num}")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(record["token"])


def cmd_status(args: argparse.Namespace) -> None:
    """Print the status of a running GDB HTTP server.

    :param args: Parsed CLI arguments.
    :returns: None
    """
    try:
        token, host, port = _explicit_or_state_token(args)
        data = _request(_endpoint_url(host, port, "/status"), token)
        print(json.dumps(data, indent=2))
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.HTTPError as exc:
        print(f"Server rejected the status request (HTTP {exc.code})", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(f"No GDB server running on {args.host}:{args.port_num}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop a running GDB HTTP server via its ``/shutdown`` endpoint.

    :param args: Parsed CLI arguments.
    :returns: None
    """
    try:
        token, host, port = _explicit_or_state_token(args)
        _request(_endpoint_url(host, port, "/shutdown"), token, "POST", b"{}")
        print(f"Server on {host}:{port} is shutting down.")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.HTTPError as exc:
        print(f"Server rejected the stop request (HTTP {exc.code})", file=sys.stderr)
        raise SystemExit(1) from exc
    except urllib.error.URLError as exc:
        print(f"No GDB server running on {args.host}:{args.port_num}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_list(_args: argparse.Namespace) -> None:
    """Print connected J-Link devices via ``nrfutil device list``.

    :param _args: Parsed CLI arguments, unused.
    :returns: None
    """
    try:
        result = nrfutil_device(["list", "--traits", "jlink"])
    except GdbSessionError as e:
        print(f"Error: {e}")
        exit(1)
    print(result.get("stdout", "").strip() or "No J-Link devices found.")


def _port_arg(raw: str) -> int:
    """Parse a TCP port command-line value.

    :param raw: Raw command-line value.
    :returns: The validated TCP port.
    :raises argparse.ArgumentTypeError: If the value is not an integer from 1 to 65535.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer from 1 to 65535") from exc
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer from 1 to 65535")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI.

    :returns: The configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(description="NCS/Zephyr GDB debug CLI and HTTP server")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_serve = subparsers.add_parser("serve", help="Start the GDB HTTP control server")
    p_serve.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_serve.add_argument("--port-num", type=_port_arg, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_serve.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Acknowledge the risk of a network-reachable bind",
    )
    p_serve.add_argument("--build-dir", default="build", help="west/CMake build directory")
    p_serve.add_argument("--elf", help="ELF path (defaults to <build-dir>/zephyr/zephyr.elf)")
    p_serve.add_argument(
        "--gdb",
        default=None,
        help="GDB binary. Default: auto-selected from arch (arm-zephyr-eabi-gdb / "
        "riscv64-zephyr-elf-gdb), where arch is auto-detected from the ELF.",
    )
    p_serve.add_argument("--runner", default=DEFAULT_RUNNER, help="west runner (jlink)")
    p_serve.add_argument(
        "--gdbserver-exe",
        default=None,
        help="J-Link GDB server executable handed to the west jlink runner. Default: "
        "auto-detect the headless build per platform (Windows JLinkGDBServerCL, "
        "Linux/macOS JLinkGDBServerCLExe). Override only for a non-standard install.",
    )
    p_serve.add_argument("--gdb-server-port", type=int, default=DEFAULT_GDB_SERVER_PORT, help="GDB server TCP port")
    p_serve.add_argument(
        "--rtt-port",
        type=int,
        default=DEFAULT_RTT_TELNET_PORT,
        help="J-Link RTT telnet port for /rtt capture (JLinkGDBServer default 19021).",
    )
    p_serve.add_argument(
        "--core",
        default=DEFAULT_CORE,
        help="Core name for this session (e.g. 'app', 'net'). Connect additional "
        "cores at runtime via POST /connect with a distinct core + gdb_server_port.",
    )
    p_serve.add_argument(
        "--dev-id",
        default=None,
        help="J-Link probe serial number. Needed to bind the right probe when two "
        "GDB servers (app + net core) share one physical J-Link.",
    )
    p_serve.add_argument("--autostart", action="store_true", help="Connect a session immediately")
    p_serve.add_argument(
        "--allow-destructive",
        action="store_true",
        help="Permit the destructive /erase and /recover endpoints. Off by default so an "
        "autonomous agent cannot mass-erase or recover a board; the human operator opts in here.",
    )
    p_serve.add_argument("--load", action="store_true", help="Flash the ELF on connect")
    p_serve.add_argument("--attach", action="store_true", help="Attach without reset/flash")
    p_serve.add_argument("--no-west", action="store_true", help="Assume an external GDB server is already running")
    p_serve.add_argument(
        "--west-topdir",
        help="NCS workspace topdir to run `west debugserver` from (e.g. C:/ncs/v3.2.0)",
    )
    p_serve.add_argument(
        "--arch",
        choices=["arm", "riscv"],
        help="Target arch for fault decoding. Default inferred from --gdb; "
        "'riscv' also selects the RISC-V GDB when --gdb is not given (nRF54L FLPR).",
    )
    p_serve.add_argument(
        "--svd",
        help="Path to a CMSIS-SVD file for /peripheral register decoding (Nordic SVDs live in the NCS MDK folder).",
    )
    p_serve.add_argument(
        "--max-response-chars",
        type=int,
        default=DEFAULT_RESPONSE_LIMIT_CHARS,
        help="Spill responses larger than this many chars to a file (0 disables). Protects the agent context window.",
    )
    p_serve.add_argument(
        "--coredump",
        help="Path to a Zephyr coredump .bin for offline post-mortem debugging "
        "(drives coredump_gdbserver.py instead of a live J-Link target).",
    )
    p_serve.add_argument(
        "--coredump-gdbserver",
        help="Path to coredump_gdbserver.py (default: <west-topdir>/zephyr/scripts/coredump/).",
    )

    p_status = subparsers.add_parser("status", help="Query a running server's status")
    p_status.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_status.add_argument("--port-num", type=_port_arg, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_status.add_argument("--token", help="Explicit process token")
    p_status.add_argument("--token-file", help="Read the process token from a file")

    p_stop = subparsers.add_parser("stop", help="Stop a running server")
    p_stop.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_stop.add_argument("--port-num", type=_port_arg, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_stop.add_argument("--token", help="Explicit process token")
    p_stop.add_argument("--token-file", help="Read the process token from a file")

    p_token = subparsers.add_parser("token", help="Print the active server token")
    p_token.add_argument("--port-num", type=_port_arg, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_token.add_argument("--json", action="store_true", help="Print the complete state record as JSON")

    subparsers.add_parser("list", help="List attached J-Link devices")

    return parser


def main() -> None:
    """Parse CLI arguments and dispatch to the requested subcommand."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "token":
        cmd_token(args)
    elif args.command == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
