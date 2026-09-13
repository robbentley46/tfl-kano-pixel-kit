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

ACTIVE_START_HOUR/ACTIVE_END_HOUR in config.py (optional) restrict
fetching and lighting up the display to a daily local-time window -
e.g. so it doesn't glow or poll the API overnight. The device has no
battery-backed RTC, so telling local time requires an NTP sync over
wifi (boot.py does one best-effort sync; this file retries/re-syncs
periodically in case that failed or the RTC has drifted). Until a
sync succeeds, or if no window is configured, the window check fails
open (always active) rather than risk going dark for good on a guess.
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

try:
    import ntptime
except ImportError:
    ntptime = None

STOP_ID = config.STOP_ID
REFRESH_SECONDS = 30
DISPLAY_SECONDS = 5
MAX_DEPARTURES_SHOWN = 3
RECONNECT_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 300  # fall back to "no data" if nothing refreshes this long
NTP_RETRY_INTERVAL_SECONDS = 60          # until the first successful sync
NTP_RESYNC_INTERVAL_SECONDS = 6 * 60 * 60  # periodic re-sync after that

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


def sync_time():
    if ntptime is None:
        return False
    try:
        ntptime.settime()  # sets the RTC to UTC
        return True
    except Exception as e:
        print('ntp sync failed:', e)
        return False


def in_active_window(time_synced):
    """Whether the display should be fetching/showing data right now.
    Fails open (always active) if no window is configured, or if we
    don't yet know the real local time - going dark indefinitely on a
    guess would be worse than occasionally lighting up when it needn't."""
    start = getattr(config, 'ACTIVE_START_HOUR', None)
    end = getattr(config, 'ACTIVE_END_HOUR', None)
    if start is None or end is None or start == end:
        return True
    if not time_synced:
        return True
    offset_seconds = getattr(config, 'UTC_OFFSET_HOURS', 0) * 3600
    hour = time.localtime(time.time() + offset_seconds)[3]
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps past midnight


def main():
    sta = network.WLAN(network.STA_IF)

    departures = None
    last_fetch = time.ticks_ms()
    last_switch = time.ticks_ms()
    current_index = 0
    last_reconnect_attempt = time.ticks_ms() - RECONNECT_INTERVAL_SECONDS * 1000
    last_time_sync = time.ticks_ms() - NTP_RETRY_INTERVAL_SECONDS * 1000
    time_synced = False

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

        sync_interval = NTP_RESYNC_INTERVAL_SECONDS if time_synced else NTP_RETRY_INTERVAL_SECONDS
        due_sync = time.ticks_diff(now, last_time_sync) > sync_interval * 1000
        if sta.isconnected() and due_sync:
            if sync_time():
                time_synced = True
            last_time_sync = now

        active = in_active_window(time_synced)

        due_refresh = active and (departures is None or time.ticks_diff(now, last_fetch) > REFRESH_SECONDS * 1000)
        if due_refresh and sta.isconnected():
            new_data = fetch_departures()
            if new_data is not None:
                departures = new_data
                last_fetch = now
                current_index = 0

        stale = departures is not None and time.ticks_diff(now, last_fetch) > STALE_AFTER_SECONDS * 1000

        if active:
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
        else:
            kit.clear()

        kit.render()

        time.sleep_ms(100)


main()
