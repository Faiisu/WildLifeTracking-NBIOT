# NCS GDB Troubleshooting

Read this only after the `gdb` skill's basic checks (target connected and
powered, `CONFIG_DEBUG_OPTIMIZATIONS=y` build, `west`/`nrfutil`/
`arm-zephyr-eabi-gdb` on `PATH`) failed to resolve the problem. This skill
covers deeper failure modes: probe/connection failures, runner selection, GDB
Python support, missing symbols, and multi-core session confusion.

## Source-of-truth order

For any device/probe/SoC specifics, prefer `nordicsemi_search_sources` over
guessing — recovery details are hardware-revision-specific and evolve over
time. Do not fetch Nordic documentation from the web directly.

## Step 1 — Diagnose by symptom

| Symptom | Likely cause | What to do |
|---|---|---|
| GDB server never binds; `west debugserver` exits immediately | J-Link software missing, or probe not found | Confirm SEGGER J-Link tools are installed and `nrfutil device list --traits jlink` sees the device |
| GDB connects but breakpoints stay "pending" / no source / bogus backtrace | Symbol / ELF-path mismatch (wrong or stale ELF, sysbuild app under `build/<app>/zephyr/`), or a size-optimized build | First confirm the loaded ELF matches the running image (resolve the sysbuild ELF path or pass `--elf`). `CONFIG_DEBUG_OPTIMIZATIONS=y` (`-Og`) and `CONFIG_DEBUG_THREAD_INFO=y` improve stepping/locals but are not what create the symbol table |
| "Python scripting is not supported in this copy of GDB" | GDB built without Python | Use a `-py` GDB build, or the Zephyr SDK `arm-zephyr-eabi-gdb`, which includes Python |
| `west debug` uses the wrong program (e.g. pyocd) | Default runner mismatch | Force the J-Link runner: `--runner jlink` (see Step 2) |
| "Probe is already in use" / cannot open J-Link | Another session/tool holds the probe lock | Stop the other debugger/flasher; stop a stale `gdb_server.py` with its `stop` command |
| Device not detected on any port/cable/computer | Cabling, hub, or probe fault | Try a different cable and a direct USB port (avoid hubs); try another computer |
| Multi-core: attach lands on the wrong core, or state resets on reset | Core/reset-kind mismatch | See Step 3 (multi-core) |
| Session connects but every GDB command hangs/times out | GUI `JLinkGDBServer.exe` popped a modal dialog (e.g. "Target connection lost") that blocks the server | Use the headless `JLinkGDBServerCL` (`west debugserver --runner jlink -- --gdbserver JLinkGDBServerCL`); the `gdb` skill does this by default |
| `gdb_server.py` crashes at startup under `uv` inside the toolchain env. POSIX: `AssertionError: SRE module mismatch` or `ModuleNotFoundError: No module named 'west'` in an `import argparse`/`import re` traceback. Windows: `Module use of pythonXY.dll conflicts...` | `nrfutil ... toolchain launch` exports `PYTHONHOME`/`PYTHONPATH` pointing at *its* bundled Python; they leak into the interpreter `uv run` starts and corrupt its stdlib imports. The script's own env-clean covers only the `west` child it spawns, not the interpreter running the script — the crash is during that interpreter's own import chain, before any script code runs | Clear the vars in the launching shell *before* `uv run` (not just before `west`): bash `unset PYTHONHOME PYTHONPATH VIRTUAL_ENV`; cmd `set PYTHONHOME=&& set PYTHONPATH=&& set VIRTUAL_ENV=`; PowerShell `Remove-Item Env:PYTHONHOME,Env:PYTHONPATH,Env:VIRTUAL_ENV -ErrorAction Ignore`. Alternatively run `uv run` *outside* `toolchain launch` so they are never set |
| `west: unknown command "debugserver"` | Zephyr extension commands are workspace-bound and `west` ran outside the workspace | Run from / pass the NCS workspace topdir (`--west-topdir`, or `--chdir`) |
| A breakpoint on `main` never fires; CPU sits in the idle thread | `main` already ran before the breakpoint was set; tickless idle means nothing executes | Set the breakpoint then `monitor reset halt` + continue, or break on a repeatedly-executed function; note some targets do not hold at the reset vector on `monitor reset halt` |
| New sessions fail with `vMustReplyEmpty: timeout` / probe "in use"; a `JLinkGDBServerCL` lingers on the GDB port | A previous `-singlerun` J-Link server wedged (USB hang) and still holds the probe; killing `west` does not kill its child | The `gdb` skill now tree-kills the server on shutdown. For an already-wedged one that `taskkill`/`Stop-Process` cannot end, unplug/replug the DK USB (or power-cycle) to force USB re-enumeration, then retry |
| No UART/console/log output over the DK's VCOM (0 bytes), yet RTT works and the COM port opens fine | The console UART's virtual COM port is disabled in the board controller (the SoC UART is electrically disconnected from the debugger). On the nRF54L15 DK the default console is `uart20` -> VCOM1, which may be off | Enable it via the board controller (e.g. the `board-controller` skill: `set --pin vcom1=on`), or in nRF Connect Board Configurator. RTT over SWD is unaffected and a good fallback |

