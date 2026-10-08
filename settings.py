"""User settings stored on the board (settings.json), edited through the on-device menu.

The defaults live in main.py (and in the detector modules); only values that differ from the
defaults are saved, so changing a default later still affects every setting you never touched.
Pure logic, no hardware imports.
"""
import json
import os

SETTINGS_FILE = 'settings.json'

LIVE, REBOOT = 'live', 'reboot'            # when a change takes effect
BOOL, CHOICE, INT = 'bool', 'choice', 'int'

# (key, label (<= 10 chars), kind, group, apply, extra)
#   extra: None (bool), tuple of choices (choice), (min, max, step) (int)
SCHEMA = (
    ('gps_baud', 'Baudrate', CHOICE, 'GPS', REBOOT, (4800, 9600, 14400, 19200, 38400, 57600, 115200)),
    ('gnss_mode', 'GNSS mode', CHOICE, 'GPS', REBOOT, ('GPS', 'GPS+BD')),
    ('jam_detect', 'Jamming', BOOL, 'Detection', LIVE, None),
    ('spoof_detect', 'Spoofing', BOOL, 'Detection', LIVE, None),
    ('spoof_action', 'Spoof act.', CHOICE, 'Detection', LIVE, ('display', 'block')),
    ('fwd_RMC', 'RMC', BOOL, 'Radio output', LIVE, None),
    ('fwd_GGA', 'GGA', BOOL, 'Radio output', LIVE, None),
    ('fwd_GSA', 'GSA', BOOL, 'Radio output', LIVE, None),
    ('fwd_GSV', 'GSV', BOOL, 'Radio output', LIVE, None),
    ('fwd_ZDA', 'ZDA', BOOL, 'Radio output', LIVE, None),
    ('contrast', 'Contrast', INT, 'Display', LIVE, (0, 255, 15)),
    ('screen_off_s', 'Screen off', CHOICE, 'Display', LIVE, (0, 30, 60, 300)),
    ('night', 'Night mode', BOOL, 'Display', LIVE, None),
    ('speed_unit', 'Speed unit', CHOICE, 'Display', LIVE, ('kn', 'km/h', 'm/s')),
    ('coord_fmt', 'Coords', CHOICE, 'Display', LIVE, ('ddmm.mm', 'dd.dddd')),
    ('utc_offset_h', 'UTC offset', INT, 'Display', LIVE, (-12, 14, 1)),
    ('log_raw', 'Log raw', BOOL, 'System', LIVE, None),
    ('cn0_drop_db', 'CN0 drop', INT, 'Advanced', LIVE, (3, 15, 1)),
    ('tracked_drop_pct', 'Sats drop%', INT, 'Advanced', LIVE, (30, 90, 5)),
    ('jam_enter_cycles', 'Jam enter', INT, 'Advanced', LIVE, (1, 6, 1)),
    ('jam_exit_cycles', 'Jam exit', INT, 'Advanced', LIVE, (1, 10, 1)),
    ('max_speed_kn', 'Max speed', INT, 'Advanced', LIVE, (20, 100, 5)),
    ('time_jump_ms', 'Time jump', INT, 'Advanced', LIVE, (200, 3000, 100)),
    ('uniform_std_x10', 'Flat C/N0', INT, 'Advanced', LIVE, (5, 40, 1)),
    ('alt_step_m', 'Alt step', INT, 'Advanced', LIVE, (10, 100, 5)),
    ('latch_min', 'Latch min', INT, 'Advanced', LIVE, (1, 60, 1)),
    ('warmup_fixes', 'Warm-up', INT, 'Advanced', LIVE, (10, 120, 5)),
    ('elev_corr_x100', 'S2 corr', INT, 'Advanced', LIVE, (-60, 20, 5)),
    ('elev_cycles', 'S2 cycles', INT, 'Advanced', LIVE, (3, 60, 1)),
)
_SPEC = {row[0]: row for row in SCHEMA}
# unit shown after the default in the menu hint line (the advanced thresholds have cryptic labels)
UNITS = {'cn0_drop_db': ' dB', 'tracked_drop_pct': '%', 'max_speed_kn': ' kn', 'time_jump_ms': ' ms',
         'uniform_std_x10': ' x0.1dB', 'alt_step_m': ' m', 'latch_min': ' min', 'warmup_fixes': ' fixes',
         'elev_corr_x100': ' x0.01', 'jam_enter_cycles': ' cyc', 'jam_exit_cycles': ' cyc',
         'elev_cycles': ' cyc'}


def spec(key):
    return _SPEC[key]


def unit(key):
    return UNITS.get(key, '')


def coerce(key, value):
    """Return a valid value for 'key' (ints are clamped and snapped to the step) or raise ValueError."""
    _, _, kind, _, _, extra = _SPEC[key]
    if kind == BOOL:
        if isinstance(value, bool):
            return value
    elif kind == CHOICE:
        for choice in extra:                       # exact type too: hand-edited false/60.0 must not pass as 0/60
            if value == choice and type(value) is type(choice):
                return choice
    elif kind == INT:
        if isinstance(value, int) and not isinstance(value, bool):
            lo, hi, step = extra
            value = min(hi, max(lo, value))
            return lo + round((value - lo) / step) * step
    raise ValueError('bad value for ' + key)


