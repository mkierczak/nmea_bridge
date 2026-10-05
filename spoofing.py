"""Heuristic GPS spoofing indicator from NMEA data (no authentication is possible here).

A raised state means "spoofing suspected", never proof; a careful spoofer (smooth drift, consistent
time, realistic power) passes these checks. Indicators (strong / medium / weak):

  K1 strong  position jump that persists (implied speed above MAX_SPEED_KN)
  T1 strong  GPS time steps relative to the Pico's own clock
  K2 medium  position change disagrees with reported speed over ~10 s
  S1 medium  satellites of one constellation have suspiciously uniform C/N0
  C1 medium  GPS vs BeiDou mean C/N0 offset shifted from its learned baseline
  K3 weak    altitude step between consecutive GGA
  S2 weak    C/N0 not correlated with elevation
  S3 weak    sudden C/N0 rise or tracked-satellite set change

No hardware imports: runs on MicroPython and CPython (tests, tools/replay.py).
"""
import math

try:
    from utime import ticks_diff as _ticks_diff
except ImportError:
    def _ticks_diff(a, b):
        return a - b

# Thresholds (tune with tools/replay.py on recorded logs)
MAX_SPEED_KN = 60            # faster implied movement than this between fixes is a jump
JUMP_MARGIN_M = 30           # position noise allowance added to the distance a vessel can cover
GAP_MAX_MS = 10 * 60 * 1000  # longer gaps between fixes are not compared
TIME_JUMP_MS = 500           # GPS time step vs local clock between consecutive fixes
TIME_CHECK_MAX_MS = 5000     # only compare consecutive fixes at most this far apart
K2_WINDOW_MS = 10 * 1000
K2_ABS_KN = 3.0              # speed mismatch must exceed this ...
K2_REL = 0.5                 # ... and this fraction of the larger of implied / reported speed
ALT_STEP_M = 30
UNIFORM_STD_DB = 1.5         # std dev of C/N0 below this (>= UNIFORM_MIN_SATS tracked) is "too clean"
UNIFORM_MIN_SATS = 6
ELEV_CORR_MAX = 0.0          # elevation/C-N0 correlation at or below this is suspicious
ELEV_MIN_SATS = 8
ELEV_CYCLES = 3              # consecutive GSV cycles before S2 fires
CN0_RISE_DB = 8              # mean C/N0 above baseline by this much
JACCARD_MIN = 0.5            # similarity of tracked-satellite sets between GSV cycles
JACCARD_MIN_SATS = 6
CROSS_SHIFT_DB = 8           # GPS-BeiDou mean C/N0 offset change from baseline
CROSS_MIN_SATS = 3
BASELINE_ALPHA = 0.05
BASELINE_MIN_SAMPLES = 5     # GSV cycles before statistical baselines are trusted
WARMUP_FIXES = 30            # valid fixes after boot before any indicator may fire
WINDOW_MS = 60 * 1000        # indicators count while younger than this
LATCH_MS = 10 * 60 * 1000    # ALERT stays up this long after the last strong evidence

STRONG = ('K1', 'T1')
MEDIUM = ('K2', 'S1', 'C1')
WEAK = ('K3', 'S2', 'S3')
OK, SUSPECT, ALERT = 'OK', 'SUSPECT', 'ALERT'

_KN_PER_MS = 1.943844        # knots per (m/s)
_DAY_MS = 86400000


def _dist_m(a, b):
    """Distance between two (lat_u, lon_u, ...) positions in 1e-4 arc-minutes."""
    dlat = (b[0] - a[0]) * 0.1852
    mid_deg = (a[0] + b[0]) / 1200000.0
    dlon = (b[1] - a[1]) * 0.1852 * math.cos(math.radians(mid_deg))
    return math.sqrt(dlat * dlat + dlon * dlon)


def _dt_ms(a, b):
    """Elapsed GPS time between (.., .., days, ms_of_day) tuples."""
    return (b[2] - a[2]) * _DAY_MS + (b[3] - a[3])


def _mean(xs):
    return sum(xs) / len(xs)


def _std(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) * (x - m) for x in xs) / len(xs))


