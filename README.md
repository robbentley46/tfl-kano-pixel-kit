# DLR departure board (Kano Pixel Kit)

Standalone Kano Pixel Kit app: connects to your WiFi, polls the TfL API
directly (no host computer involved once running), and shows a big
countdown-in-minutes to the next DLR departure from your local stop,
colour-coded by destination (colour picked automatically per
destination name - see `dest_color_for` in `main.py`), cycling through
the next few trains.

The stop point is set in `device/config.py` (gitignored, not committed
- see step 2) rather than hardcoded, so forking or sharing this repo
doesn't reveal which station it's pointed at.

## 0. Host setup

This repo's `.tool-versions` pins Python 3.13.2 via asdf - run `asdf
install` in this directory if you don't already have that version.

The only host-side Python dependencies are two CLI tools (`esptool`,
`mpremote`); nothing in `device/` runs on your laptop, it all gets
copied onto the Pixel Kit. Keep them in a project-local virtualenv
rather than installing globally:

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Re-run `source .venv/bin/activate` in new terminal sessions before
using `esptool`/`mpremote` below.

## 1. Flash official MicroPython (not the Kano flash tool)

The community "Kano Pixel Kit flash tool" GUI is a single-maintainer,
unsigned, effectively abandoned project (last release Nov 2018) - not
something worth trusting with full USB/device access. Its actual job
is just writing a MicroPython firmware image via `esptool`, which we
can do directly with the official, actively-maintained tool instead
(already installed in the venv above).

Download the latest **ESP32_GENERIC** firmware `.bin` from the
official MicroPython site: https://micropython.org/download/ESP32_GENERIC/
(this is the generic ESP32-WROOM-32 build - no Kano-specific firmware
needed, since `PixelKit.py` is just a plain Python file, see step 2)
into this repo's `firmware/` directory (gitignored - it's a downloaded
binary artifact, not project source).

With the Pixel Kit connected over USB:

```
esptool.py --chip esp32 --port <port> erase_flash
esptool.py --chip esp32 --port <port> --baud 460800 write_flash 0x1000 firmware/ESP32_GENERIC-<date>.bin
```

(`<port>` is something like `/dev/cu.usbserial-XXXX` on macOS. If
`esptool.py` isn't found, try `esptool` - the command name has varied
across versions.)

## 2. Set your stop point and copy the device files

Copy `device/config.py.example` to `device/config.py` and fill in your
own stop ID (the example file explains how to find it via the TfL API).
`device/config.py` is gitignored - it never gets committed.

With the Pixel Kit connected over USB:

```
./deploy.sh
```

(This runs `mpremote cp` for each file in `device/` - except
`wifi.py`, see step 3 - and resets the device. If mpremote can't
auto-detect your device, set `PORT=/dev/cu.usbserial-XXXX` first.)

`PixelKit.py` here is vendored from the Kano community's
[pixel32](https://github.com/murilopolese/kano-pixel-kit-pixel32) repo
(one small, fully human-readable file, not a compiled blob) with one
bug fixed - see the comment at the top of `device/PixelKit.py`.
`boot.py` is ours: a minimal WiFi-connect script, since we're not
using Kano's own firmware/boot.py anymore.

## 3. Set your WiFi credentials directly on the device

`boot.py` reads a `wifi.py` file on every boot to connect. Write that
file straight onto the device's flash over a serial REPL, rather than
creating it as a file on your laptop first - that way the plaintext
password never touches your laptop's disk at all, only the device's.

This is over the USB cable, not WiFi - you don't need to join any
hotspot first.

```
mpremote connect <port> repl
```

Then, at the device's live prompt, type this yourself with your real
values (paste it in your own terminal, not into a chat with anyone):

```python
f = open('wifi.py', 'w')
f.write('SSID = "yournetwork"\n')
f.write('PASSWORD = "yourpassword"\n')
f.close()
```

Exit the REPL with Ctrl-] (or Ctrl-D), then `mpremote reset` so
`boot.py` picks it up. `device/wifi.py.example` in this repo is just a
reference for the format `boot.py` expects - it's not meant to be
filled in and copied across.

## 4. Prove out networking before the full app

This is the step that actually matters - it confirms your specific
unit can do a standalone HTTPS request before you invest in anything
else:

```
mpremote run device/wifi_test.py
```

You should see `Connected: (...)` followed by a handful of
`<seconds>s -> <destination>` lines. If it fails, see the SNI note at
the top of `tfl.py` - that's the most likely culprit on this chip.

## 5. Deploy the full app

```
./deploy.sh
```

(Once `wifi.py` and everything else is already on the device, and
you're only iterating on `main.py`, `./deploy.sh main` skips the rest
and just pushes that one file.)

The device now runs standalone: on every power-up/reset it connects to
WiFi via `boot.py`, then `main.py` takes over the display permanently.
No laptop, phone, or other device needs to be on the network for it to
keep working.

## Notes

- There's no AP/config-portal fallback in this setup (that was Kano's
  own firmware's job, and we're not using it). If WiFi credentials
  need to change, redo step 3 over USB.
- Colours: confirmed on real hardware to be standard `[R, G, B]` order.
  All colour constants in `main.py` (`URGENT_COLOR`, `SOON_COLOR`,
  `CLEAR_COLOR`, `DEST_COLOR_PALETTE`, etc.) are in that order.
- The direction-filter in `tfl.py` (see its comments) was tuned against
  one real terminus's quirks; if your stop shows a wrong/missing
  departure, compare its raw fields (`mpremote run device/wifi_test.py`
  vs a manual `curl` of the TfL endpoint) the same way this repo's
  history did.
- `REFRESH_SECONDS` (30s) and `DISPLAY_SECONDS` (5s per departure) in
  `main.py` are easy to tune.
- The board can go dark outside a daily window instead of running
  24/7 - set `ACTIVE_START_HOUR`/`ACTIVE_END_HOUR` (and `UTC_OFFSET_HOURS`
  for DST) in `device/config.py` (see `config.py.example`). This
  needs the device's clock synced via NTP, which `boot.py` attempts
  once at connect and `main.py` retries/re-syncs on its own; until
  that first sync succeeds the window check fails open (stays active)
  rather than risk going dark for good on a guess.
- WiFi drops and stale/no-data states show as small status pixels on
  row 5 of the display (between the digits and the destination bar) -
  see `draw_status_icons` in `main.py`.
