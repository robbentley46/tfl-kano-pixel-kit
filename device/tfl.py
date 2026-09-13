"""
Minimal HTTPS client for the TfL Unified API, written against raw
usocket/ussl instead of urequests: this Kano MicroPython fork ships
neither urequests nor a manifest confirming which stdlib modules are
frozen in, so a hand-rolled GET keeps the dependency footprint (and
therefore the things that can go wrong) to just usocket/ussl/ujson.

HTTP/1.0 + "Connection: close" is used deliberately so the response
is terminated by the server closing the socket instead of chunked
transfer-encoding, which is simpler to parse with no library.
"""
try:
    import usocket as socket
except ImportError:
    import socket

try:
    import ussl
except ImportError:
    # Newer MicroPython dropped the u-prefixed alias in favour of `ssl`.
    import ssl as ussl

try:
    import ujson
except ImportError:
    import json as ujson

HOST = 'api.tfl.gov.uk'
PATH = '/Line/dlr/Arrivals/{}'


def _https_get(host, path, port=443, timeout=10):
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)
    s.connect(addr)
    # server_hostname enables SNI - required by most modern HTTPS hosts,
    # TfL's API included. If your firmware's ussl predates SNI support
    # this raises a TypeError; drop the kwarg and expect the handshake
    # to then fail for real, since the host serves multiple certs.
    s = ussl.wrap_socket(s, server_hostname=host)
    request = (
        'GET {} HTTP/1.0\r\n'
        'Host: {}\r\n'
        'Connection: close\r\n'
        'User-Agent: pixelkit-dlr\r\n'
        '\r\n'
    ).format(path, host)
    s.write(request.encode())
    response = b''
    while True:
        chunk = s.read(512)
        if not chunk:
            break
        response += chunk
    s.close()
    header, _, body = response.partition(b'\r\n\r\n')
    return header, body


def get_departures(stop_id):
    """Return [{'timeToStation': seconds, 'destinationName': str}, ...]"""
    header, body = _https_get(HOST, PATH.format(stop_id))
    status_line = header.split(b'\r\n', 1)[0]
    if b'200' not in status_line:
        raise Exception('HTTP error: {}'.format(status_line))
    predictions = ujson.loads(body)
    return [
        {
            'timeToStation': p['timeToStation'],
            'destinationName': p.get('destinationName', ''),
        }
        for p in predictions
        # TfL's "arrival" prediction for a train already dwelling at a
        # terminus platform lingers at ~0 rather than counting down to
        # a real future departure - exclude it so it doesn't show as a
        # phantom extra departure.
        if p['timeToStation'] > 0
        # At some termini, a turned-back-service's prediction comes back
        # with an empty `direction` instead of "outbound" - confirmed
        # against real departures at this stop, these aren't genuine
        # upcoming trains. If your stop shows a real missing direction
        # value that isn't "outbound" (e.g. "inbound" is valid there),
        # this filter needs adjusting.
        and p.get('direction') == 'outbound'
    ]