def next_value(key, value, direction):
    """Next/previous value in the setting's range (choices wrap around, ints stop at the ends)."""
    _, _, kind, _, _, extra = _SPEC[key]
    if kind == BOOL:
        return not value
    if kind == CHOICE:
        return extra[(extra.index(value) + direction) % len(extra)]
    lo, hi, step = extra
    return min(hi, max(lo, value + direction * step))


def threshold_defaults(jamming, spoofing):
    """Defaults of the advanced settings, taken from the detector modules' own constants."""
    return {
        'cn0_drop_db': jamming.CN0_DROP_DB,
        'tracked_drop_pct': round(jamming.TRACKED_DROP_FRACTION * 100),
        'jam_enter_cycles': jamming.ENTER_CYCLES,
        'jam_exit_cycles': jamming.EXIT_CYCLES,
        'max_speed_kn': spoofing.MAX_SPEED_KN,
        'time_jump_ms': spoofing.TIME_JUMP_MS,
        'uniform_std_x10': round(spoofing.UNIFORM_STD_DB * 10),
        'alt_step_m': spoofing.ALT_STEP_M,
        'latch_min': spoofing.LATCH_MS // 60000,
        'warmup_fixes': spoofing.WARMUP_FIXES,
        'elev_corr_x100': round(spoofing.ELEV_CORR_MAX * 100),
        'elev_cycles': spoofing.ELEV_CYCLES,
    }


UNIT_KEYS = ('speed_unit', 'coord_fmt', 'utc_offset_h')


def apply_units(cfg, units):
    """Push the display unit settings into the units module (read by the formatting code on every call)."""
    units.SPEED_UNIT = cfg.get('speed_unit')
    units.COORD_FORMAT = cfg.get('coord_fmt')
    units.UTC_OFFSET_H = cfg.get('utc_offset_h')


def apply_thresholds(cfg, jamming, spoofing):
    """Push the advanced settings into the detector modules (they read these constants on every call)."""
    jamming.CN0_DROP_DB = cfg.get('cn0_drop_db')
    jamming.TRACKED_DROP_FRACTION = cfg.get('tracked_drop_pct') / 100
    jamming.ENTER_CYCLES = cfg.get('jam_enter_cycles')
    jamming.EXIT_CYCLES = cfg.get('jam_exit_cycles')
    spoofing.MAX_SPEED_KN = cfg.get('max_speed_kn')
    spoofing.TIME_JUMP_MS = cfg.get('time_jump_ms')
    spoofing.UNIFORM_STD_DB = cfg.get('uniform_std_x10') / 10
    spoofing.ALT_STEP_M = cfg.get('alt_step_m')
    spoofing.LATCH_MS = cfg.get('latch_min') * 60000
    spoofing.WARMUP_FIXES = cfg.get('warmup_fixes')
    spoofing.ELEV_CORR_MAX = cfg.get('elev_corr_x100') / 100
    spoofing.ELEV_CYCLES = cfg.get('elev_cycles')


class Settings(object):

    def __init__(self, defaults, path=SETTINGS_FILE):
        for row in SCHEMA:
            if row[0] not in defaults:
                raise KeyError('no default for ' + row[0])
        self.path = path
        self.defaults = {row[0]: coerce(row[0], defaults[row[0]]) for row in SCHEMA}
        self.values = dict(self.defaults)
        self._boot = {k: v for k, v in self.values.items() if _SPEC[k][4] == REBOOT}

    def load(self):
        """Merge stored overrides; invalid or unknown entries and a corrupt file are ignored."""
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for key, value in data.items():
                if key in _SPEC:
                    try:
                        self.values[key] = coerce(key, value)
                    except ValueError:
                        pass
        self._boot = {k: self.values[k] for k in self._boot}

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        """Set a (validated) value; returns True if it changed. Call save() to persist."""
        value = coerce(key, value)
        changed = self.values[key] != value
        self.values[key] = value
        return changed

    def is_overridden(self, key):
        return self.values[key] != self.defaults[key]

    def overrides(self):
        return {k: v for k, v in self.values.items() if v != self.defaults[k]}

    def save(self):
        """Write only the overrides (temp file + rename, so a power cut cannot corrupt the file)."""
        overrides = self.overrides()
        if not overrides:
            self._remove(self.path)
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(overrides, f)
        try:
            os.rename(tmp, self.path)
        except OSError as e:
            if e.args and e.args[0] == 17:  # EEXIST: this filesystem cannot rename over a file (FAT)
                self._remove(self.path)
                os.rename(tmp, self.path)
            else:                            # anything else: keep the old file, drop the temp file
                self._remove(tmp)
                raise

    def reset(self):
        self.values = dict(self.defaults)
        self._remove(self.path)

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    @property
    def pending_reboot(self):
        """True if a setting that needs a reboot differs from the value this boot started with."""
        return any(self.values[k] != v for k, v in self._boot.items())
