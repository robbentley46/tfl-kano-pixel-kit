"""
Minimal HTTPS GET-a-JSON-document shared by tfl.py and weather.py,
written against raw usocket/ussl instead of urequests: this Kano
MicroPython fork ships neither urequests nor a manifest confirming
which stdlib modules are frozen in, so a hand-rolled GET keeps the
dependency footprint (and therefore the things that can go wrong) to
just usocket/ussl.

HTTP/1.0 + "Connection: close" is used deliberately so the response
is terminated by the server closing the socket instead of chunked
transfer-encoding, which is simpler to parse with no library. No
Accept-Encoding header is sent, so replies also come back uncompressed.

The body is parsed straight off the socket rather than buffered first.
A buffered body needs one large contiguous allocation, and when the
fragmented MicroPython heap can't find one it grows by taking most of
the largest free block of the ESP-IDF heap - which is where mbedTLS
allocates its handshake buffers. That growth is only handed back once
everything in it is freed, so in practice every handshake from then on
fails ('SSL', 'MEM', or a handshake that hangs) until a reboot.

The FetchError codes are host-agnostic (they name the stage that
failed, not the API), so the caller is what knows which API it was -
app.py uses that to colour the error screen's source bar.
"""
try:
    import usocket as socket
except ImportError:
    import socket

try:
    import uerrno as errno
except ImportError:
    import errno

try:
    import ussl
except ImportError:
    # Newer MicroPython dropped the u-prefixed alias in favour of `ssl`.
    import ssl as ussl

try:
    import ujson
except ImportError:
    import json as ujson

try:
    import esp32
except ImportError:
    esp32 = None

# A TLS handshake failing while the ESP-IDF heap's largest free block is
# smaller than this is down to memory, whatever error mbedTLS raised for
# it (often not an *_ALLOC_FAILED one - EBUSY or a timeout are common).
# A healthy board has ~50KB+; one whose heap has grown has ~23KB.
TLS_MIN_FREE_BLOCK = 32 * 1024


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


def _out_of_memory(e):
    return (
        isinstance(e, MemoryError)
        or getattr(e, 'errno', None) == errno.ENOMEM
        or 'ALLOC_FAILED' in repr(e)
    )


def _tls_heap_starved():
    if esp32 is None:
        return False
    largest = max(region[2] for region in esp32.idf_heap_info(esp32.HEAP_DATA))
    return largest < TLS_MIN_FREE_BLOCK


def _stage_error(code, e):
    """FetchError for exception `e` from stage `code` - except that
    running out of memory is tagged 'MEM' whichever stage it hit, rather
    than a misleading 'SSL' or 'rd'."""
    if _out_of_memory(e) or (code == 'SSL' and _tls_heap_starved()):
        code = 'MEM'
    return FetchError(code, repr(e))


def https_get_json(host, path, user_agent='pixelkit-dlr', port=443, timeout=10):
    """Return the parsed JSON body of a 200 reply. Raises FetchError
    tagged with the stage that failed, so a DNS failure, a refused
    connection and a TLS problem stay distinguishable instead of all
    becoming OSError - or with the HTTP status if it wasn't 200."""
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
    except Exception as e:
        raise _stage_error('dnS', e)
    # Can fail with ENFILE if the socket pool is exhausted - reported as
    # part of the connect stage.
    try:
        s = socket.socket()
    except Exception as e:
        raise _stage_error('Con', e)
    try:
        s.settimeout(timeout)
        try:
            s.connect(addr)
        except Exception as e:
            raise _stage_error('Con', e)
        # server_hostname enables SNI - required by most modern HTTPS hosts,
        # TfL's API included. If your firmware's ussl predates SNI support
        # this raises a TypeError; drop the kwarg and expect the handshake
        # to then fail for real, since the host serves multiple certs.
        try:
            s = ussl.wrap_socket(s, server_hostname=host)
        except Exception as e:
            raise _stage_error('SSL', e)
        request = (
            'GET {} HTTP/1.0\r\n'
            'Host: {}\r\n'
            'Connection: close\r\n'
            'User-Agent: {}\r\n'
            '\r\n'
        ).format(path, host, user_agent)
        try:
            s.write(request.encode())
            status_line = s.readline()
            while s.readline() not in (b'\r\n', b''):
                pass  # skip the rest of the headers
        except Exception as e:
            raise _stage_error('rd', e)
        try:
            status = int(status_line.split()[1])
        except (IndexError, ValueError):
            raise FetchError('bAd', repr(status_line))
        if not 100 <= status <= 999:  # must fit the 3-digit display
            raise FetchError('bAd', repr(status_line))
        if status != 200:
            try:
                detail = s.read(200)
            except Exception:
                detail = b''
            raise FetchError(str(status), detail)
        try:
            return ujson.load(s)
        except ValueError as e:
            raise FetchError('JSn', repr(e))
        except Exception as e:
            # Timeout/reset partway through the body.
            raise _stage_error('rd', e)
    finally:
        # Always release the socket - leaking one per failed attempt
        # would eventually exhaust the ESP32's small socket pool and
        # turn a transient outage into a permanent one.
        s.close()
