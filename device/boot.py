"""
Runs automatically on every boot, before main.py. Connects to WiFi
using wifi.py (SSID/PASSWORD), which you write directly onto the
device over the REPL - see the README. No AP/config-portal fallback
here (that was Kano's stock firmware's job); if wifi.py is missing or
wrong, this just prints why and main.py's own isconnected() check
keeps it from trying to fetch anything until it's fixed.
"""
import network
import time

def connect():
    try:
        import wifi
    except ImportError:
        print('No wifi.py found - see README for how to create it over the REPL.')
        return

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        print('Connecting to', wifi.SSID)
        sta.connect(wifi.SSID, wifi.PASSWORD)
        timeout = 100  # ~10 seconds
        while not sta.isconnected() and timeout > 0:
            time.sleep_ms(100)
            timeout -= 1

    if sta.isconnected():
        print('Connected:', sta.ifconfig())
    else:
        print('Could not connect to wifi - check wifi.py credentials.')

connect()
