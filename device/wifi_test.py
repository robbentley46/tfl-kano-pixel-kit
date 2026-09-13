"""
Run this FIRST, before deploying main.py, to prove the device can do a
standalone HTTPS request to the TfL API. boot.py connects to wifi before
this script would run, so it just double-checks and gets straight to it.

    mpremote run wifi_test.py

If this fails with a TLS/handshake error, that's the classic ESP32
MicroPython HTTPS weak spot - see the note in tfl.py about SNI.
"""
import time
import network
import tfl
import config

STOP_ID = config.STOP_ID

sta = network.WLAN(network.STA_IF)
timeout = 100
while not sta.isconnected() and timeout > 0:
    time.sleep_ms(100)
    timeout -= 1

if not sta.isconnected():
    print('Not connected to wifi - check wifi.py has the right SSID/PASSWORD')
else:
    print('Connected:', sta.ifconfig())
    try:
        departures = tfl.get_departures(STOP_ID)
        print('Got {} departures'.format(len(departures)))
        for d in departures[:5]:
            print('{}s -> {}'.format(d['timeToStation'], d['destinationName']))
    except Exception as e:
        print('Request failed:', e)
