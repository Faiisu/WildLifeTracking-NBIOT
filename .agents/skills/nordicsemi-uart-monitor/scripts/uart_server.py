# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5"]
# ///

"""Nordic UART/VCOM Single-File CLI and HTTP Session Server.

This module provides a command-line interface and a local HTTP server for
reading, capturing and interacting with a UART/VCOM console on a Nordic
Semiconductor development kit.

The HTTP server is the primary interface. It holds the serial port open in a
background reader thread and buffers decoded lines in a sequence-numbered ring,
so an agent can:

- poll incrementally with a cursor (``GET /logs?since_seq=``) instead of
  blocking for a fixed duration,
- run request/response against a Zephyr shell (``POST /send`` with ``expect``),
- reset the board and capture the boot log without a race
  (``POST /reset`` clears the buffer and restarts the sequence at 0),
- control DTR/RTS explicitly (Nordic DK UART lines are tri-stated until the
  terminal asserts DTR).

Only one process may hold a serial port at a time, so a running server blocks
every other tool from using that VCOM until it is stopped.

PEP 723 invocation (preferred):
    The script declares its dependencies inline in the PEP 723 header above, so
    `uv run` and `pipx run` install pyserial automatically on first use:

        uv run scripts/uart_server.py status --port-num 8100
        pipx run scripts/uart_server.py status --port-num 8100

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
import math
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
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:  # pragma: no cover - gives the type checker the real module
    import serial
    import serial.tools.list_ports

# pyserial is imported defensively so the pure helpers — line assembly, the ring
# buffer, response shaping — stay importable, and unit-testable, in an
# environment without it. Everything that touches a port calls require_serial
# first, so the name is never used unbound.
try:
    import serial
    import serial.tools.list_ports

    SERIAL_IMPORT_ERROR: Optional[str] = None
except ImportError as _exc:  # pragma: no cover - only hit when pyserial is absent
    SERIAL_IMPORT_ERROR = str(_exc)

# Exception tuple usable in ``except`` clauses whether or not pyserial imported.
_SERIAL_ERRORS: tuple[type[BaseException], ...] = (
    (serial.SerialException, OSError) if SERIAL_IMPORT_ERROR is None else (OSError,)
)

# --- Constants ---

DEFAULT_HTTP_PORT = 8100
DEFAULT_BAUD = 115200
DEFAULT_NEWLINE = "\r\n"

# Lines retained in the ring buffer. At 115200 baud a chatty application emits
# a few hundred lines per second, so this holds roughly a minute of heavy logging.
DEFAULT_RING_LINES = 5000

# Serial read timeout. Short enough that the reader loop notices a stop request
# and flushes a pending partial line promptly.
SERIAL_READ_TIMEOUT_S = 0.1

# A Zephyr shell prompt ("uart:~$ ") arrives without a trailing newline, so the
# reader flushes whatever is pending once the line has been quiet this long.
# Without this, `expect` could never match a prompt.
PARTIAL_FLUSH_S = 0.15

# Hard cap on a single line held in the assembler, so a peer that never sends a
# newline cannot grow the buffer without bound.
MAX_LINE_BYTES = 8192

MAX_REQUEST_BODY_BYTES = 1 << 20
DEFAULT_MAX_RESPONSE_CHARS = 40000
TOKEN_HEADER = "X-UART-Token"
STATE_FILE_PREFIX = "server-"

# Spilled responses go in a dedicated owner-only directory rather than loose in
# the shared temp root, so captured output is not world-readable.
SPILL_SUBDIR = "uart_mcp_spill"
DEFAULT_MAX_LINES = 500
DEFAULT_EXPECT_TIMEOUT_S = 5.0
MAX_TIMEOUT_S = 300.0

NRFUTIL_TIMEOUT_S = 60
NRFUTIL_STDOUT_PREVIEW_CHARS = 2000

# CSI escape sequences (colors, cursor moves) emitted by the Zephyr shell when
# CONFIG_SHELL_VT100_COLORS is enabled, plus OSC sequences. Stripped by default:
# they are noise in a captured log and would break `expect` patterns.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

# --- Module state (owned by the `serve` command) ---

server_session: Optional["UartSession"] = None
http_server: Optional[Any] = None
server_response_limit: int = DEFAULT_MAX_RESPONSE_CHARS
server_spill_dir: str = os.path.join(tempfile.gettempdir(), SPILL_SUBDIR)
server_token: Optional[str] = None


class UartError(RuntimeError):
    """Raised for recoverable UART session failures (bad state, port errors)."""


# --- Line decoding ---


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*.

    :param text: Decoded line text, possibly carrying VT100 escapes.
    :returns: The text with CSI/OSC escape sequences removed.
    """
    return _ANSI_RE.sub("", text)


def decode_line(raw: bytes, *, ansi: bool = True) -> str:
    """Decode one raw line, replacing undecodable bytes.

    :param raw: Raw line bytes, without the terminating newline.
    :param ansi: Whether to strip ANSI escape sequences.
    :returns: The decoded line with trailing carriage returns removed.
    """
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    return strip_ansi(text) if ansi else text


class LineAssembler:
    """Split an incoming byte stream into complete text lines.

    Serial reads arrive in arbitrary chunks, so bytes after the last newline are
    held until the rest of the line shows up. :meth:`take_partial` releases that
    tail, which is how a newline-less shell prompt becomes an observable line.

    :param ansi: Whether decoded lines have ANSI escapes stripped.
    :param max_line_bytes: Length at which an unterminated line is force-flushed.

    """

    def __init__(self, *, ansi: bool = True, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        """Create an assembler with an empty partial buffer.

        :param ansi: Whether decoded lines have ANSI escapes stripped.
        :param max_line_bytes: Length at which an unterminated line is force-flushed.
        :returns: None
        """
        self._partial = bytearray()
        self._ansi = ansi
        self._max_line_bytes = max_line_bytes

    def feed(self, chunk: bytes) -> list[str]:
        """Consume *chunk* and return every line it completed.

        :param chunk: Bytes read from the serial port.
        :returns: Decoded lines, in arrival order; empty when no line completed.
        """
        if chunk:
            self._partial.extend(chunk)

        lines: list[str] = []
        while True:
            idx = self._partial.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._partial[:idx])
            del self._partial[: idx + 1]
            lines.append(decode_line(raw, ansi=self._ansi))

        if len(self._partial) >= self._max_line_bytes:
            raw = bytes(self._partial)
            self._partial.clear()
            lines.append(decode_line(raw, ansi=self._ansi))

        return lines

    def has_partial(self) -> bool:
        """Report whether an unterminated line is currently buffered.

        :returns: ``True`` when bytes are held back awaiting a newline.
        """
        return bool(self._partial)

    def take_partial(self) -> Optional[str]:
        """Release and clear the buffered partial line.

        :returns: The decoded partial line, or ``None`` when nothing is pending.
        """
        if not self._partial:
            return None
        raw = bytes(self._partial)
        self._partial.clear()
        return decode_line(raw, ansi=self._ansi)


# --- Line ring buffer ---


