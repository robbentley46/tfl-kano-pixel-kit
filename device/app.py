"""
Standalone DLR departure board for a single stop point (see config.py).

main.py is just a stub that imports this and calls main() - deploy.sh
precompiles this file to app.mpy on the host, because compiling it on
the board grows MicroPython's heap into the memory TLS handshakes need
(see net.py's docstring). boot.py has already connected to wifi by the
time this runs.

Display: big MM countdown to the next train in minutes (top 5 rows),
coloured red/amber/green by urgency, with a 2-row colour bar
underneath showing which destination it's headed to (colour picked
automatically per destination name, not hardcoded, so no station
names need to live in this file). Cycles through the next few
departures. Flashes when a train is due in under a minute.

If LATITUDE/LONGITUDE are set in config.py, each pass through the
departures is followed by two weather screens (Open-Meteo, see
weather.py): an icon plus the current temperature, then today's low
(blue) and high (amber) above a 16-column rain strip - one column per
hour from now, brighter blue for a higher chance of rain.

Row 5 (the gap between the digits and the destination bar) doubles as
a status strip: a lit pixel in the bottom-left corner means wifi is
currently down and a reconnect is being attempted; a lit pixel in the
bottom-right corner means the displayed data is stale (no successful
refresh in a while) even if it's still on screen.

When an API's data can't be shown because fetching it is failing, its
turn in the cycle goes red with a short white code saying why: the
HTTP status (e.g. 503) if the API answered, otherwise a mnemonic for
the step that failed (dnS, Con, SSL, MEM, ...) - see the README's
table. The bottom two rows of the red screen are a source bar saying
which API failed: teal for TfL, yellow for weather. Each API only ever
takes over its own turn (see cycle_screens), so a working one keeps
showing its screens either side of the other's error. Weather only
goes red once there's no recent cached weather left to show (until
then the cached weather stays up with the stale pixel lit). A
successful fetch that simply has no departures in it isn't an error,
so it gets a calm dim "--" on black instead of the red screen.

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

A hardware watchdog reboots the board if the main loop stops running
for WATCHDOG_SECONDS. The socket timeouts in net.py should stop a fetch
blocking forever, but a TLS handshake has been seen to hang past them
on real hardware when short of memory (see net.py), and a reboot gets
the board back within seconds. Once started, the ESP32 watchdog can't
be stopped, so interrupting the app over USB (REPL, mpremote run) also reboots the board after that
long - set WATCHDOG = False in config.py for a long debugging session.
"""
import gc
import time
import esp32
import machine
import network
import tfl
import weather
import icons
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
RETRY_INTERVAL_SECONDS = 15  # min gap between fetch attempts, so failures don't hammer the API
DISPLAY_SECONDS = 3
MAX_DEPARTURES_SHOWN = 3
RECONNECT_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 300  # fall back to "no data" if nothing refreshes this long
NTP_RETRY_INTERVAL_SECONDS = 60          # until the first successful sync
NTP_RESYNC_INTERVAL_SECONDS = 6 * 60 * 60  # periodic re-sync after that
WEATHER_REFRESH_SECONDS = 30 * 60
WEATHER_RETRY_SECONDS = 5 * 60
WEATHER_STALE_AFTER_SECONDS = 3 * 60 * 60  # stop showing cached weather after this
RAIN_MIN_PERCENT = 20  # rain strip ignores chances below this, to cut noise
WATCHDOG_SECONDS = 120  # well past the slowest healthy fetch (a few seconds)
MEM_REBOOT_MIN_UPTIME_SECONDS = 10 * 60

URGENT_THRESHOLD_MIN = 7   # below this: red
SOON_THRESHOLD_MIN = 8     # at or below this (but not urgent): amber, above: green

URGENT_COLOR = [10, 0, 0]    # red
SOON_COLOR = [10, 6, 0]      # amber
CLEAR_COLOR = [0, 10, 0]     # green
DEFAULT_DEST_COLOR = [8, 8, 8]
ERROR_BG_COLOR = [10, 0, 0]
ERROR_TEXT_COLOR = [10, 10, 10]
NO_DEPARTURES_COLOR = [4, 4, 4]
TFL_SOURCE_COLOR = [0, 8, 8]        # error screen source bar: TfL failed
WEATHER_SOURCE_COLOR = [10, 10, 0]  # error screen source bar: weather failed