## Step 2 — Runner and connection checks

`west` chooses a flash/debug **runner**. On Nordic DKs the J-Link runner is
correct; if another is selected by default, force it:

```bash
west debug --runner jlink
west debugserver --runner jlink
```

Inspect the runners and their options your board supports (the capital `-H`
re-runs the build to print up-to-date info):

```bash
west debug -H            # list available runners for the board
west debug -H -r jlink   # J-Link runner options (ports, device name, ...)
```

If you prefer to run the pieces separately, start the server in one place and
connect the client to it (this is what `--no-west` in the `gdb` skill assumes):

```bash
west debugserver --runner jlink            # opens a GDB server (default :2331)
arm-zephyr-eabi-gdb build/zephyr/zephyr.elf # in another shell
(gdb) target remote :2331
(gdb) monitor reset halt
```

## Step 3 — Core out of sync / multi-core (nRF54H, nRF53)

GDB can lose sync with the target after a reset. Re-halt and re-sync before
stepping:

```text
(gdb) monitor reset halt
(gdb) monitor gdb_sync
(gdb) stepi
```

On multi-core devices, each core needs its **own** debug session, and a
domain/local reset can trigger a global reset that drops the debug connection.
To debug early boot, keep the cores halted while attaching, then release them
individually. Resolve the exact reset kind and core names via
`nordicsemi_search_sources`; a typical nRF54H early-boot sequence is:

```bash
nrfutil device reset --reset-kind RESET_VIA_SECDOM
nrfutil device go --core Network
west attach
```

## Step 4 — Probe not detected at all (suspected fault or bricked probe)

1. Rule out the obvious: different USB cable, a direct USB port (avoid hubs and
   docking stations), and a different computer.
2. Confirm whether the OS sees it at all (Device Manager / `lsusb` / macOS
   System Information) and whether `nrfutil device list` sees it.
3. If a Nordic DK's onboard J-Link OB is suspected out-of-date or corrupted,
   the debug-probe firmware can be updated with
   `nrfutil device x-update-debug-probe-firmware`. Confirm the exact procedure
   for the specific board via `nordicsemi_search_sources` before running it.
4. If it is invisible even to the OS, this is a lower-level hardware/recovery
   case. Do not guess a fix — query `nordicsemi_search_sources` with the exact
   symptom, and if no recovery path is found, direct the user to a private
   support ticket via Nordic DevZone (<https://devzone.nordicsemi.com>).

## Reference

- For anything not covered here (new/unknown symptoms, hardware-revision
  specifics, recovery procedures), prefer `nordicsemi_search_sources` over
  guessing — it retrieves current DevZone Q&A and documentation.