@dataclass(frozen=True)
class LogLine:
    """One captured line with its cursor position and arrival time.

    :param seq: Monotonic sequence number, restarting at 0 after a buffer clear.
    :param t_ms: Milliseconds since the capture epoch (the last clear or open).
    :param text: Decoded line text.
    :param partial: Whether the line was flushed on idle rather than by a newline.

    """

    seq: int
    t_ms: float
    text: str
    partial: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Project the line into its JSON representation.

        :returns: A mapping with ``seq``, ``t_ms``, ``text``, and ``partial``.
        """
        return {"seq": self.seq, "t_ms": round(self.t_ms, 1), "text": self.text, "partial": self.partial}


class LineRing:
    """Thread-safe, fixed-capacity ring of sequence-numbered log lines.

    Sequence numbers let a client resume exactly where it left off instead of
    re-reading a time window. Evictions are counted in :attr:`dropped` so a
    client can tell "nothing was logged" apart from "the buffer overflowed".

    :param capacity: Maximum number of lines retained.

    """

    def __init__(self, capacity: int = DEFAULT_RING_LINES) -> None:
        """Create an empty ring.

        :param capacity: Maximum number of lines retained; at least 1.
        :returns: None
        """
        self.capacity = max(1, capacity)
        self._buf: deque[LogLine] = deque(maxlen=self.capacity)
        self._next_seq = 0
        self._t0 = time.monotonic()
        self._generation = 0
        self.dropped = 0
        self._cond = threading.Condition()

    @property
    def next_seq(self) -> int:
        """Sequence number the next appended line will receive.

        :returns: The next sequence number.
        """
        with self._cond:
            return self._next_seq

    @property
    def generation(self) -> int:
        """Number of times the buffer has been cleared.

        Sequence numbers restart at 0 on every clear, so a cursor is only
        meaningful within one generation. A client that sees this value change
        knows its cursor belongs to a previous capture and must restart, which
        comparing sequence numbers alone cannot always reveal.

        :returns: The current generation counter.
        """
        with self._cond:
            return self._generation

    def append(self, text: str, *, partial: bool = False) -> LogLine:
        """Append a line, evicting and counting the oldest one when full.

        :param text: Decoded line text.
        :param partial: Whether the line was flushed on idle rather than by a newline.
        :returns: The stored :class:`LogLine`, including its assigned sequence number.
        """
        with self._cond:
            if len(self._buf) == self.capacity:
                self.dropped += 1
            line = LogLine(
                seq=self._next_seq,
                t_ms=(time.monotonic() - self._t0) * 1000.0,
                text=text,
                partial=partial,
            )
            self._buf.append(line)
            self._next_seq += 1
            self._cond.notify_all()
            return line

    def clear(self) -> int:
        """Discard every buffered line and restart the sequence at 0.

        Used before a device reset so the boot log starts at a known cursor.

        :returns: The new next sequence number (always 0).
        """
        with self._cond:
            self._buf.clear()
            self._next_seq = 0
            self.dropped = 0
            self._t0 = time.monotonic()
            self._generation += 1
            self._cond.notify_all()
            return 0

    def _since_locked(self, since_seq: int) -> tuple[list[LogLine], bool]:
        """Collect buffered lines at or after *since_seq*; caller holds the lock.

        :param since_seq: Inclusive lower-bound sequence number.
        :returns: ``(lines, buffer_reset)`` where *buffer_reset* marks a cursor
            from before a :meth:`clear`.
        """
        # A cursor ahead of the ring can only mean the buffer was cleared under
        # the client. Returning everything (plus the flag) beats silently
        # starving the client until the sequence catches up again. This cannot
        # detect a clear the sequence has already grown past, which is why every
        # response also carries `generation`.
        if since_seq > self._next_seq:
            return list(self._buf), True

        # Walk back from the newest line and stop at the cursor. Callers poll for
        # what is new, so the tail is the common case and scanning the whole ring
        # for it would make each poll cost O(capacity) instead of O(new lines).
        out: list[LogLine] = []
        for line in reversed(self._buf):
            if line.seq < since_seq:
                break
            out.append(line)
        out.reverse()
        return out, False

    def since(self, since_seq: int) -> tuple[list[LogLine], bool]:
        """Return buffered lines at or after *since_seq*.

        :param since_seq: Inclusive lower-bound sequence number.
        :returns: ``(lines, buffer_reset)`` in chronological order.
        """
        with self._cond:
            return self._since_locked(since_seq)

    def window(self, window_s: float) -> list[LogLine]:
        """Return lines received within the last *window_s* seconds.

        :param window_s: Length of the trailing time window, in seconds.
        :returns: Matching lines, in chronological order.
        """
        with self._cond:
            if not self._buf:
                return []
            cutoff = self._buf[-1].t_ms - (window_s * 1000.0)
            return [line for line in self._buf if line.t_ms >= cutoff]

    def snapshot(self) -> list[LogLine]:
        """Return every buffered line.

        :returns: All retained lines, in chronological order.
        """
        with self._cond:
            return list(self._buf)

    def __len__(self) -> int:
        """Return the number of buffered lines.

        :returns: Count of lines currently retained.
        """
        with self._cond:
            return len(self._buf)

    def wait_for(
        self,
        matcher: Callable[[str], bool],
        *,
        since_seq: int,
        timeout_s: float,
    ) -> tuple[Optional[LogLine], list[LogLine], bool]:
        """Block until a line at or after *since_seq* satisfies *matcher*.

        :param matcher: Predicate applied to each candidate line's text.
        :param since_seq: Inclusive lower-bound sequence number to scan from.
        :param timeout_s: Maximum time to wait, in seconds.
        :returns: ``(match, lines, buffer_reset)``; *match* is ``None`` on timeout
            and *lines* always carries everything seen since *since_seq*.
        """
        deadline = time.monotonic() + timeout_s
        seen: list[LogLine] = []
        scan_seq = since_seq
        reset = False
        with self._cond:
            while True:
                # Only the lines added since the last wakeup are examined. Every
                # append wakes this loop, so re-testing the whole backlog each
                # time would run the matcher O(n^2) times over a busy log.
                fresh, fresh_reset = self._since_locked(scan_seq)
                reset = reset or fresh_reset
                if fresh:
                    scan_seq = fresh[-1].seq + 1
                for line in fresh:
                    seen.append(line)
                    if matcher(line.text):
                        return line, seen, reset
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, seen, reset
                self._cond.wait(timeout=min(remaining, 0.25))


def filter_lines(lines: list[LogLine], pattern: Optional[str]) -> list[LogLine]:
    """Keep only the lines whose text matches *pattern*.

    :param lines: Candidate lines.
    :param pattern: Regular expression; ``None`` or empty keeps everything.
    :returns: The matching lines, in the original order.
    :raises ValueError: When *pattern* is not a valid regular expression.
    """
    if not pattern:
        return lines
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid 'grep' pattern {pattern!r}: {exc}") from exc
    return [line for line in lines if regex.search(line.text)]


def compile_matcher(pattern: str) -> Callable[[str], bool]:
    """Build a search predicate for *pattern*.

    :param pattern: Regular expression to search for within a line.
    :returns: A callable returning whether a given line matches.
    :raises ValueError: When *pattern* is not a valid regular expression.
    """
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid pattern {pattern!r}: {exc}") from exc
    return lambda text: regex.search(text) is not None


def validate_timeout(timeout_s: float) -> float:
    """Check a caller-supplied timeout and clamp it to the server maximum.

    Callers must apply this *before* writing to the device. A timeout rejected
    afterwards would fail a command the board has already executed, and the
    caller — seeing only an error — could resend it and run it twice. ``NaN``
    and infinities arrive readily from JSON and would otherwise wait forever or
    raise from deep inside the wait.

    :param timeout_s: Requested timeout, in seconds.
    :returns: *timeout_s* clamped to :data:`MAX_TIMEOUT_S`.
    :raises ValueError: When *timeout_s* is not a finite, non-negative number.
    """
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise ValueError(f"'timeout_s' must be a finite, non-negative number, got {timeout_s!r}")
    return min(timeout_s, MAX_TIMEOUT_S)


# --- Response size control ---


def write_spill_file(body: str, spill_dir: str) -> str:
    """Write *body* to a private file in *spill_dir* and return its path.

    Captured UART output can contain credentials, keys or personal data, so the
    file is created owner-only and symlinks are refused: the name is derived
    from the content digest and therefore predictable, which in a shared
    temporary directory would otherwise let another user pre-plant a symlink and
    redirect the write.

    :param body: Serialized JSON to store.
    :param spill_dir: Directory to write into; created if absent.
    :returns: The path written.
    :raises OSError: When the directory or file cannot be created.
    """
    os.makedirs(spill_dir, mode=0o700, exist_ok=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path = os.path.join(spill_dir, f"uart-response-{digest[:12]}.json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path


def spill_body(body: str, limit_chars: int, spill_dir: str) -> dict[str, Any]:
    """Build the replacement envelope for an oversized serialized response.

    :param body: The serialized JSON that exceeded the limit.
    :param limit_chars: The threshold that was exceeded, for the message.
    :param spill_dir: Directory to write the spill file into.
    :returns: An envelope pointing at the file, or describing why it failed.
    """
    try:
        path = write_spill_file(body, spill_dir)
    except OSError as exc:
        return {
            "spilled": False,
            "error": f"response too large ({len(body)} chars) and spill failed: {exc}",
            "preview": body[:limit_chars],
        }
    return {
        "spilled": True,
        "reason": (
            f"Response is {len(body)} chars (limit {limit_chars}); the full JSON was written "
            "to a file to protect context. Read the file, or narrow the query with "
            "'max_lines' / 'grep' / 'since_seq'."
        ),
        "path": path,
        "bytes": len(body),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "preview": body[:1000],
    }


def serialize_response(data: Any, limit_chars: int, spill_dir: str) -> bytes:
    """Serialize a response body once, spilling it when it is too large.

    Protects the agent's context window: a verbose boot log can be tens of
    thousands of lines, and reading it whole is rarely what was wanted.

    :param data: The JSON-serializable response payload.
    :param limit_chars: Threshold in characters; ``0`` disables spilling.
    :param spill_dir: Directory to write spill files into.
    :returns: The encoded response body.
    """
    body = json.dumps(data)
    if 0 < limit_chars < len(body):
        body = json.dumps(spill_body(body, limit_chars, spill_dir))
    return body.encode("utf-8")


# --- Authentication and server state ---


def _is_windows() -> bool:
    """Return whether Windows storage and permission semantics apply.

    :returns: ``True`` when the script is running on Windows.
    """
    return os.name == "nt"


def _runtime_dir() -> Path:
    """Return the private runtime directory used for UART server records.

    :returns: The private per-user runtime directory.
    :raises RuntimeError: If a safe runtime directory cannot be created or verified.
    """
    if _is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        directory = base / "Nordic Semiconductor" / "Nordic MCP" / "runtime" / "uart"
    else:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        user_id = str(os.getuid())
        base = Path(xdg_runtime) if xdg_runtime else Path(tempfile.gettempdir()) / f"uart-{user_id}"
        directory = base / "nordic-uart" if xdg_runtime else base
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
    except OSError as exc:
        raise RuntimeError("Cannot create the private UART runtime directory") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("The UART runtime path is not a directory")
    if not _is_windows():
        if info.st_uid != os.getuid():
            raise RuntimeError("The UART runtime directory is not owned by the current user")
        try:
            os.chmod(directory, 0o700)
            info = directory.lstat()
        except OSError as exc:
            raise RuntimeError("Cannot secure the private UART runtime directory") from exc
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("The UART runtime directory permissions are unsafe")
    return directory


def _state_path(port: int) -> Path:
    """Return the state-file path for a server port.

    :param port: TCP port identifying the server record.
    :returns: The expected state-file path.
    :raises ValueError: If *port* is outside the TCP port range.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("'port' must be an integer from 1 to 65535")
    return _runtime_dir() / f"{STATE_FILE_PREFIX}{port}.json"