def _corr(xs, ys):
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) * (x - mx) for x in xs)
    syy = sum((y - my) * (y - my) for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


class SpoofDetector(object):

    def __init__(self, parser, time_tolerance_ms=TIME_JUMP_MS):
        self.parser = parser
        self.time_tolerance_ms = time_tolerance_ms  # raise when the GPS link delays RMC (low baud)
        self.state = OK
        self.reason = ''
        self.warm_fixes = 0
        self._events = {}             # indicator code -> ticks when last seen
        self._alert_at = None         # ticks of the last ALERT-level evidence
        self._alert_reason = ''
        self._fix_seen = parser.fix_count
        self._alt_seen = parser.alt_version
        self._cn0_seen = parser.cn0_version
        # kinematics
        self._trusted = None          # (lat_u, lon_u, days, ms) last accepted position
        self._candidate = None        # position after a suspected jump, awaiting confirmation
        self._prev_time = None        # (days, ms, rx_ms) of the previous fix
        self._anchor = None
        self._sog_sum = 0.0
        self._sog_n = 0
        self._alt_prev = None
        # signal statistics
        self._mean_base = None
        self._mean_samples = 0
        self._offset_base = None
        self._offset_samples = 0
        self._prev_prns = None
        self._elev_bad = 0

    def label(self):
        return 'SPF!' if self.state == ALERT else 'SPF?' if self.state == SUSPECT else ''

    def signature(self):
        return (self.state, self.reason)

    def active_codes(self):
        """Indicator codes currently counting (younger than WINDOW_MS), in severity order."""
        return [c for c in STRONG + MEDIUM + WEAK if c in self._events]

    def latch_remaining_ms(self, now_ms):
        """Milliseconds until a latched ALERT may clear (0 if not latched)."""
        if self._alert_at is None:
            return 0
        return max(0, LATCH_MS - _ticks_diff(now_ms, self._alert_at))

    @property
    def armed(self):
        return self.warm_fixes >= WARMUP_FIXES

    def evaluate(self, now_ms):
        """Process whatever is new in the parser; returns (state, reason). Cheap when idle."""
        p = self.parser
        if p.fix_count != self._fix_seen:
            self._fix_seen = p.fix_count
            self._on_fix(now_ms)
        if p.alt_version != self._alt_seen:
            self._alt_seen = p.alt_version
            self._on_altitude(now_ms)
        if p.cn0_version != self._cn0_seen:
            self._cn0_seen = p.cn0_version
            self._on_gsv(now_ms)
        return self._update_state(now_ms)

    def _flag(self, code, now_ms):
        if self.armed:
            self._events[code] = now_ms

    # --- kinematics -------------------------------------------------------------------------
    def _is_jump(self, a, b):
        dt = _dt_ms(a, b)
        if dt <= 0 or dt > GAP_MAX_MS:
            return False
        allowed = MAX_SPEED_KN / _KN_PER_MS * (dt / 1000.0) + JUMP_MARGIN_M
        return _dist_m(a, b) > allowed

    def _on_fix(self, now_ms):
        p = self.parser
        cur = (p.lat_u, p.lon_u, p.utc_days, p.utc_ms)
        self.warm_fixes += 1

        # T1: GPS time vs the Pico's own clock between consecutive fixes
        if self._prev_time is not None and p.rx_ms is not None and self._prev_time[2] is not None:
            dt_gps = (p.utc_days - self._prev_time[0]) * _DAY_MS + (p.utc_ms - self._prev_time[1])
            dt_local = _ticks_diff(p.rx_ms, self._prev_time[2])
            if dt_gps < 0 or (0 <= dt_local <= TIME_CHECK_MAX_MS and abs(dt_gps - dt_local) > self.time_tolerance_ms):
                self._flag('T1', now_ms)
        self._prev_time = (p.utc_days, p.utc_ms, p.rx_ms)

        # K1: a jump counts only once the new position persists (a single glitch is dropped)
        if self._trusted is None:
            self._trusted = cur
        elif self._candidate is None:
            if self._is_jump(self._trusted, cur):
                self._candidate = cur
            else:
                self._trusted = cur
        else:
            if not self._is_jump(self._candidate, cur):      # stayed at the new place
                self._flag('K1', now_ms)
            elif not self._is_jump(self._trusted, cur):      # back to normal: it was a glitch
                pass
            else:                                            # erratic jumping
                self._flag('K1', now_ms)
            self._trusted = cur
            self._candidate = None

        # K2: movement over ~10 s versus reported speed
        if self._candidate is None:
            if self._anchor is None:
                self._anchor, self._sog_sum, self._sog_n = self._trusted, 0.0, 0
            if p.sog_kn is not None:
                self._sog_sum += p.sog_kn
                self._sog_n += 1
            dt = _dt_ms(self._anchor, self._trusted)
            if dt >= K2_WINDOW_MS:
                if self._sog_n and dt <= 3 * K2_WINDOW_MS:
                    implied = _dist_m(self._anchor, self._trusted) / (dt / 1000.0) * _KN_PER_MS
                    sog = self._sog_sum / self._sog_n
                    if abs(implied - sog) > max(K2_ABS_KN, K2_REL * max(implied, sog)):
                        self._flag('K2', now_ms)
                self._anchor, self._sog_sum, self._sog_n = self._trusted, 0.0, 0

    def _on_altitude(self, now_ms):
        alt = self.parser.alt_m
        if self._alt_prev is not None and abs(alt - self._alt_prev) > ALT_STEP_M:
            self._flag('K3', now_ms)
        self._alt_prev = alt

    # --- signal statistics ------------------------------------------------------------------
    def _on_gsv(self, now_ms):
        sats = self.parser.sats_by_talker
        tracked = [(t, prn, el, cn) for t, lst in sats.items() for prn, el, cn in lst if cn > 0]
        if not tracked:
            return

        # S1: too uniform within one constellation
        for talker, lst in sats.items():
            cns = [cn for _, _, cn in lst if cn > 0]
            if len(cns) >= UNIFORM_MIN_SATS and _std(cns) < UNIFORM_STD_DB:
                self._flag('S1', now_ms)

        # S2: real signals get stronger with elevation
        pairs = [(el, cn) for _, _, el, cn in tracked if el is not None]
        if len(pairs) >= ELEV_MIN_SATS:
            if _corr([e for e, _ in pairs], [c for _, c in pairs]) <= ELEV_CORR_MAX:
                self._elev_bad += 1
                if self._elev_bad >= ELEV_CYCLES:
                    self._flag('S2', now_ms)
            else:
                self._elev_bad = 0

        # S3: sudden power rise, or the set of tracked satellites changes abruptly
        mean = _mean([cn for _, _, _, cn in tracked])
        rise = (self._mean_samples >= BASELINE_MIN_SAMPLES and mean > self._mean_base + CN0_RISE_DB)
        if rise:
            self._flag('S3', now_ms)
        else:
            self._mean_base = mean if self._mean_base is None else \
                self._mean_base + BASELINE_ALPHA * (mean - self._mean_base)
            self._mean_samples += 1
        prns = set((t, prn) for t, prn, _, _ in tracked)
        if (self._prev_prns is not None and len(prns) >= JACCARD_MIN_SATS and
                len(self._prev_prns) >= JACCARD_MIN_SATS):
            if len(prns & self._prev_prns) / len(prns | self._prev_prns) < JACCARD_MIN:
                self._flag('S3', now_ms)
        self._prev_prns = prns

        # C1: GPS vs BeiDou level offset
        gp = [cn for t, _, _, cn in tracked if t in ('GP', 'GN')]
        bd = [cn for t, _, _, cn in tracked if t in ('BD', 'GB')]
        if len(gp) >= CROSS_MIN_SATS and len(bd) >= CROSS_MIN_SATS:
            offset = _mean(gp) - _mean(bd)
            if self._offset_samples >= BASELINE_MIN_SAMPLES and \
                    abs(offset - self._offset_base) > CROSS_SHIFT_DB:
                self._flag('C1', now_ms)
            else:
                self._offset_base = offset if self._offset_base is None else \
                    self._offset_base + BASELINE_ALPHA * (offset - self._offset_base)
                self._offset_samples += 1

    # --- decision ---------------------------------------------------------------------------
    def _update_state(self, now_ms):
        for code in list(self._events):
            if _ticks_diff(now_ms, self._events[code]) > WINDOW_MS:
                del self._events[code]
        codes = self._events
        strong = [c for c in STRONG if c in codes]
        medium = [c for c in MEDIUM if c in codes]
        weak = [c for c in WEAK if c in codes]
        reason = ''.join(c for c in STRONG + MEDIUM + WEAK if c in codes)
        if strong or len(medium) >= 2:
            self._alert_at, self._alert_reason = now_ms, reason
            state = ALERT
        elif self._alert_at is not None and _ticks_diff(now_ms, self._alert_at) < LATCH_MS:
            state, reason = ALERT, self._alert_reason  # latched
        elif medium or len(weak) >= 2:
            state = SUSPECT
        else:
            state = OK
        if state == OK:
            self._alert_at = None
        self.state = state
        self.reason = reason
        return self.state, self.reason
