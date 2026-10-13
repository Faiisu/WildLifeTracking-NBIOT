#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$WORKSPACE_DIR/myprojects/gps_mqtt_tracker_temp"
BOARD="${BOARD:-sparkfun_thing_plus/nrf9160/ns}"
BUILD_DIR="${BUILD_DIR:-$APP_DIR/build-gnss-baseline}"
NCS_ROOT="${NCS_ROOT:-/Volumes/Mac_storage/opt/nordic/ncs/v3.3.0}"
TOOLCHAIN_BUNDLE="${TOOLCHAIN_BUNDLE:-/Volumes/Mac_storage/opt/nordic/ncs/toolchains/0c0f19d91c}"
JLINK_SERIAL_NUMBER="${JLINK_SERIAL_NUMBER:-802002444}"
JLINK_DLL="${JLINK_DLL:-/Applications/SEGGER/JLink/libjlinkarm.dylib}"
SWD_CLOCK_FREQUENCY="${SWD_CLOCK_FREQUENCY:-100}"
NRFUTIL="${NRFUTIL:-$TOOLCHAIN_BUNDLE/nrfutil/bin/nrfutil}"
export NRFUTIL_HOME="${NRFUTIL_HOME:-$TOOLCHAIN_BUNDLE/nrfutil/home}"

case "$BOARD" in
  sparkfun_thing_plus/nrf9160/ns|circuitdojo_feather/nrf9160/ns) ;;
  *) echo "Unsupported board: $BOARD" >&2; exit 1 ;;
esac

if [[ ! -x "$TOOLCHAIN_BUNDLE/bin/west" || ! -f "$NCS_ROOT/.west/config" ]]; then
  echo "NCS v3.3.0 toolchain not found; set NCS_ROOT and TOOLCHAIN_BUNDLE." >&2
  exit 1
fi
if [[ ! -x "$NRFUTIL" || ! -f "$JLINK_DLL" ]]; then
  echo "nrfutil or SEGGER J-Link DLL not found; set NRFUTIL and JLINK_DLL." >&2
  exit 1
fi

export PATH="$TOOLCHAIN_BUNDLE/bin:$TOOLCHAIN_BUNDLE/opt/zephyr-sdk/arm-zephyr-eabi/bin:$PATH"
export ZEPHYR_BASE="$NCS_ROOT/zephyr"
export ZEPHYR_TOOLCHAIN_VARIANT=zephyr
export ZEPHYR_SDK_INSTALL_DIR="$TOOLCHAIN_BUNDLE/opt/zephyr-sdk"
export TOOLCHAIN_ROOT="$ZEPHYR_BASE"

cd "$NCS_ROOT"
west build -p always -d "$BUILD_DIR" -b "$BOARD" "$APP_DIR" --sysbuild

FIRMWARE="$BUILD_DIR/merged.hex"
[[ -f "$FIRMWARE" ]] || { echo "Build did not produce $FIRMWARE" >&2; exit 1; }

echo "Close the active J-Link/VS Code debug session and power the target board before programming."
echo "Programming $BOARD at $SWD_CLOCK_FREQUENCY kHz; erasing only firmware memory ranges."
if ! "$NRFUTIL" device program \
  --firmware "$FIRMWARE" \
  --serial-number "$JLINK_SERIAL_NUMBER" \
  --family nrf91 \
  --swd-clock-frequency "$SWD_CLOCK_FREQUENCY" \
  --options chip_erase_mode=ERASE_RANGES_TOUCHED_BY_FIRMWARE,reset=RESET_SYSTEM,verify=VERIFY_READ \
  --jlink-dll "$JLINK_DLL"; then
  echo "Flash failed. Check target power, SWD GND/VTref/RESET, and whether another session owns J-Link." >&2
  exit 1
fi

echo "Flash and verification completed. Reconnect RTT on up channel 0 to read GNSS PVT/NMEA."