def _validate_state_file(path: Path) -> None:
    """Validate ownership, type, and permissions of an existing state file.

    :param path: State-file path to inspect.
    :returns: None
    :raises RuntimeError: If the file is not private and owned by the current user.
    """
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("The UART server state is not a regular file")
    if not _is_windows():
        if info.st_uid != os.getuid():
            raise RuntimeError("The UART server state is not owned by the current user")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError("The UART server state permissions are unsafe")


def _write_state(record: dict[str, Any]) -> Path:
    """Atomically persist a private server state record.

    :param record: State record containing process and authentication details.
    :returns: The final state-file path.
    :raises RuntimeError: If the record cannot be written safely.
    """
    path = _state_path(record["port"])
    if path.exists():
        try:
            _validate_state_file(path)
        except OSError as exc:
            raise RuntimeError("Cannot inspect the UART server state") from exc
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
        raise RuntimeError("Cannot write the private UART server state") from exc
    return path


def _read_state(port: int) -> dict[str, Any]:
    """Read and validate a private UART server state record.

    :param port: TCP port identifying the record.
    :returns: The validated state record.
    :raises RuntimeError: If the record is absent, unsafe, or malformed.
    """
    path = _state_path(port)
    try:
        _validate_state_file(path)
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"No UART server state found for port {port}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The UART server state is unreadable") from exc
    if not isinstance(record, dict):
        raise RuntimeError("The UART server state is malformed")
    required = {"host": str, "port": int, "started_at": str, "token": str}
    malformed = (
        isinstance(record.get(key), bool) or not isinstance(record.get(key), kind) for key, kind in required.items()
    )
    if any(malformed):
        raise RuntimeError("The UART server state is malformed")
    if record["port"] != port or not record["token"]:
        raise RuntimeError("The UART server state is malformed")
    return record


def _request(url: str, token: str, method: str = "GET", data: Optional[bytes] = None) -> Any:
    """Perform an authenticated request to a UART server.

    :param url: Endpoint URL.
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

    Serial-port openness is deliberately not a liveness condition: the HTTP
    server remains useful after an open failure or an explicit ``POST /close``.

    :param record: Validated state record.
    :returns: ``True`` only when the authenticated UART server responds.
    """
    try:
        result = _request(_endpoint_url(record["host"], record["port"], "/status"), record["token"])
    except (urllib.error.URLError, ValueError, TimeoutError):
        return False
    return isinstance(result, dict) and result.get("server") == "uart"


def _remove_state_if_owned(path: Path, token: str) -> None:
    """Remove a state record only if it still belongs to this server.

    :param path: State-file path to remove.
    :param token: Expected process token.
    :returns: None
    """
    try:
        _validate_state_file(path)
        record = json.loads(path.read_text(encoding="utf-8"))
        if hmac.compare_digest(str(record.get("token", "")), token):
            path.unlink()
    except FileNotFoundError:
        return
    except (OSError, RuntimeError, json.JSONDecodeError):
        print("Warning: could not clean up the UART server state", file=sys.stderr)


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


# --- Device discovery ---


def nrfutil_device(args: list[str]) -> dict[str, Any]:
    """Run an ``nrfutil device`` subcommand and return a structured result.

    :param args: Arguments following ``nrfutil device`` (e.g. ``["list"]``).
    :returns: A dict with ``returncode``, ``stdout``, and ``stderr``; parsed
        output is added under ``json`` when the command emitted JSON.
    :raises UartError: When the ``nrfutil`` executable is not on PATH.
    """
    if shutil.which("nrfutil") is None:
        raise UartError("`nrfutil` not found on PATH.")
    proc = subprocess.run(
        ["nrfutil", "device", *args],
        capture_output=True,
        text=True,
        timeout=NRFUTIL_TIMEOUT_S,
    )
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    # nrfutil streams JSON Lines (task_begin, task_end, info), so the output as a
    # whole is not a single document and must be parsed one line at a time.
    objects = [parsed for line in proc.stdout.splitlines() if (parsed := _try_json(line.strip())) is not None]
    if objects:
        result["json"] = objects[0] if len(objects) == 1 else objects
    return result


