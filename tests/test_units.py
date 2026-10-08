import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
import units


class Config:
    """Set the module-level display settings for a test and put them back."""

    def __init__(self, speed='kn', coords='ddmm.mm', offset=0):
        self.new = (speed, coords, offset)

    def __enter__(self):
        self.old = (units.SPEED_UNIT, units.COORD_FORMAT, units.UTC_OFFSET_H)
        units.SPEED_UNIT, units.COORD_FORMAT, units.UTC_OFFSET_H = self.new

    def __exit__(self, *exc):
        units.SPEED_UNIT, units.COORD_FORMAT, units.UTC_OFFSET_H = self.old


def test_speed_conversion_and_text():
    with Config('kn'):
        assert units.speed_text(5.24) == '5.2' and units.speed_text(123.4) == '123'
    with Config('km/h'):
        assert units.speed_text(10.0) == '18.5'
        assert units.speed_text(60.0) == '111'             # no decimal from 100 up
    with Config('m/s'):
        assert units.speed_text(10.0) == '5.1'


def test_distance_text_switches_to_miles_or_kilometres_and_stays_short():
    with Config('kn'):
        assert units.distance_text(42.4) == '42m' and units.distance_text(999.4) == '999m'
        assert units.distance_text(1852) == '1.00nm' and units.distance_text(25000) == '13.5nm'
    with Config('km/h'):
        assert units.distance_text(1500) == '1.50km' and units.distance_text(12345) == '12.3km'
    with Config('kn'):
        assert all(len(units.distance_text(m)) <= 6 for m in (0, 5, 999, 1000, 9999, 99999, 999999))


def test_clock_shift_and_suffix():
    with Config(offset=0):
        assert units.shift_time('12:34:56') == '12:34:56' and units.clock_suffix() == 'Z'
    with Config(offset=2):
        assert units.shift_time('12:34:56') == '14:34:56' and units.clock_suffix() == 'L'
        assert units.shift_time('23:10:00') == '01:10:00'            # wraps past midnight
        assert units.shift_time('--:--:--') == '--:--:--'            # no time yet: untouched
    with Config(offset=-5):
        assert units.shift_time('03:00:01') == '22:00:01'


def test_decimal_degree_coordinates():
    p = NMEA.Parser()
    p.lat, p.NS, p.lon, p.EW = '5918.3420', 'N', '01803.2190', 'E'
    with Config(coords='dd.dddd'):
        assert p.get_lat_string() == 'N59.3057' + chr(176)
        assert p.get_lon_string() == 'E018.0537' + chr(176)           # 18 + 3.219/60
        assert len(p.get_lon_string()) <= 10                          # fits the large font
    with Config(coords='ddmm.mm'):
        assert p.get_lat_string() == 'N59' + chr(176) + '18.34'
    p.lat = 'garbage'
    with Config(coords='dd.dddd'):
        assert p.get_lat_string() == ''


def test_settings_push_into_units():
    import settings
    cfg = settings.Settings({k: v for k, v in __import__('test_settings').full_defaults().items()}, '/nonexistent/s.json')
    cfg.set('speed_unit', 'km/h')
    cfg.set('coord_fmt', 'dd.dddd')
    cfg.set('utc_offset_h', 2)
    with Config():
        settings.apply_units(cfg, units)
        assert (units.SPEED_UNIT, units.COORD_FORMAT, units.UTC_OFFSET_H) == ('km/h', 'dd.dddd', 2)