WEATHER_TEMP_COLOR = [8, 8, 8]
LOW_TEMP_COLOR = [0, 4, 10]   # blue
HIGH_TEMP_COLOR = [10, 6, 0]  # amber

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


def heap_stats():
    """One-line summary of both heaps for the serial log. TLS handshakes
    allocate from the ESP-IDF heap (not the MicroPython GC heap), so its
    largest free block is the number to watch when SSL errors appear."""
    gc.collect()
    idf = esp32.idf_heap_info(esp32.HEAP_DATA)
    return 'uptime={}s gc_free={} idf_free={} idf_max_block={}'.format(
        time.ticks_ms() // 1000, gc.mem_free(),
        sum(region[1] for region in idf), max(region[2] for region in idf))


def reboot_if_out_of_memory(error):
    """A 'MEM' failure means the heap is too full or fragmented for a
    TLS handshake, and MicroPython never hands grown heap back, so only
    a reboot clears it. Not within MEM_REBOOT_MIN_UPTIME_SECONDS of
    boot, so a board that's short of memory from the start shows the
    error instead of reboot-looping."""
    if error == 'MEM' and time.ticks_ms() > MEM_REBOOT_MIN_UPTIME_SECONDS * 1000:
        print('out of memory for fetches, rebooting for a fresh heap')
        machine.reset()


def fetch_departures():
    """Return (departures, None) on success, or (None, error_code)."""
    gc.collect()
    try:
        data = tfl.get_departures(STOP_ID)
    except tfl.FetchError as e:
        print('tfl fetch failed:', e.code, e.detail, heap_stats())
        return None, e.code
    except Exception as e:
        print('tfl fetch failed:', repr(e), heap_stats())
        return None, 'Err'
    data.sort(key=lambda d: d['timeToStation'])
    return data, None


def fetch_weather():
    """Return (weather, None) on success, or (None, error_code)."""
    gc.collect()
    try:
        return weather.get_weather(config.LATITUDE, config.LONGITUDE), None
    except weather.FetchError as e:
        print('weather fetch failed:', e.code, e.detail, heap_stats())
        return None, e.code
    except Exception as e:
        print('weather fetch failed:', repr(e), heap_stats())
        return None, 'Err'


def urgency_color(minutes):
    if minutes < URGENT_THRESHOLD_MIN:
        return URGENT_COLOR
    if minutes <= SOON_THRESHOLD_MIN:
        return SOON_COLOR
    return CLEAR_COLOR


def text_width(text):
    return len(text) * 4 - 1