def _try_json(text: str) -> Any:
    """Parse *text* as a JSON document.

    :param text: A single candidate JSON line.
    :returns: The parsed value, or ``None`` when *text* is not JSON.
    """
    if not text.startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_for_devices(payload: Any) -> list[dict[str, Any]]:
    """Find the device records inside an ``nrfutil device list --json`` payload.

    The envelope has changed shape across nrfutil releases, so rather than
    hard-coding one path this walks the structure for objects that carry both a
    serial number and a serial-port list.

    :param payload: Parsed JSON from nrfutil.
    :returns: Every device-looking object found, in traversal order.
    """
    found: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        """Recurse into *node*, collecting device-shaped objects."""
        if isinstance(node, dict):
            has_serial = any(k in node for k in ("serialNumber", "serial_number", "serialnumber"))
            has_ports = any(k in node for k in ("serialPorts", "serial_ports", "serialports"))
            if has_serial and has_ports:
                found.append(node)
                return
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)
    return found


def _first_key(node: dict[str, Any], *names: str) -> Any:
    """Return the first present value among *names*.

    :param node: Mapping to read from.
    :param names: Candidate key spellings, in priority order.
    :returns: The first value found, or ``None``.
    """
    for name in names:
        if name in node:
            return node[name]
    return None


def correlate_devices(nrfutil_payload: Any) -> list[dict[str, Any]]:
    """Map each Nordic device to its VCOM ports.

    Each port carries the VCOM index referenced by the Nordic DK documentation
    ("Serial Port 0" is ``vcom`` 0), taken from nrfutil when it reports one and
    otherwise inferred from position.

    :param nrfutil_payload: Parsed ``nrfutil device list --json`` output.
    :returns: One entry per device with ``serial_number``, ``board_version``,
        ``device_family``, and a ``ports`` list of ``{vcom, path}`` mappings
        ordered by VCOM index.
    """
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _walk_for_devices(nrfutil_payload):
        serial = str(_first_key(record, "serialNumber", "serial_number", "serialnumber") or "")
        # The same kit appears on both the task_end and info lines.
        if serial in seen:
            continue
        seen.add(serial)

        raw_ports = _first_key(record, "serialPorts", "serial_ports", "serialports") or []
        ports: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_ports):
            vcom: Any = None
            if isinstance(entry, dict):
                path = _first_key(entry, "comName", "com_name", "path", "port")
                vcom = _first_key(entry, "vcom", "vcom_index")
            else:
                path = entry
            if path:
                # nrfutil labels each port with its VCOM index; list order does
                # not reliably follow it, and picking the wrong port is the most
                # common cause of "the board prints nothing".
                ports.append({"vcom": index if vcom is None else int(vcom), "path": str(path)})
        ports.sort(key=lambda port: port["vcom"])

        devkit = record.get("devkit")
        devkit = devkit if isinstance(devkit, dict) else {}
        devices.append({
            "serial_number": serial,
            "board_version": _first_key(record, "boardVersion", "board_version")
            or _first_key(devkit, "boardVersion", "board_version"),
            "device_family": _first_key(record, "deviceFamily", "device_family")
            or _first_key(devkit, "deviceFamily", "device_family"),
            "ports": ports,
        })
    return devices


def list_serial_ports() -> list[dict[str, Any]]:
    """Enumerate every serial port visible to the OS.

    :returns: One mapping per port with ``path``, ``description``, ``manufacturer``,
        ``vid``, ``pid``, and ``serial_number``.
    :raises UartError: When pyserial is unavailable.
    """
    require_serial()
    ports = []
    for info in serial.tools.list_ports.comports():
        ports.append({
            "path": info.device,
            "description": info.description,
            "manufacturer": info.manufacturer,
            "vid": info.vid,
            "pid": info.pid,
            "serial_number": info.serial_number,
        })
    return ports


def describe_devices() -> dict[str, Any]:
    """Build the ``/devices`` payload: OS serial ports plus Nordic correlation.

    ``nrfutil`` is optional here — without it the OS port list is still useful,
    it just cannot say which port belongs to which kit.

    :returns: A mapping with ``serial_ports``, ``devices``, and, when nrfutil is
        unavailable, a ``note`` explaining the missing correlation.
    """
    payload: dict[str, Any] = {"serial_ports": list_serial_ports()}
    try:
        result = nrfutil_device(["list", "--json"])
    except (UartError, OSError, subprocess.SubprocessError) as exc:
        payload["devices"] = []
        payload["note"] = f"nrfutil device list unavailable ({exc}); ports are not correlated to kits."
        return payload

    payload["devices"] = correlate_devices(result.get("json"))
    if not payload["devices"]:
        payload["note"] = "nrfutil returned no correlatable devices; see 'nrfutil_stdout'."
        # Truncated: the raw envelope runs to several KB and would otherwise
        # dominate the reply for a caller that can do nothing with it.
        payload["nrfutil_stdout"] = result.get("stdout", "")[:NRFUTIL_STDOUT_PREVIEW_CHARS]
    return payload


# --- Serial session ---


def require_serial() -> None:
    """Ensure pyserial is importable before touching a port.

    :returns: None
    :raises UartError: When pyserial could not be imported.
    """
    if SERIAL_IMPORT_ERROR is not None:
        raise UartError(f"pyserial is not available ({SERIAL_IMPORT_ERROR}). Run this script with `uv run`.")


def open_serial(
    port: str,
    baud: int,
    *,
    dtr: bool = True,
    rts: bool = True,
    timeout: float = SERIAL_READ_TIMEOUT_S,
) -> "serial.Serial":
    """Open a serial port with explicit DTR/RTS control.

    The port is configured before opening so the requested DTR/RTS levels are
    applied by the open itself. Nordic DK UART lines stay tri-stated until DTR
    is asserted, but asserting it also pulses reset on some targets — which is
    why the caller gets to choose.

    :param port: Serial port path or name, e.g. ``/dev/ttyACM0`` or ``COM3``.
    :param baud: Baud rate.
    :param dtr: Whether to assert DTR on open.
    :param rts: Whether to assert RTS on open.
    :param timeout: Read timeout in seconds.
    :returns: An open :class:`serial.Serial` instance.
    :raises UartError: When the port cannot be opened.
    """
    require_serial()
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = timeout
    ser.dtr = dtr
    ser.rts = rts
    try:
        ser.open()
    except _SERIAL_ERRORS as exc:
        raise UartError(
            f"Cannot open port {port!r}: {exc}. The port may be held by another "
            f"process — a running uart_server (try `status --port-num {DEFAULT_HTTP_PORT}`), "
            "a serial terminal, or another capture."
        ) from exc
    return ser


