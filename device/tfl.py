"""
TfL Unified API client - fetches and filters DLR arrivals for one stop.
The HTTPS plumbing (and FetchError, with its stage codes) lives in
net.py, shared with weather.py; FetchError is re-exported here so
callers can keep catching tfl.FetchError.
"""
try:
    import ujson
except ImportError:
    import json as ujson

from net import FetchError, https_get

HOST = 'api.tfl.gov.uk'
PATH = '/Line/dlr/Arrivals/{}'


def get_departures(stop_id):
    """Return [{'timeToStation': seconds, 'destinationName': str}, ...]
    or raise FetchError."""
    status, body = https_get(HOST, PATH.format(stop_id))
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
