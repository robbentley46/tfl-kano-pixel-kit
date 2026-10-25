#!/usr/bin/env bash
# Copies the device files onto a connected Pixel Kit over USB via
# mpremote and resets it. Covers README steps 2 and 5 (the parts you
# repeat every time code changes) - not the one-off firmware flash or
# the wifi.py REPL step, which deliberately never touches disk.
#
# Modules are precompiled to .mpy with mpy-cross first: compiling them
# on the board grows MicroPython's heap into the memory its TLS
# handshakes need (see device/app.py's docstring). boot.py, main.py
# (a stub) and config.py stay as source - they're tiny, and the board
# only runs boot.py/main.py by those exact names.
#
# Usage:
#   ./deploy.sh         full deploy: all device files, then reset
#   ./deploy.sh app     quick redeploy: app.mpy only, then reset
#
# Optional: PORT=/dev/cu.usbserial-XXXX ./deploy.sh  (only needed if
# mpremote can't auto-detect a single connected device)
set -euo pipefail
cd "$(dirname "$0")"

for tool in mpremote mpy-cross; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool not found on PATH - activate the venv first (source .venv/bin/activate)" >&2
        exit 1
    fi
done

if [ ! -f device/config.py ]; then
    echo "error: device/config.py not found - copy device/config.py.example to device/config.py and fill in your stop ID first" >&2
    exit 1
fi

mpremote_cmd=(mpremote)
if [ -n "${PORT:-}" ]; then
    mpremote_cmd=(mpremote connect "$PORT")
fi

compiled_modules=(PixelKit net tfl weather font icons app)
source_files=(boot.py config.py wifi_test.py main.py)

mode="${1:-full}"
case "$mode" in
    full)
        modules=("${compiled_modules[@]}")
        files=("${source_files[@]}")
        ;;
    app)
        modules=(app)
        files=()
        ;;
    *)
        echo "usage: $0 [full|app]" >&2
        exit 1
        ;;
esac

mkdir -p build
for m in "${modules[@]}"; do
    echo "-> $m.mpy"
    mpy-cross -o "build/$m.mpy" "device/$m.py"
    "${mpremote_cmd[@]}" cp "build/$m.mpy" ":$m.mpy"
done

# MicroPython imports foo.py ahead of foo.mpy, so a .py left over from
# before modules were precompiled would shadow the fresh .mpy.
stale_sources=$(printf "'%s.py'," "${modules[@]}")
echo "-> removing any stale .py copies of compiled modules"
"${mpremote_cmd[@]}" exec "
import os
for f in ($stale_sources):
    try:
        os.remove(f)
    except OSError:
        pass
"

for f in ${files[@]+"${files[@]}"}; do
    echo "-> $f"
    "${mpremote_cmd[@]}" cp "device/$f" ":$f"
done

echo "-> reset"
"${mpremote_cmd[@]}" reset

echo "Deployed ($mode). Device is running standalone."