class UartSession:
    """An open UART port with a background reader feeding a line ring.

    :param port: Serial port path or name.
    :param baud: Baud rate.
    :param dtr: Whether to assert DTR on open.
    :param rts: Whether to assert RTS on open.
    :param ring_lines: Ring buffer capacity, in lines.
    :param ansi: Whether to strip ANSI escape sequences from captured lines.
    :param log_file: Optional path to append every captured line to.
    :param echo: Whether to print every captured line to stdout as it arrives.

    """

    def __init__(
        self,
        port: str,
        baud: int = DEFAULT_BAUD,
        *,
        dtr: bool = True,
        rts: bool = True,
        ring_lines: int = DEFAULT_RING_LINES,
        ansi: bool = True,
        log_file: Optional[str] = None,
        echo: bool = False,
    ) -> None:
        """Create a closed session; call :meth:`open` to acquire the port.

        :param port: Serial port path or name.
        :param baud: Baud rate.
        :param dtr: Whether to assert DTR on open.
        :param rts: Whether to assert RTS on open.
        :param ring_lines: Ring buffer capacity, in lines.
        :param ansi: Whether to strip ANSI escape sequences from captured lines.
        :param log_file: Optional path to append every captured line to.
        :param echo: Whether to print every captured line to stdout as it arrives.
        :returns: None
        """
        self.port = port
        self.baud = baud
        self.dtr = dtr
        self.rts = rts
        self.ansi = ansi
        self.log_file = log_file
        self.echo = echo
        self.ring = LineRing(ring_lines)
        self.error: Optional[str] = None

        self._ser: Optional[Any] = None
        self._assembler = LineAssembler(ansi=ansi)
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._log_handle: Optional[Any] = None
        self._bytes_read = 0

    # -- lifecycle --

    @property
    def is_open(self) -> bool:
        """Report whether the serial port is currently held open.

        :returns: ``True`` when the port is open.
        """
        return self._ser is not None and bool(self._ser.is_open)

    def open(self) -> None:
        """Acquire the serial port and start the background reader.

        :returns: None
        :raises UartError: When the port is already open or cannot be opened.
        """
        if self.is_open:
            raise UartError(f"Port {self.port!r} is already open on this server.")
        self.error = None
        self._ser = open_serial(self.port, self.baud, dtr=self.dtr, rts=self.rts)
        self._stop.clear()
        self._reader = threading.Thread(target=self._reader_loop, name="uart-reader", daemon=True)
        self._reader.start()

    def close(self) -> None:
        """Stop the reader and release the serial port.

        Safe to call repeatedly and from a signal handler.

        :returns: None
        """
        self._stop.set()
        reader, self._reader = self._reader, None
        if reader is not None and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except _SERIAL_ERRORS:
                pass
        self._close_log()

    # -- reader --

    def _record(self, text: str, *, partial: bool = False) -> None:
        """Store one captured line in the ring and the optional log file.

        :param text: Decoded line text.
        :param partial: Whether the line was flushed on idle.
        :returns: None
        """
        self.ring.append(text, partial=partial)
        if self.echo:
            # stderr is reserved for server diagnostics, so captured lines go to stdout as raw text.
            print(text, flush=True)
        if not self.log_file:
            return
        try:
            # Opened once and held: re-opening per line would cost three syscalls
            # for every log line on a port that can emit hundreds per second.
            if self._log_handle is None:
                self._log_handle = open(self.log_file, "a", encoding="utf-8")
            self._log_handle.write(text + "\n")
            # Flushed so the user can tail the file while the capture runs.
            self._log_handle.flush()
        except OSError as exc:
            # A broken log file must not take the capture down with it.
            self._close_log()
            self.log_file = None
            self.error = f"log file disabled: {exc}"

    def _close_log(self) -> None:
        """Close the log file handle if one is open.

        :returns: None
        """
        handle, self._log_handle = self._log_handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _reader_loop(self) -> None:
        """Read from the port until stopped, appending decoded lines to the ring.

        :returns: None
        """
        last_data = time.monotonic()
        while not self._stop.is_set():
            ser = self._ser
            if ser is None:
                break
            try:
                waiting = ser.in_waiting or 1
                chunk = ser.read(waiting)
            except _SERIAL_ERRORS as exc:
                self.error = f"UART read failed: {exc}"
                self._stop.set()
                break

            if chunk:
                last_data = time.monotonic()
                self._bytes_read += len(chunk)
                for text in self._assembler.feed(chunk):
                    self._record(text)
            elif self._assembler.has_partial() and (time.monotonic() - last_data) >= PARTIAL_FLUSH_S:
                # No newline is coming: a shell prompt or a progress line. Emit it
                # so `expect` can see it, flagged so the caller knows why.
                pending = self._assembler.take_partial()
                if pending is not None:
                    self._record(pending, partial=True)
                last_data = time.monotonic()

    # -- operations --

    def status(self) -> dict[str, Any]:
        """Summarise the session state.

        :returns: A mapping describing the port, line states, and buffer cursor.
        """
        return {
            "server": "uart",
            "port": self.port,
            "baud": self.baud,
            "open": self.is_open,
            "dtr": self.dtr,
            "rts": self.rts,
            "strip_ansi": self.ansi,
            "echo": self.echo,
            "log_file": self.log_file,
            "next_seq": self.ring.next_seq,
            "generation": self.ring.generation,
            "lines_buffered": len(self.ring),
            "ring_capacity": self.ring.capacity,
            "dropped": self.ring.dropped,
            "bytes_read": self._bytes_read,
            "error": self.error,
        }

    def require_open(self) -> None:
        """Ensure the port is open before an operation that needs it.

        :returns: None
        :raises UartError: When the port is closed.
        """
        if not self.is_open:
            detail = f" ({self.error})" if self.error else ""
            raise UartError(f"Serial port {self.port!r} is not open{detail}. POST /open first.")

    def write(self, text: str, newline: str = DEFAULT_NEWLINE) -> int:
        """Write *text* plus *newline* to the port.

        :param text: Payload to send.
        :param newline: Line ending appended to *text*.
        :returns: Number of bytes written.
        :raises UartError: When the port is closed or the write fails.
        """
        self.require_open()
        payload = (text + newline).encode("utf-8")
        with self._write_lock:
            ser = self._ser
            if ser is None:
                raise UartError("Serial port closed during write.")
            try:
                ser.write(payload)
                ser.flush()
            except _SERIAL_ERRORS as exc:
                self.error = f"UART write failed: {exc}"
                raise UartError(self.error) from exc
        return len(payload)

    def send(
        self,
        text: str,
        *,
        newline: str = DEFAULT_NEWLINE,
        expect: Optional[str] = None,
        timeout_s: float = DEFAULT_EXPECT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Write *text* and capture the response that follows.

        With *expect*, this is a synchronous request/response against a shell:
        the cursor is taken before the write, so no output can slip through
        between sending and listening.

        :param text: Payload to send.
        :param newline: Line ending appended to *text*.
        :param expect: Regular expression to wait for in the response.
        :param timeout_s: How long to wait for *expect*, or how long to capture
            when *expect* is not given.
        :returns: A mapping with ``sent``, ``matched``, ``lines``, and ``next_seq``.
        :raises UartError: When the port is closed or the write fails.
        :raises ValueError: When *expect* is not a valid regular expression, or
            *timeout_s* is not a finite, non-negative number. Nothing is written
            in either case.
        """
        # Both inputs are validated before the write, so a bad request cannot
        # leave the device having already run a command the caller sees fail.
        matcher = compile_matcher(expect) if expect else None
        timeout_s = validate_timeout(timeout_s)

        # Serialised so two concurrent sends cannot interleave their writes or
        # attribute each other's output to the wrong request.
        with self._send_lock:
            start_seq = self.ring.next_seq
            written = self.write(text, newline)

            if matcher is None:
                time.sleep(timeout_s)
                lines, reset = self.ring.since(start_seq)
                match = None
            else:
                match, lines, reset = self.ring.wait_for(matcher, since_seq=start_seq, timeout_s=timeout_s)

        return {
            "sent": text,
            "bytes_written": written,
            "expect": expect,
            "matched": match is not None if matcher else None,
            "matched_line": match.as_dict() if match else None,
            "since_seq": start_seq,
            "next_seq": self.ring.next_seq,
            "generation": self.ring.generation,
            "buffer_reset": reset,
            "count": len(lines),
            "lines": [line.as_dict() for line in lines],
        }

    def wait(
        self,
        pattern: str,
        *,
        since_seq: Optional[int] = None,
        timeout_s: float = DEFAULT_EXPECT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Block until a line matching *pattern* arrives.

        :param pattern: Regular expression to search for.
        :param since_seq: Cursor to scan from; defaults to the current cursor.
        :param timeout_s: Maximum time to wait, in seconds.
        :returns: A mapping with ``matched``, ``matched_line``, and the lines seen.
        :raises ValueError: When *pattern* is not a valid regular expression, or
            *timeout_s* is not a finite, non-negative number.
        """
        matcher = compile_matcher(pattern)
        timeout_s = validate_timeout(timeout_s)
        start_seq = self.ring.next_seq if since_seq is None else since_seq
        match, lines, reset = self.ring.wait_for(matcher, since_seq=start_seq, timeout_s=timeout_s)
        return {
            "pattern": pattern,
            "matched": match is not None,
            "matched_line": match.as_dict() if match else None,
            "since_seq": start_seq,
            "next_seq": self.ring.next_seq,
            "generation": self.ring.generation,
            "buffer_reset": reset,
            "count": len(lines),
            "lines": [line.as_dict() for line in lines],
        }

    def set_line(self, name: str, on: bool) -> dict[str, Any]:
        """Set the DTR or RTS control line.

        :param name: Either ``"dtr"`` or ``"rts"``.
        :param on: Whether to assert the line.
        :returns: The updated session status.
        :raises UartError: When the port is closed or the line cannot be set.
        :raises ValueError: When *name* is not a known control line.
        """
        if name not in ("dtr", "rts"):
            raise ValueError(f"unknown control line {name!r}; expected 'dtr' or 'rts'")
        self.require_open()
        ser = self._ser
        if ser is None:
            raise UartError("Serial port closed.")
        try:
            setattr(ser, name, on)
        except _SERIAL_ERRORS as exc:
            raise UartError(f"Cannot set {name.upper()}: {exc}") from exc
        setattr(self, name, on)
        return self.status()


# --- HTTP server ---


def _query_int(qs: dict[str, list[str]], key: str, default: Optional[int] = None) -> Optional[int]:
    """Read an integer query parameter.

    :param qs: Parsed query string.
    :param key: Parameter name.
    :param default: Value returned when the parameter is absent.
    :returns: The parsed integer, or *default*.
    :raises ValueError: When the value is present but not an integer.
    """
    raw = qs.get(key, [None])[0]
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"'{key}' must be an integer, got {raw!r}") from exc


