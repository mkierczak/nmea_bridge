import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import sun

STOCKHOLM = (59.33, 18.07)
# days since 1970-01-01
JUN21 = 20625        # 2026-06-21
DEC21 = 20808        # 2026-12-21


def day_curve(lat, lon, days, step_min=10):
    return [sun.elevation_deg(lat, lon, days, m * 60000) for m in range(0, 24 * 60, step_min)]


def test_the_sun_at_known_times_and_places():
    # a sun-over-the-equator day: the equinox noon at the equator is about overhead
    assert abs(max(day_curve(0.0, 0.0, 20532)) - 90.0) < 2.0            # 2026-03-20
    # Stockholm: noon height is 90 - latitude + declination
    summer = day_curve(*STOCKHOLM, JUN21)
    winter = day_curve(*STOCKHOLM, DEC21)
    assert abs(max(summer) - (90 - 59.33 + 23.44)) < 0.7
    assert abs(max(winter) - (90 - 59.33 - 23.44)) < 0.7
    assert abs(min(summer) - (59.33 + 23.44 - 90)) < 0.7                # the midsummer night is never dark
    assert min(summer) > -8 and min(winter) < -40


def test_solar_noon_is_where_the_longitude_says():
    curve = day_curve(*STOCKHOLM, JUN21, step_min=1)
    noon_min = curve.index(max(curve))
    assert abs(noon_min - (12 * 60 - 18.07 * 4 + 1.5)) < 6              # 12:00 - 4 min per degree east, minus ~2 min


def test_dark_and_light_with_hysteresis():
    assert sun.is_dark(-5.0, False) and not sun.is_dark(-2.0, False)    # not yet dark at -2
    assert sun.is_dark(-2.0, True) and not sun.is_dark(-0.5, True)      # and dark stays dark until -1
    assert not sun.is_dark(10.0, True)
    # the polar night at 69 N: the sun never gets higher than about -2.4 degrees, which counts as twilight, not dark
    assert abs(max(day_curve(69.0, 20.0, DEC21)) - (90 - 69.0 - 23.44)) < 0.7
