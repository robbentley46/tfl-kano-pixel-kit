"""
Standalone DLR departure board for a single stop point (see config.py).

Replaces the stock pixel32 main.py (which serves the browser code
editor) - this runs on its own with no host computer, no browser IDE.
boot.py has already connected to wifi by the time this runs.

Display: big MM countdown to the next train in minutes (top 5 rows),
coloured red/amber/green by urgency, with a 2-row colour bar
underneath showing which destination it's headed to (colour picked
automatically per destination name, not hardcoded, so no station
names need to live in this file). Cycles through the next few
departures. Flashes when a train is due in under a minute.

Row 5 (the gap between the digits and the destination bar) doubles as
a status strip: a lit pixel in the bottom-left corner means wifi is
currently down and a reconnect is being attempted; a lit pixel in the
bottom-right corner means the displayed data is stale (no successful
refresh in a while) even if it's still on screen.

boot.py only tries wifi once, with no retry, so this file owns
reconnecting for the lifetime of the run - it kicks off a fresh
non-blocking sta.connect() every RECONNECT_INTERVAL_SECONDS while
disconnected, rather than blocking the render loop the way boot.py's
one-shot connect does.
"""
import time
import network
import tfl
import font
import config
import PixelKit as kit

try:
    import wifi
except ImportError:
    wifi = None

STOP_ID = config.STOP_ID
REFRESH_SECONDS = 30
DISPLAY_SECONDS = 5
MAX_DEPARTURES_SHOWN = 3
RECONNECT_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 300  # fall back to "no data" if nothing refreshes this long

URGENT_THRESHOLD_MIN = 7   # below this: red
SOON_THRESHOLD_MIN = 8     # at or below this (but not urgent): amber, above: green

URGENT_COLOR = [10, 0, 0]    # red
SOON_COLOR = [10, 6, 0]      # amber
CLEAR_COLOR = [0, 10, 0]     # green
DEFAULT_DEST_COLOR = [8, 8, 8]
NO_DATA_COLOR = [10, 0, 0]

WIFI_DOWN_ICON_COLOR = [10, 6, 0]  # amber, matches SOON_COLOR's "warning" tone
STALE_ICON_COLOR = [6, 6, 6]       # dim white, deliberately distinct from urgency colours

DEST_COLOR_PALETTE = [
    [0, 10, 0],   # green
    [0, 0, 10],   # blue
    [8, 0, 8],    # purple
    [10, 10, 0],  # yellow
    [0, 10, 10],  # cyan
]


def dest_color_for(name):
    if not name:
        return DEFAULT_DEST_COLOR
    index = sum(ord(c) for c in name) % len(DEST_COLOR_PALETTE)
    return DEST_COLOR_PALETTE[index]


def fetch_departures():
    try:
        data = tfl.get_departures(STOP_ID)
        data.sort(key=lambda d: d['timeToStation'])
        return data
    except Exception as e:
        print('fetch failed:', e)
        return None


def urgency_color(minutes):
    if minutes < URGENT_THRESHOLD_MIN:
        return URGENT_COLOR
    if minutes <= SOON_THRESHOLD_MIN:
        return SOON_COLOR
    return CLEAR_COLOR


def draw_countdown(minutes, dest_color, blink_on):
    kit.clear()
    minutes = max(0, min(minutes, 99))
    text = '{:02d}'.format(minutes)
    digit_color = urgency_color(minutes) if blink_on else [0, 0, 0]
    x = 4
    for ch in text:
        glyph = font.DIGITS[ch]
        for row in range(5):
            for col in range(3):
                if glyph[row][col] == '1':
                    kit.set_pixel(x + col, row, digit_color)
        x += 4
    for col in range(16):
        kit.set_pixel(col, 6, dest_color)
        kit.set_pixel(col, 7, dest_color)


def draw_no_data():
    kit.set_background(NO_DATA_COLOR)


def draw_status_icons(wifi_down, stale):
    """Overlay small status pixels on row 5 - call after the main
    screen is drawn but before kit.render(), so they show up on top
    of either the countdown or the no-data screen."""
    if wifi_down:
        kit.set_pixel(0, 5, WIFI_DOWN_ICON_COLOR)
        kit.set_pixel(1, 5, WIFI_DOWN_ICON_COLOR)
    if stale:
        kit.set_pixel(14, 5, STALE_ICON_COLOR)
        kit.set_pixel(15, 5, STALE_ICON_COLOR)


def main():
    sta = network.WLAN(network.STA_IF)

    departures = None
    last_fetch = time.ticks_ms()
    last_switch = time.ticks_ms()
    current_index = 0
    last_reconnect_attempt = time.ticks_ms() - RECONNECT_INTERVAL_SECONDS * 1000

    while True:
        now = time.ticks_ms()

        if not sta.isconnected():
            due_reconnect = time.ticks_diff(now, last_reconnect_attempt) > RECONNECT_INTERVAL_SECONDS * 1000
            if wifi is not None and due_reconnect:
                print('wifi down, reconnecting...')
                try:
                    # Non-blocking on ESP32 - the connection proceeds in the
                    # background, unlike boot.py's one-shot blocking connect.
                    sta.connect(wifi.SSID, wifi.PASSWORD)
                except OSError as e:
                    print('reconnect attempt failed:', e)
                last_reconnect_attempt = now

        due_refresh = departures is None or time.ticks_diff(now, last_fetch) > REFRESH_SECONDS * 1000
        if due_refresh and sta.isconnected():
            new_data = fetch_departures()
            if new_data is not None:
                departures = new_data
                last_fetch = now
                current_index = 0

        stale = departures is not None and time.ticks_diff(now, last_fetch) > STALE_AFTER_SECONDS * 1000

        if departures and not stale:
            if time.ticks_diff(now, last_switch) > DISPLAY_SECONDS * 1000:
                shown = min(len(departures), MAX_DEPARTURES_SHOWN)
                current_index = (current_index + 1) % shown
                last_switch = now

            dep = departures[current_index]
            elapsed = time.ticks_diff(now, last_fetch) / 1000.0
            remaining = max(0, dep['timeToStation'] - elapsed)
            minutes = int(remaining // 60)

            blink_on = True
            if remaining < 60:
                blink_on = (now // 500) % 2 == 0

            dest_color = dest_color_for(dep['destinationName'])
            draw_countdown(minutes, dest_color, blink_on)
        else:
            draw_no_data()

        draw_status_icons(wifi_down=not sta.isconnected(), stale=stale)
        kit.render()

        time.sleep_ms(100)


main()
