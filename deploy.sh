#!/usr/bin/env bash
# Copies device/*.py onto a connected Pixel Kit over USB via mpremote
# and resets it. Covers README steps 2 and 5 (the parts you repeat
# every time code changes) - not the one-off firmware flash or the
# wifi.py REPL step, which deliberately never touches disk.
#
# Usage:
#   ./deploy.sh         full deploy: all device files, then reset
#   ./deploy.sh main    quick redeploy: main.py only, then reset
#
# Optional: PORT=/dev/cu.usbserial-XXXX ./deploy.sh  (only needed if
# mpremote can't auto-detect a single connected device)
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v mpremote >/dev/null 2>&1; then
    echo "error: mpremote not found on PATH - activate the venv first (source .venv/bin/activate)" >&2
    exit 1
fi

if [ ! -f device/config.py ]; then
    echo "error: device/config.py not found - copy device/config.py.example to device/config.py and fill in your stop ID first" >&2
    exit 1
fi

mpremote_cmd=(mpremote)
if [ -n "${PORT:-}" ]; then
    mpremote_cmd=(mpremote connect "$PORT")
fi

full_files=(boot.py PixelKit.py tfl.py font.py config.py wifi_test.py main.py)

mode="${1:-full}"
case "$mode" in
    full)
        files=("${full_files[@]}")
        ;;
    main)
        files=(main.py)
        ;;
    *)
        echo "usage: $0 [full|main]" >&2
        exit 1
        ;;
esac

for f in "${files[@]}"; do
    echo "-> $f"
    "${mpremote_cmd[@]}" cp "device/$f" ":$f"
done

echo "-> reset"
"${mpremote_cmd[@]}" reset

echo "Deployed ($mode). Device is running standalone."
