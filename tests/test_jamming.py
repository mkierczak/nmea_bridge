import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
import jamming
from jamming import JamDetector


def with_checksum(body):
    csum = 0
    for c in body:
        csum ^= ord(c)
    return '${}*{:02X}'.format(body, csum)


def gsv(parser, cn0_list, talker='GP', in_view=None):
    """Feed one complete GSV cycle with the given per-satellite C/N0 values."""
    groups = [cn0_list[i:i + 4] for i in range(0, len(cn0_list), 4)] or [[]]
    in_view = len(cn0_list) if in_view is None else in_view
    for m, group in enumerate(groups, 1):
        fields = ''.join(',{:02d},40,083,{}'.format(i + 1, c if c else '') for i, c in enumerate(group))
        assert parser.parse_sentence(with_checksum(
            '{}GSV,{},{},{:02d}{}'.format(talker, len(groups), m, in_view, fields)))


class Clock:
    def __init__(self):
        self.now = 0

    def step(self, det, fix_ok=True):
        self.now += 4000
        return det.evaluate(self.now, fix_ok)[0]


def warmed_up(parser=None):
    p = parser or NMEA.Parser()
    det = JamDetector(p)
    clk = Clock()
    for _ in range(jamming.BASELINE_MIN_SAMPLES):
        gsv(p, [40, 38, 42, 36, 41, 39, 37, 40])
        clk.step(det)
    return p, det, clk


def test_cn0_parsing_multi_message_and_untracked():
    p = NMEA.Parser()
    gsv(p, [40, 0, 42, 36, 41, 0])  # two messages, two untracked
    assert p.cn0_stats()[0] == 4
    assert abs(p.cn0_stats()[1] - 39.75) < 1e-9
    assert p.cn0_stats()[2] == 42
    assert p.cn0_version == 1


def test_cn0_talkers_combined():
    p = NMEA.Parser()
    gsv(p, [40, 40], talker='GP')
    gsv(p, [30, 30], talker='GL')
    assert p.cn0_stats()[0] == 4
    assert p.cn0_version == 2


def test_no_alert_during_warmup():
    p = NMEA.Parser()
    det = JamDetector(p)
    clk = Clock()
    for _ in range(jamming.BASELINE_MIN_SAMPLES - 1):
        gsv(p, [10, 10])  # poor but steady cold start
        assert clk.step(det) == jamming.INIT
    assert det.label() == ''


def test_healthy_stays_ok():
    p, det, clk = warmed_up()
    for _ in range(10):
        gsv(p, [39, 38, 41, 36, 40, 39, 37, 41])
        assert clk.step(det) == jamming.OK


def test_cn0_drop_goes_low_with_hysteresis_then_recovers():
    p, det, clk = warmed_up()
    gsv(p, [25, 24, 26, 25, 24, 26, 25, 24])  # C/N0 down ~14 dB, all still tracked
    assert clk.step(det) == jamming.OK  # first bad cycle is debounced
    gsv(p, [25, 24, 26, 25, 24, 26, 25, 24])
    assert clk.step(det) == jamming.LOW
    assert 'C' in det.reason
    for i in range(jamming.EXIT_CYCLES):
        gsv(p, [40, 38, 42, 36, 41, 39, 37, 40])
        state = clk.step(det)
    assert state == jamming.OK


def test_loss_of_tracking_and_fix_is_jam():
    p, det, clk = warmed_up()
    for _ in range(jamming.ENTER_CYCLES):
        gsv(p, [0] * 8, in_view=9)  # satellites visible, none tracked
        state = clk.step(det, fix_ok=False)
    assert state == jamming.JAM
    assert set(det.reason) >= {'N', 'F'}


def test_stale_gsv_counts_as_lost_signal():
    p, det, clk = warmed_up()
    states = []
    for _ in range(12):  # no GSV cycles any more
        clk.now += jamming.STALE_MS + 1000
        states.append(det.evaluate(clk.now, False)[0])
    assert states[-1] in (jamming.LOW, jamming.JAM)


def test_pmtk_ack_parsed_and_not_forwardable_type():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('PMTK001,286,3'))
    assert p.pmtk_acks[286] == 3
    assert p.sentence_last_valid_type == 'TK0'


def mp_ticks_diff(a, b):
    """MicroPython's ticks_diff: ticks_ms() wraps at 2**30, differences are taken modulo that."""
    return ((a - b + (1 << 29)) & ((1 << 30) - 1)) - (1 << 29)


def test_stale_detection_survives_ticks_wrap():
    saved = getattr(jamming, '_ticks_diff', None)
    jamming._ticks_diff = mp_ticks_diff           # behave like the board
    try:
        p = NMEA.Parser()
        det = JamDetector(p)
        wrap = lambda t: t % (1 << 30)            # what utime.ticks_ms() would return
        t = (1 << 30) - 30000                     # 30 s before ticks_ms() wraps around
        for _ in range(jamming.BASELINE_MIN_SAMPLES):
            gsv(p, [40, 38, 42, 36, 41, 39, 37, 40])
            t += 4000
            det.evaluate(wrap(t), True)
        assert det.state == jamming.OK
        states = []
        for _ in range(jamming.ENTER_CYCLES + 1):  # GSV stops; time runs on across the wrap
            t += jamming.STALE_MS + 1000
            states.append(det.evaluate(wrap(t), True)[0])
        assert t >= (1 << 30)                     # the clock really did wrap during the test
        assert states[-1] == jamming.JAM, states
    finally:
        if saved is not None:
            jamming._ticks_diff = saved