def draw_text(text, color):
    """Draw up to 3 glyphs on rows 0-4, horizontally centred."""
    draw_glyphs(text, (kit.WIDTH - text_width(text)) // 2, color)


def draw_glyphs(text, x, color):
    """Draw glyphs on rows 0-4 starting at column x."""
    for ch in text:
        glyph = font.GLYPHS[ch]
        for row in range(5):
            for col in range(3):
                if glyph[row][col] == '1':
                    kit.set_pixel(x + col, row, color)
        x += 4


def draw_countdown(minutes, dest_color, blink_on):
    kit.clear()
    minutes = max(0, min(minutes, 99))
    digit_color = urgency_color(minutes) if blink_on else [0, 0, 0]
    draw_text('{:02d}'.format(minutes), digit_color)
    for col in range(16):
        kit.set_pixel(col, 6, dest_color)
        kit.set_pixel(col, 7, dest_color)


def draw_error(code, source_color):
    """Red screen with the error code, plus a 2-row bar along the
    bottom in source_color saying which API it came from."""
    kit.set_background(ERROR_BG_COLOR)
    if code:
        draw_text(code, ERROR_TEXT_COLOR)
    for col in range(16):
        kit.set_pixel(col, 6, source_color)
        kit.set_pixel(col, 7, source_color)


def temp_text(degrees):
    # Clamped so it always fits in 2 glyphs ('-' counts as one).
    return str(max(-9, min(degrees, 99)))


def draw_icon(icon, x, y):
    for row, line in enumerate(icon):
        for col, ch in enumerate(line):
            if ch != '.':
                kit.set_pixel(x + col, y + row, icons.COLORS[ch])


def draw_weather_now(w):
    """Icon on the left, current temperature + degree pixel on the right."""
    kit.clear()
    draw_icon(icons.ICONS[weather.icon_for(w['code'], w['is_day'])], 0, 0)
    text = temp_text(w['temp'])
    draw_glyphs(text, 16 - len(text) * 4, WEATHER_TEMP_COLOR)
    kit.set_pixel(15, 0, WEATHER_TEMP_COLOR)


def draw_weather_today(w):
    """Low (left half) and high (right half), each centred in 7 columns,
    with the hourly rain strip on rows 6-7."""
    kit.clear()
    for text, x, color in (
        (temp_text(w['low']), 0, LOW_TEMP_COLOR),
        (temp_text(w['high']), 9, HIGH_TEMP_COLOR),
    ):
        draw_glyphs(text, x + (7 - text_width(text)) // 2, color)
    for col, chance in enumerate(w['rain'][:16]):
        if chance >= RAIN_MIN_PERCENT:
            rain_color = [0, 0, max(1, chance * 10 // 100)]
            kit.set_pixel(col, 6, rain_color)
            kit.set_pixel(col, 7, rain_color)


def draw_no_departures():
    kit.clear()
    draw_text('--', NO_DEPARTURES_COLOR)


# Screens in the display cycle that aren't a departure - departures
# are their index into the departures list instead.
SCREEN_TFL_ERROR = 'tfl_error'
SCREEN_NO_DEPARTURES = 'no_departures'
SCREEN_WEATHER_NOW = 'weather_now'
SCREEN_WEATHER_TODAY = 'weather_today'
SCREEN_WEATHER_ERROR = 'weather_error'


def cycle_screens(departures, stale, weather_usable, weather_error):
    """The screens to cycle through, DISPLAY_SECONDS each. TfL and
    weather each always get their own turn, so when one API is failing
    its error screen only fills that API's turn and the other keeps
    showing its data either side of it."""
    if departures and not stale:
        screens = list(range(min(len(departures), MAX_DEPARTURES_SHOWN)))
    elif departures == [] and not stale:
        screens = [SCREEN_NO_DEPARTURES]
    else:
        screens = [SCREEN_TFL_ERROR]
    if weather_usable:
        screens += [SCREEN_WEATHER_NOW, SCREEN_WEATHER_TODAY]
    elif weather_error is not None:
        screens.append(SCREEN_WEATHER_ERROR)  # a single error screen in place of both
    return screens


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


def _last_sunday_1am_utc(year, month):
    # Only used for March and October, both of which have 31 days.
    weekday = time.localtime(time.mktime((year, month, 31, 1, 0, 0, 0, 0)))[6]  # Mon=0
    return time.mktime((year, month, 31 - (weekday + 1) % 7, 1, 0, 0, 0, 0))


def summer_time(utc_seconds):
    """UK/EU summer time: from 01:00 UTC on the last Sunday of March
    until 01:00 UTC on the last Sunday of October. The RTC holds UTC,
    so localtime()/mktime() here are effectively gmtime()/timegm()."""
    year = time.localtime(utc_seconds)[0]
    return _last_sunday_1am_utc(year, 3) <= utc_seconds < _last_sunday_1am_utc(year, 10)


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
    now = time.time()
    offset_hours = getattr(config, 'UTC_OFFSET_HOURS', 0)
    if getattr(config, 'AUTO_DST', False) and summer_time(now):
        offset_hours += 1
    hour = time.localtime(now + offset_hours * 3600)[3]
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps past midnight


def main():
    sta = network.WLAN(network.STA_IF)

    departures = None
    last_fetch = time.ticks_ms()
    last_switch = time.ticks_ms()
    screen_index = 0  # position in cycle_screens()' list
    last_reconnect_attempt = time.ticks_ms() - RECONNECT_INTERVAL_SECONDS * 1000
    last_time_sync = time.ticks_ms() - NTP_RETRY_INTERVAL_SECONDS * 1000
    time_synced = False
    last_error = None
    last_attempt = time.ticks_ms() - RETRY_INTERVAL_SECONDS * 1000
    weather_enabled = getattr(config, 'LATITUDE', None) is not None
    weather_data = None
    weather_error = None
    last_weather_fetch = time.ticks_ms()
    last_weather_attempt = time.ticks_ms() - WEATHER_RETRY_SECONDS * 1000
    watchdog = None
    if getattr(config, 'WATCHDOG', True):
        watchdog = machine.WDT(timeout=WATCHDOG_SECONDS * 1000)

    while True:
        if watchdog is not None:
            watchdog.feed()
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

        # last_fetch only advances on success, so without the last_attempt
        # gate a failing fetch would be retried on every loop pass.
        due_refresh = (
            active
            and (departures is None or time.ticks_diff(now, last_fetch) > REFRESH_SECONDS * 1000)
            and time.ticks_diff(now, last_attempt) > RETRY_INTERVAL_SECONDS * 1000
        )
        if due_refresh:
            if sta.isconnected():
                last_attempt = now
                new_data, error = fetch_departures()
                if new_data is not None:
                    departures = new_data
                    last_fetch = now
                    last_error = None
                else:
                    last_error = error
                    reboot_if_out_of_memory(error)
            else:
                last_error = 'wiF'
        # elif, so a weather fetch never adds to the render freeze of a
        # TfL fetch in the same pass.
        elif weather_enabled and active and sta.isconnected() and (
            (weather_data is None or time.ticks_diff(now, last_weather_fetch) > WEATHER_REFRESH_SECONDS * 1000)
            and time.ticks_diff(now, last_weather_attempt) > WEATHER_RETRY_SECONDS * 1000
        ):
            last_weather_attempt = now
            new_weather, error = fetch_weather()
            if new_weather is not None:
                weather_data = new_weather
                last_weather_fetch = now
                weather_error = None
            else:
                weather_error = error
                reboot_if_out_of_memory(error)

        stale = departures is not None and time.ticks_diff(now, last_fetch) > STALE_AFTER_SECONDS * 1000

        weather_usable = (
            weather_data is not None
            and time.ticks_diff(now, last_weather_fetch) < WEATHER_STALE_AFTER_SECONDS * 1000
        )

        if active:
            screens = cycle_screens(departures, stale, weather_usable, weather_error)
            if time.ticks_diff(now, last_switch) > DISPLAY_SECONDS * 1000:
                screen_index += 1
                last_switch = now
            if screen_index >= len(screens):
                screen_index = 0
            screen = screens[screen_index]

            wifi_down = not sta.isconnected()
            if screen == SCREEN_TFL_ERROR:
                draw_error(last_error, TFL_SOURCE_COLOR)
                draw_status_icons(wifi_down=wifi_down, stale=stale)
            elif screen == SCREEN_NO_DEPARTURES:
                draw_no_departures()
                draw_status_icons(wifi_down=wifi_down, stale=stale)
            elif screen == SCREEN_WEATHER_ERROR:
                draw_error(weather_error, WEATHER_SOURCE_COLOR)
            elif screen in (SCREEN_WEATHER_NOW, SCREEN_WEATHER_TODAY):
                if screen == SCREEN_WEATHER_NOW:
                    draw_weather_now(weather_data)
                else:
                    draw_weather_today(weather_data)
                # No wifi pixel - it would land on the icon. The stale
                # pixel here means the last weather refresh failed and
                # this is cached data.
                draw_status_icons(wifi_down=False, stale=weather_error is not None)
            else:
                dep = departures[screen]
                elapsed = time.ticks_diff(now, last_fetch) / 1000.0
                remaining = max(0, dep['timeToStation'] - elapsed)
                minutes = int(remaining // 60)

                blink_on = True
                if remaining < 60:
                    blink_on = (now // 500) % 2 == 0

                dest_color = dest_color_for(dep['destinationName'])
                draw_countdown(minutes, dest_color, blink_on)
                draw_status_icons(wifi_down=wifi_down, stale=stale)
        else:
            kit.clear()

        kit.render()

        time.sleep_ms(100)