def _query_float(qs: dict[str, list[str]], key: str, default: Optional[float] = None) -> Optional[float]:
    """Read a float query parameter.

    :param qs: Parsed query string.
    :param key: Parameter name.
    :param default: Value returned when the parameter is absent.
    :returns: The parsed float, or *default*.
    :raises ValueError: When the value is present but not a number.
    """
    raw = qs.get(key, [None])[0]
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"'{key}' must be a number, got {raw!r}") from exc


def _json_bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    """Read a strict JSON boolean from a request body.

    Truthy strings such as ``"false"`` are rejected rather than silently
    enabling a state change the caller did not mean.

    :param data: The parsed request body.
    :param key: The field to read.
    :param default: Value returned when the field is absent.
    :returns: The boolean value.
    :raises ValueError: When the field is present but not a JSON boolean.
    """
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ValueError(f"'{key}' must be a JSON boolean, got {value!r}")
    return value


def build_logs_payload(
    session: UartSession,
    *,
    since_seq: Optional[int],
    window_s: Optional[float],
    max_lines: int,
    grep: Optional[str],
) -> dict[str, Any]:
    """Build the ``GET /logs`` response for the given selection.

    Selection precedence is cursor, then time window, then the whole buffer;
    the result is filtered by *grep* and finally truncated to the newest
    *max_lines* entries.

    :param session: The session whose ring is read.
    :param since_seq: Inclusive cursor to resume from, or ``None``.
    :param window_s: Trailing time window in seconds, used when *since_seq* is ``None``.
    :param max_lines: Maximum number of lines to return (newest kept); values
        below 1 are treated as 1.
    :param grep: Optional regular expression filter.
    :returns: The response mapping.
    :raises ValueError: When *grep* is not a valid regular expression.
    """
    reset = False
    if since_seq is not None:
        lines, reset = session.ring.since(since_seq)
    elif window_s is not None:
        lines = session.ring.window(window_s)
    else:
        lines = session.ring.snapshot()

    lines = filter_lines(lines, grep)
    # A zero or negative cap would otherwise slice from the wrong end and report
    # a truncation that did not happen.
    max_lines = max(1, max_lines)
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[-max_lines:]

    return {
        "port": session.port,
        "open": session.is_open,
        "since_seq": since_seq,
        "next_seq": session.ring.next_seq,
        "generation": session.ring.generation,
        "dropped": session.ring.dropped,
        "buffer_reset": reset,
        "truncated": truncated,
        "count": len(lines),
        "lines": [line.as_dict() for line in lines],
    }


class UartRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler exposing UART capture and interaction endpoints as JSON."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - match BaseHTTPRequestHandler
        """Silence the default per-request logging to stderr.

        :param format: Base-handler log format string.
        :param args: Values for the log format string.
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
        if server_token is None or not hmac.compare_digest(supplied, server_token):
            self.send_json({"error": "Authentication failed"}, 403)
            return False
        return True

    def send_json(self, data: Any, status: int = 200) -> None:
        """Serialize *data* as JSON and write it as the response body.

        :param data: JSON-serializable payload to send.
        :param status: HTTP status code to respond with.
        :returns: None
        """
        body = (
            serialize_response(data, server_response_limit, server_spill_dir)
            if status == 200
            else json.dumps(data).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _session(self) -> UartSession:
        """Return the active session.

        :returns: The server's :class:`UartSession`.
        :raises UartError: When the server has no session configured.
        """
        if server_session is None:
            raise UartError("No UART session configured on this server.")
        return server_session

    # -- GET --

    def do_GET(self) -> None:
        """Dispatch authenticated ``GET`` requests to capture/status endpoints.

        :returns: None
        """
        if not self._authenticated():
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            self._dispatch_get(parsed.path, qs)
        except UartError as exc:
            self.send_json({"error": str(exc)}, 409)
        except ValueError as exc:
            self.send_json({"error": f"Invalid request: {exc}"}, 400)
        except Exception:  # noqa: BLE001 - never close the connection without JSON
            self.send_json({"error": "Internal server error"}, 500)

    def _dispatch_get(self, path: str, qs: dict[str, list[str]]) -> None:
        """Route a ``GET`` request to the matching handler.

        :param path: Request path.
        :param qs: Parsed query string.
        :returns: None
        :raises UartError: When the session is missing or the port is closed.
        :raises ValueError: When a parameter is malformed or the path is unknown.
        """
        if path == "/status":
            self.send_json(self._session().status())
            return

        if path == "/devices":
            self.send_json(describe_devices())
            return

        if path == "/logs":
            session = self._session()
            payload = build_logs_payload(
                session,
                since_seq=_query_int(qs, "since_seq"),
                window_s=_query_float(qs, "window_s"),
                max_lines=_query_int(qs, "max_lines", DEFAULT_MAX_LINES) or DEFAULT_MAX_LINES,
                grep=qs.get("grep", [None])[0],
            )
            self.send_json(payload)
            return

        if path == "/wait":
            session = self._session()
            session.require_open()
            pattern = qs.get("pattern", [None])[0]
            if not pattern:
                raise ValueError("'pattern' is required")
            result = session.wait(
                pattern,
                since_seq=_query_int(qs, "since_seq"),
                timeout_s=_query_float(qs, "timeout_s", DEFAULT_EXPECT_TIMEOUT_S) or DEFAULT_EXPECT_TIMEOUT_S,
            )
            self.send_json(result, 200 if result["matched"] else 504)
            return

        raise ValueError(f"unknown endpoint {path!r}")

    # -- POST --

    def _drain_body(self, content_length: int) -> None:
        """Consume an unread request body before replying with an error.

        Answering while the body is still in flight leaves unread bytes on the
        socket. The connection then resets — and the client loses the response it
        was meant to read — or the leftovers are parsed as the next request.

        :param content_length: Declared body length in bytes.
        :returns: None
        """
        if content_length <= 0:
            return
        if content_length > MAX_REQUEST_BODY_BYTES:
            # Too large to read purely to discard; drop the connection instead.
            self.close_connection = True
            return
        try:
            self.rfile.read(content_length)
        except OSError:
            self.close_connection = True

    def _reject_cross_origin(self, content_length: int) -> bool:
        """Reject a POST that a browser could have been tricked into sending.

        Every endpoint here is unauthenticated, so any page the user visits could
        otherwise drive the attached hardware — writing to a device shell or
        resetting the board — via a simple cross-origin form/``fetch`` POST.
        Requiring a JSON content type forces such a request into a CORS preflight,
        which this server does not answer, while normal API clients are unaffected.

        :param content_length: Declared body length, drained before replying.
        :returns: ``True`` when a rejection response has already been sent.
        """
        media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media_type != "application/json":
            self._drain_body(content_length)
            self.send_json(
                {
                    "error": (
                        "POST requires the header 'Content-Type: application/json'. "
                        f"Got {media_type or 'no content type'}."
                    )
                },
                415,
            )
            return True
        return False

    def do_POST(self) -> None:
        """Authenticate, parse JSON, and dispatch a ``POST`` endpoint.

        :returns: None
        """
        if not self._authenticated():
            self.close_connection = True
            return
        parsed = urlparse(self.path)
        try:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            # The body cannot be drained without a length, so end the connection.
            self.close_connection = True
            self.send_json({"error": "Invalid Content-Length header"}, 400)
            return
        if content_length < 0:
            self.close_connection = True
            self.send_json({"error": "Invalid Content-Length header"}, 400)
            return
        if self._reject_cross_origin(content_length):
            return
        if content_length > MAX_REQUEST_BODY_BYTES:
            self._drain_body(content_length)
            self.send_json({"error": f"Request body too large (> {MAX_REQUEST_BODY_BYTES} bytes)"}, 413)
            return

        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            self.send_json({"error": f"Malformed JSON body: {exc}"}, 400)
            return
        if not isinstance(data, dict):
            self.send_json({"error": "Request body must be a JSON object"}, 400)
            return

        try:
            self._dispatch_post(parsed.path, data)
        except UartError as exc:
            self.send_json({"error": str(exc)}, 409)
        except (TypeError, ValueError, KeyError) as exc:
            self.send_json({"error": f"Invalid request: {exc}"}, 400)
        except Exception:  # noqa: BLE001 - never close the connection without JSON
            self.send_json({"error": "Internal server error"}, 500)

    def _dispatch_post(self, path: str, data: dict[str, Any]) -> None:
        """Route a validated ``POST`` body to the matching handler.

        :param path: Request path.
        :param data: Parsed JSON body.
        :returns: None
        :raises UartError: When the session is missing or in the wrong state.
        :raises ValueError: When the body is malformed or the path is unknown.
        """
        if path == "/shutdown":
            self.send_json({"status": "stopping"})
            if http_server is not None:
                threading.Thread(target=http_server.shutdown, daemon=True).start()
            return

        session = self._session()

        if path == "/open":
            if "port" in data:
                session.port = str(data["port"])
            if "baud" in data:
                session.baud = int(data["baud"])
            session.dtr = _json_bool(data, "dtr", session.dtr)
            session.rts = _json_bool(data, "rts", session.rts)
            session.open()
            self.send_json(session.status())
            return

        if path == "/close":
            session.close()
            self.send_json(session.status())
            return

        if path == "/clear":
            next_seq = session.ring.clear()
            self.send_json({"status": "cleared", "next_seq": next_seq})
            return

        if path == "/send":
            text = data.get("text")
            if text is None:
                raise ValueError("'text' is required")
            result = session.send(
                str(text),
                newline=str(data.get("newline", DEFAULT_NEWLINE)),
                expect=data.get("expect"),
                timeout_s=float(data.get("timeout_s", DEFAULT_EXPECT_TIMEOUT_S)),
            )
            # An unmet `expect` is a timeout, not a bad request — and the captured
            # lines still ship so the caller can see what did come back.
            self.send_json(result, 504 if result["matched"] is False else 200)
            return

        if path == "/reset":
            self.send_json(self._handle_reset(session, data))
            return

        if path in ("/dtr", "/rts"):
            self.send_json(session.set_line(path.lstrip("/"), _json_bool(data, "on", True)))
            return

        raise ValueError(f"unknown endpoint {path!r}")

    def _handle_reset(self, session: UartSession, data: dict[str, Any]) -> dict[str, Any]:
        """Clear the capture buffer and reset the target, in that order.

        Clearing first means the boot log starts at sequence 0 with no race
        against the reset completing.

        :param session: The active session.
        :param data: Parsed request body.
        :returns: The reset result plus the new cursor.
        :raises UartError: When ``nrfutil`` is unavailable or the reset fails.
        """
        next_seq = session.ring.clear() if _json_bool(data, "clear_buffer", True) else session.ring.next_seq

        args = ["reset"]
        serial_number = data.get("serial_number")
        if serial_number:
            args += ["--serial-number", str(serial_number)]
        reset_kind = data.get("reset_kind")
        if reset_kind:
            args += ["--reset-kind", str(reset_kind)]

        try:
            result = nrfutil_device(args)
        except (OSError, subprocess.SubprocessError) as exc:
            raise UartError(f"nrfutil device reset failed: {exc}") from exc

        return {
            "status": "reset" if result.get("returncode") == 0 else "reset-failed",
            "next_seq": next_seq,
            "returncode": result.get("returncode"),
            "stdout": result.get("stdout", "").strip(),
            "stderr": result.get("stderr", "").strip(),
        }


# --- CLI commands ---


def cmd_list(_args: argparse.Namespace) -> None:
    """Print serial ports and their Nordic device correlation.

    :param _args: Parsed command-line arguments (unused).
    :returns: None
    """
    try:
        print(json.dumps(describe_devices(), indent=2))
    except UartError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_read(args: argparse.Namespace) -> None:
    """Read from a port and print lines to stdout until the duration elapses.

    A serverless convenience for one-shot captures. Anything interactive or
    incremental should use ``serve``.

    :param args: Parsed command-line arguments.
    :returns: None
    """
    try:
        ser = open_serial(args.port, args.baud, dtr=args.dtr, rts=args.rts)
    except UartError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    assembler = LineAssembler(ansi=not args.no_strip_ansi)
    deadline = time.monotonic() + args.duration if args.duration else None
    print(
        f"[UART] Reading {args.port} @ {args.baud} baud"
        + (f" for {args.duration}s" if args.duration else " (Ctrl+C to stop)"),
        file=sys.stderr,
    )
    try:
        while deadline is None or time.monotonic() < deadline:
            chunk = ser.read(ser.in_waiting or 1)
            for text in assembler.feed(chunk):
                print(text, flush=True)
    except KeyboardInterrupt:
        pass
    except _SERIAL_ERRORS as exc:
        print(f"[UART error] {exc}", file=sys.stderr)
    finally:
        pending = assembler.take_partial()
        if pending:
            print(pending, flush=True)
        ser.close()
        print("[UART] Done.", file=sys.stderr)


def cmd_write(args: argparse.Namespace) -> None:
    """Write a single string to a port and exit.

    :param args: Parsed command-line arguments.
    :returns: None
    """
    try:
        ser = open_serial(args.port, args.baud, dtr=args.dtr, rts=args.rts)
    except UartError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        payload = (args.message + args.newline).encode("utf-8")
        ser.write(payload)
        ser.flush()
        print(f"[UART] Sent {len(payload)} bytes to {args.port}.", file=sys.stderr)
    except _SERIAL_ERRORS as exc:
        print(f"Error: write failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        ser.close()


def cmd_serve(args: argparse.Namespace) -> None:
    """Run the HTTP session server, optionally opening the port immediately.

    :param args: Parsed command-line arguments.
    :returns: None
    """
    global server_session, http_server, server_response_limit, server_spill_dir, server_token

    if not _is_loopback(args.host):
        if not args.allow_non_loopback:
            print("Error: non-loopback binding requires --allow-non-loopback", file=sys.stderr)
            raise SystemExit(2)
        print(
            "WARNING: UART is binding to a non-loopback address; network clients can reach the authenticated API.",
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
            print(f"Error: a UART server is already active on port {args.port_num}", file=sys.stderr)
            raise SystemExit(1)
        _remove_state_if_owned(state_path, previous["token"])

    server_response_limit = args.max_response_chars
    server_spill_dir = args.spill_dir or os.path.join(tempfile.gettempdir(), SPILL_SUBDIR)

    server_session = UartSession(
        args.port,
        args.baud,
        dtr=args.dtr,
        rts=args.rts,
        ring_lines=args.buffer_lines,
        ansi=not args.no_strip_ansi,
        log_file=args.log_file,
    )

    # Bind before acquiring the port: if the HTTP port is taken we must not have
    # locked the VCOM, and the try/finally below releases it on any later failure.
    # Threading keeps /status and /shutdown responsive while /wait blocks.
    http_server = ThreadingHTTPServer((args.host, args.port_num), UartRequestHandler)
    http_server.daemon_threads = True
    server_token = secrets.token_urlsafe(32)
    record_path: Optional[Path] = None

    def sigterm_handler(_signum: int, _frame: Any) -> None:
        """Shut down the HTTP server gracefully on SIGTERM.

        :param _signum: Signal number received (unused).
        :param _frame: Current stack frame (unused).
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
        if not args.no_open:
            try:
                server_session.open()
                print(f"Opened {args.port} @ {args.baud} baud (DTR={args.dtr}, RTS={args.rts}).")
            except UartError as exc:
                # Keep the server up so /status explains the failure and the
                # caller can retry with POST /open after freeing the port.
                print(f"Error opening port: {exc}", file=sys.stderr)
                server_session.error = str(exc)

        # Readiness token consumed by the skill (grep -m1 "ready").
        print(f"UART HTTP server ready on {args.host}:{args.port_num}", flush=True)

        # Enable the live stdout echo only after the readiness token so a captured
        # line can never reach stdout before it.
        if args.echo:
            server_session.echo = True
            print(f"Echoing live UART output from {args.port} below (use --no-echo to disable):", flush=True)

        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if server_session is not None:
            server_session.close()
        http_server.server_close()
        if record_path is not None and server_token is not None:
            _remove_state_if_owned(record_path, server_token)


