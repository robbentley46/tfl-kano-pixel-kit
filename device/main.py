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
"""
import time
import network
import tfl
import font
import config
import PixelKit as kit

STOP_ID = config.STOP_ID
REFRESH_SECONDS = 30
DISPLAY_SECONDS = 5
MAX_DEPARTURES_SHOWN = 3

URGENT_COLOR = [10, 0, 0]    # < 7 min: red
SOON_COLOR = [10, 6, 0]      # 7-8 min: amber
CLEAR_COLOR = [0, 10, 0]     # > 8 min: green
DEFAULT_DEST_COLOR = [8, 8, 8]
NO_DATA_COLOR = [10, 0, 0]

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
    if minutes < 7:
        return URGENT_COLOR
    if minutes <= 8:
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
    kit.render()


def draw_no_data():
    kit.set_background(NO_DATA_COLOR)
    kit.render()


def main():
    sta = network.WLAN(network.STA_IF)

    departures = None
    last_fetch = time.ticks_ms()
    last_switch = time.ticks_ms()
    current_index = 0

    while True:
        now = time.ticks_ms()

        due_refresh = departures is None or time.ticks_diff(now, last_fetch) > REFRESH_SECONDS * 1000
        if due_refresh and sta.isconnected():
            new_data = fetch_departures()
            if new_data is not None:
                departures = new_data
                last_fetch = now
                current_index = 0

        if departures:
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

        time.sleep_ms(100)


main()
