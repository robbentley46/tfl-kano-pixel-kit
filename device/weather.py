"""
Open-Meteo client (https://open-meteo.com) - free, no API key, and with
a tight field list the reply is ~1KB, far smaller than the TfL arrivals
body. Uses the same net.https_get_json as tfl.py, so failures carry the
same stage codes (dnS, Con, SSL, ...); app.py tells the two APIs apart.

timezone=Europe/London makes the daily low/high cover the local
calendar day. forecast_hours=RAIN_HOURS returns exactly that many
hourly rain chances starting from the current hour - one per display
column of the rain strip.
"""
from net import FetchError, https_get_json

HOST = 'api.open-meteo.com'
RAIN_HOURS = 16
PATH = (
    '/v1/forecast?latitude={}&longitude={}'
    '&current=temperature_2m,weather_code,is_day'
    '&daily=temperature_2m_max,temperature_2m_min'
    '&hourly=precipitation_probability&forecast_hours=' + str(RAIN_HOURS) +
    '&forecast_days=1&timezone=Europe%2FLondon'
)

# WMO weather interpretation codes -> icon names in icons.py.
SUN_CODES = (0, 1)
PART_CLOUD_CODES = (2,)
CLOUD_CODES = (3, 45, 48)
SNOW_CODES = tuple(range(71, 78)) + (85, 86)
THUNDER_CODES = (95, 96, 99)


def get_weather(lat, lon):
    """Return {'temp', 'code', 'is_day', 'low', 'high', 'rain'} - temps
    as rounded int degrees C, rain as a list of RAIN_HOURS hourly
    precipitation probabilities (%) - or raise FetchError."""
    data = https_get_json(HOST, PATH.format(lat, lon))
    try:
        return _parse_weather(data)
    except Exception as e:
        raise FetchError('JSn', repr(e))


def _parse_weather(data):
    current = data['current']
    daily = data['daily']
    return {
        'temp': int(round(current['temperature_2m'])),
        'code': current['weather_code'],
        'is_day': bool(current.get('is_day', 1)),
        'low': int(round(daily['temperature_2m_min'][0])),
        'high': int(round(daily['temperature_2m_max'][0])),
        # Open-Meteo reports null for hours it has no estimate for.
        'rain': [p or 0 for p in data['hourly']['precipitation_probability']],
    }


def icon_for(code, is_day):
    if code in SUN_CODES:
        return 'sun' if is_day else 'moon'
    if code in PART_CLOUD_CODES:
        return 'part_cloud' if is_day else 'cloud'
    if code in CLOUD_CODES:
        return 'cloud'
    if code in SNOW_CODES:
        return 'snow'
    if code in THUNDER_CODES:
        return 'thunder'
    # Everything else left in the WMO table (51-67, 80-82) is drizzle,
    # rain or showers - and an unknown future code most likely is too.
    return 'rain'
