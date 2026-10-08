"""Anchor watch and the man-overboard mark: pure logic, no hardware imports (runs on MicroPython and CPython).

Positions are (lat_u, lon_u) in 1e-4 arc-minutes, as the parser keeps them. AnchorWatch remembers where the anchor
was dropped and raises an alarm when the boat has stayed outside a radius for a few fixes, or when there has been
no fix for a while (the watch cannot see anything then). The anchor position is stored in a small file so that a
reboot does not end the watch. MobMark remembers where somebody went overboard.
"""
import math

try:
    import json
except ImportError:
    json = None

OFF, OK, DRAG, NOFIX = 'off', 'ok', 'drag', 'nofix'
ALARM_FIXES = 3               # consecutive fixes outside the radius before the alarm (a single glitch is ignored)
CLEAR_FIXES = 3               # consecutive fixes back inside before the alarm ends
NOFIX_ALARM_MS = 2 * 60 * 1000   # without a fix this long the watch is blind: alarm
DEFAULT_RADIUS_M = 50

_HALF_TURN_U = 108000000      # 180 degrees in 1e-4 arc-minutes
_FULL_TURN_U = 216000000


def _delta(a, b):
    """(north, east) metres from position a to position b."""
    dlat = (b[0] - a[0]) * 0.1852
    dlon_u = b[1] - a[1]
    if dlon_u > _HALF_TURN_U:       # across the antimeridian the short way round
        dlon_u -= _FULL_TURN_U
    elif dlon_u < -_HALF_TURN_U:
        dlon_u += _FULL_TURN_U
    mid_deg = (a[0] + b[0]) / 1200000.0
    return dlat, dlon_u * 0.1852 * math.cos(math.radians(mid_deg))


def distance_m(a, b):
    north, east = _delta(a, b)
    return math.sqrt(north * north + east * east)


def bearing_deg(a, b):
    """True bearing (0-359) of position b seen from position a."""
    north, east = _delta(a, b)
    return int(round(math.degrees(math.atan2(east, north)))) % 360


class AnchorWatch(object):

    def __init__(self, path=None, radius_m=DEFAULT_RADIUS_M):
        self.path = path
        self.radius_m = radius_m
        self.anchor = None            # (lat_u, lon_u) where the anchor was dropped
        self.state = OFF
        self.distance = 0.0           # metres from the anchor now
        self.bearing = 0              # true bearing of the boat seen from the anchor
        self.max_distance = 0.0       # the furthest it has been since the anchor was dropped
        self._fix_seen = None
        self._out = 0
        self._in = 0

    @property
    def is_set(self):
        return self.anchor is not None

    @property
    def alarming(self):
        return self.state == DRAG or self.state == NOFIX

    def set(self, position):
        """Drop the anchor at a position (lat_u, lon_u)."""
        self.anchor = (position[0], position[1])
        self.state = OK
        self.distance = 0.0
        self.max_distance = 0.0
        self._out = self._in = 0
        self._fix_seen = None
        self._save()

    def clear(self):
        self.anchor = None
        self.state = OFF
        self._out = self._in = 0
        self._save()

    def update(self, parser, fix_age_ms):
        """Call every loop with the parser and the age of the last valid fix (None: no fix since boot).
        Returns the state."""
        if self.anchor is None:
            return OFF
        if fix_age_ms is None or fix_age_ms >= NOFIX_ALARM_MS:
            self.state = NOFIX
            return self.state
        if self.state == NOFIX:
            self.state = OK                      # the fix is back: judge by the position again
            self._out = self._in = 0
        if parser.fix_count != self._fix_seen:
            self._fix_seen = parser.fix_count
            here = (parser.lat_u, parser.lon_u)
            self.distance = distance_m(self.anchor, here)
            self.bearing = bearing_deg(self.anchor, here)
            if self.distance > self.max_distance:
                self.max_distance = self.distance
            if self.distance > self.radius_m:
                self._out += 1
                self._in = 0
                if self._out >= ALARM_FIXES:
                    self.state = DRAG
            else:
                self._in += 1
                self._out = 0
                if self.state == DRAG and self._in >= CLEAR_FIXES:
                    self.state = OK
        return self.state

    # --- persistence: the anchor survives a reboot ----------------------------------------------
    def load(self):
        if not self.path or json is None:
            return False
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.anchor = (int(data['lat_u']), int(data['lon_u']))
            self.state = OK
            return True
        except (OSError, ValueError, KeyError, TypeError):
            self.anchor = None
            self.state = OFF
            return False

    def _save(self):
        if not self.path or json is None:
            return
        try:
            if self.anchor is None:
                import os
                os.remove(self.path)
            else:
                with open(self.path, 'w') as f:
                    json.dump({'lat_u': self.anchor[0], 'lon_u': self.anchor[1]}, f)
        except OSError:
            pass                                 # no file system, or nothing to remove: the watch still works


class MobMark(object):
    """A man-overboard position, with the time it was marked. With a file it survives a reboot (and then raises the
    alarm again: somebody must look at it)."""

    def __init__(self, path=None):
        self.path = path
        self.position = None
        self.marked_ms = 0            # uptime when it was marked (counted from the boot after a reboot)
        self.marked_utc = None        # seconds since 1970 on the GPS clock, when it was known
        self.alerting = False         # True from the mark until a key press: banner, blink and buzzer

    @property
    def active(self):
        return self.position is not None

    def set(self, position, now_ms, utc_s=None):
        self.position = (position[0], position[1])
        self.marked_ms = now_ms
        self.marked_utc = utc_s
        self.alerting = True
        self._save()

    def acknowledge(self):
        self.alerting = False

    def clear(self):
        self.position = None
        self.marked_utc = None
        self.alerting = False
        self._save()

    def seconds(self, now_ms, utc_s=None):
        """Seconds since the mark: by the GPS clock when both times are known (right across a reboot), else by
        the uptime."""
        if self.marked_utc is not None and utc_s is not None:
            return max(0, utc_s - self.marked_utc)
        return max(0, (now_ms - self.marked_ms) // 1000)

    def load(self):
        if not self.path or json is None:
            return False
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.position = (int(data['lat_u']), int(data['lon_u']))
            utc = data.get('utc')
            self.marked_utc = int(utc) if utc is not None else None
            self.marked_ms = 0
            self.alerting = True                 # a reboot with somebody in the water: make sure it is seen
            return True
        except (OSError, ValueError, KeyError, TypeError):
            self.position = None
            return False

    def _save(self):
        if not self.path or json is None:
            return
        try:
            if self.position is None:
                import os
                os.remove(self.path)
            else:
                with open(self.path, 'w') as f:
                    json.dump({'lat_u': self.position[0], 'lon_u': self.position[1], 'utc': self.marked_utc}, f)
        except OSError:
            pass
