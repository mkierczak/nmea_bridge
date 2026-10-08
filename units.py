"""Display units and formats: speed unit, coordinate format and the UTC offset of the clock.

Module-level settings, pushed in from the menu settings (see settings.apply_units) and read by the
formatting code, like the detector thresholds. No hardware imports: runs on MicroPython and CPython.
"""
SPEED_UNITS = ('kn', 'km/h', 'm/s')
COORD_FORMATS = ('ddmm.mm', 'dd.dddd')

SPEED_UNIT = 'kn'
COORD_FORMAT = 'ddmm.mm'       # degrees and decimal minutes, or decimal degrees
UTC_OFFSET_H = 0               # the clock shows UTC plus this many hours (0 = UTC, with a Z)

_PER_KNOT = {'kn': 1.0, 'km/h': 1.852, 'm/s': 0.514444}


def speed(knots):
    """A speed in knots converted to the display unit."""
    return knots * _PER_KNOT.get(SPEED_UNIT, 1.0)


def speed_text(knots):
    """'5.2', or '123' from 100 up (no decimal: it would not fit)."""
    value = speed(knots)
    return '{:.1f}'.format(value) if value < 100 else '{:.0f}'.format(value)


def distance_text(metres):
    """Distance for the anchor and man-overboard pages: metres up to 999, then nautical miles (with knots)
    or kilometres. At most 6 characters."""
    metres = abs(metres)
    if metres < 1000:
        return '{:.0f}m'.format(metres)
    if SPEED_UNIT == 'kn':
        return _long(metres / 1852.0, 'nm')
    return _long(metres / 1000.0, 'km')


def distance_parts(metres):
    """(number, unit) for a large-font readout: ('42', 'm'), ('1.25', 'nm'), ('13.5', 'km')."""
    text = distance_text(metres)
    for i in range(len(text)):
        if not (text[i].isdigit() or text[i] == '.'):
            return text[:i], text[i:]
    return text, ''


def _long(value, unit):
    """'1.25nm', '12.3nm', '123nm': three significant digits, at most 6 characters."""
    return '{:.2f}{}'.format(value, unit) if value < 10 else '{:.1f}{}'.format(value, unit) if value < 100 else '{:.0f}{}'.format(value, unit)


def shift_time(text):
    """'12:34:56' shifted by the UTC offset; '--:--:--' (no time yet) stays as it is."""
    if not UTC_OFFSET_H or text[0] == '-':
        return text
    hours = (int(text[0:2]) + UTC_OFFSET_H) % 24
    return '{:02d}{}'.format(hours, text[2:])


def clock_suffix():
    """'Z' for UTC, 'L' for a clock shifted to local time."""
    return 'L' if UTC_OFFSET_H else 'Z'


def decimal_degrees(value, deg_digits):
    """'ddmm.mmmm' / 'dddmm.mmmm' -> degrees as a float."""
    return int(value[:deg_digits]) + float(value[deg_digits:]) / 60.0