def _explicit_or_state_token(args: argparse.Namespace) -> tuple[str, str, int]:
    """Resolve a CLI token and endpoint from explicit input or server state.

    :param args: Parsed command-line arguments.
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
    if explicit:
        return explicit, args.host, args.port_num
    record = _read_state(args.port_num)
    if not _state_is_live(record):
        raise RuntimeError(f"Stale UART server state found for port {args.port_num}")
    return record["token"], record["host"], record["port"]


def cmd_token(args: argparse.Namespace) -> None:
    """Print the token for a verified active UART server.

    :param args: Parsed command-line arguments.
    :returns: None
    """
    try:
        record = _read_state(args.port_num)
        if not _state_is_live(record):
            raise RuntimeError(f"Stale UART server state found for port {args.port_num}")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(record, indent=2))
    else:
        print(record["token"])


def cmd_status(args: argparse.Namespace) -> None:
    """Print the status of a running UART HTTP server.

    :param args: Parsed command-line arguments.
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
        print(f"No UART server running on {args.host}:{args.port_num}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop a running UART HTTP server via its ``/shutdown`` endpoint.

    :param args: Parsed command-line arguments.
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
        print(f"No UART server running on {args.host}:{args.port_num}", file=sys.stderr)
        raise SystemExit(1) from exc


def _add_line_state_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared DTR/RTS flags to *parser*.

    :param parser: Parser to extend.
    :returns: None
    """
    parser.add_argument(
        "--dtr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Assert DTR (default: on; Nordic DK UART lines stay tri-stated without it)",
    )
    parser.add_argument("--rts", action=argparse.BooleanOptionalAction, default=True, help="Assert RTS (default: on)")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI.

    :returns: The configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(description="Nordic UART/VCOM CLI and HTTP session server")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List serial ports and correlate them with Nordic devices")

    p_serve = subparsers.add_parser("serve", help="Start the UART HTTP session server")
    p_serve.add_argument("--port", required=True, help="Serial port, e.g. /dev/ttyACM0 or COM3")
    p_serve.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    p_serve.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_serve.add_argument("--port-num", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_serve.add_argument(
        "--allow-non-loopback", action="store_true", help="Acknowledge the risk of a network-reachable bind"
    )
    _add_line_state_args(p_serve)
    p_serve.add_argument(
        "--buffer-lines", type=int, default=DEFAULT_RING_LINES, help="Lines retained in the ring buffer"
    )
    p_serve.add_argument("--log-file", help="Append every captured line to this file")
    p_serve.add_argument(
        "--echo",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print every captured line to stdout as it arrives (default: on)",
    )
    p_serve.add_argument("--no-strip-ansi", action="store_true", help="Keep ANSI escape sequences in captured lines")
    p_serve.add_argument("--no-open", action="store_true", help="Start the server without opening the port")
    p_serve.add_argument(
        "--max-response-chars",
        type=int,
        default=DEFAULT_MAX_RESPONSE_CHARS,
        help="Spill responses larger than this to a file (0 disables)",
    )
    p_serve.add_argument("--spill-dir", help="Directory for spilled responses (default: system temp)")

    p_status = subparsers.add_parser("status", help="Get the status of a running HTTP server")
    p_status.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_status.add_argument("--port-num", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_status.add_argument("--token", help="Explicit process token")
    p_status.add_argument("--token-file", help="Read the process token from a file")

    p_stop = subparsers.add_parser("stop", help="Stop a running HTTP server")
    p_stop.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p_stop.add_argument("--port-num", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_stop.add_argument("--token", help="Explicit process token")
    p_stop.add_argument("--token-file", help="Read the process token from a file")

    p_token = subparsers.add_parser("token", help="Print the active server token")
    p_token.add_argument("--port-num", type=int, default=DEFAULT_HTTP_PORT, help="HTTP port")
    p_token.add_argument("--json", action="store_true", help="Print the complete state record as JSON")

    p_read = subparsers.add_parser("read", help="One-shot read from a port (no server)")
    p_read.add_argument("--port", required=True, help="Serial port")
    p_read.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    p_read.add_argument("--duration", type=float, metavar="SECONDS", help="Stop after N seconds")
    p_read.add_argument("--no-strip-ansi", action="store_true", help="Keep ANSI escape sequences")
    _add_line_state_args(p_read)

    p_write = subparsers.add_parser("write", help="One-shot write to a port (no server)")
    p_write.add_argument("--port", required=True, help="Serial port")
    p_write.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    p_write.add_argument("--message", required=True, help="String to send")
    p_write.add_argument(
        "--newline", default=DEFAULT_NEWLINE, help=r"Line ending appended to the message (default: \r\n)"
    )
    _add_line_state_args(p_write)

    return parser


def main() -> None:
    """Parse command-line arguments and dispatch to the requested subcommand.

    :returns: None
    """
    args = build_parser().parse_args()

    handlers: dict[str, Callable[[argparse.Namespace], None]] = {
        "list": cmd_list,
        "serve": cmd_serve,
        "status": cmd_status,
        "stop": cmd_stop,
        "token": cmd_token,
        "read": cmd_read,
        "write": cmd_write,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
