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


class FetchError(Exception):
    """A failed fetch, tagged with a short `code` for the display - the
    HTTP status (e.g. '503') if the server answered, otherwise a
    mnemonic for the stage that failed (see the README's error code
    table) - plus free-text `detail` (errno, raw status line, etc.)
    for the serial log."""

    def __init__(self, code, detail=''):
        # super() rather than Exception.__init__(self, ...) - MicroPython's
        # built-in Exception type doesn't expose __init__ as an attribute.
        super().__init__(code, detail)
        self.code = code
        self.detail = detail


def _https_get(host, path, port=443, timeout=10):
    """Return (status_code, body). Raises FetchError tagged with the
    stage that failed, so a DNS failure, a refused connection and a
    TLS problem stay distinguishable instead of all becoming OSError."""
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
    except Exception as e:
        raise FetchError('dnS', repr(e))
    # Can fail with ENFILE/ENOMEM if the socket pool or heap is exhausted -
    # reported as part of the connect stage.
    try:
        s = socket.socket()
    except Exception as e:
        raise FetchError('Con', repr(e))
    try:
        s.settimeout(timeout)
        try:
            s.connect(addr)
        except Exception as e:
            raise FetchError('Con', repr(e))
        # server_hostname enables SNI - required by most modern HTTPS hosts,
        # TfL's API included. If your firmware's ussl predates SNI support
        # this raises a TypeError; drop the kwarg and expect the handshake
        # to then fail for real, since the host serves multiple certs.
        try:
            s = ussl.wrap_socket(s, server_hostname=host)
        except Exception as e:
            raise FetchError('SSL', repr(e))
        request = (
            'GET {} HTTP/1.0\r\n'
            'Host: {}\r\n'
            'Connection: close\r\n'
            'User-Agent: pixelkit-dlr\r\n'
            '\r\n'
        ).format(path, host)
        try:
            s.write(request.encode())
            response = b''
            while True:
                chunk = s.read(512)
                if not chunk:
                    break
                response += chunk
            # Split here rather than after the finally, so a MemoryError
            # copying a large body out of the buffer is reported as 'rd'
            # instead of escaping as an untagged exception.
            header, _, body = response.partition(b'\r\n\r\n')
        except Exception as e:
            raise FetchError('rd', repr(e))
    finally:
        # Always release the socket - leaking one per failed attempt
        # would eventually exhaust the ESP32's small socket pool and
        # turn a transient outage into a permanent one.
        s.close()
    status_line = header.split(b'\r\n', 1)[0]
    try:
        status = int(status_line.split()[1])
    except (IndexError, ValueError):
        raise FetchError('bAd', repr(status_line))
    if not 100 <= status <= 999:  # must fit the 3-digit display
        raise FetchError('bAd', repr(status_line))
    return status, body


def get_departures(stop_id):
    """Return [{'timeToStation': seconds, 'destinationName': str}, ...]
    or raise FetchError."""
    status, body = _https_get(HOST, PATH.format(stop_id))
    if status != 200:
        raise FetchError(str(status), body[:200])
    try:
        return _parse_departures(body)
    except Exception as e:
        raise FetchError('JSn', repr(e))


def _parse_departures(body):
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
