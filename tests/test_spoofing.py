import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
import jamming
import spoofing
from spoofing import SpoofDetector
from test_jamming import gsv, with_checksum, warmed_up


def _fmt(value, deg_digits):
    deg = int(abs(value))
    return '{:0{w}d}{:07.4f}'.format(deg, (abs(value) - deg) * 60, w=deg_digits)


def rmc(t_s, lat, lon, sog=5.0, status='A', date='040726'):
    h, rem = divmod(int(t_s), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((t_s - int(t_s)) * 1000))
    body = 'GNRMC,{:02d}{:02d}{:02d}.{:03d},{},{},{},{},{},{:.2f},90.0,{},,,A'.format(
        h, m, s, ms, status, _fmt(lat, 2), 'N' if lat >= 0 else 'S',
        _fmt(lon, 3), 'E' if lon >= 0 else 'W', sog, date)
    return with_checksum(body)


class Sim:
    """Feeds fixes to a parser + detector with controllable GPS and local clocks."""

    def __init__(self, lat=59.0, lon=18.0):
        self.p = NMEA.Parser()
        self.det = SpoofDetector(self.p)
        self.gps_t = 12 * 3600.0
        self.local_ms = 1000
        self.lat, self.lon = lat, lon
        self.state = spoofing.OK

    def fix(self, dt=0.8, east_kn=5.0, sog=None, gps_dt=None, jump_lat=0.0):
        """Advance one fix: move east at east_kn, optionally jump north by jump_lat degrees."""
        self.gps_t += dt if gps_dt is None else gps_dt
        self.local_ms += int(dt * 1000)
        m_per_s = east_kn / 1.943844
        self.lon += m_per_s * dt / (111320.0 * math.cos(math.radians(self.lat)))
        self.lat += jump_lat
        s = rmc(self.gps_t, self.lat, self.lon, east_kn if sog is None else sog)
        assert self.p.parse_sentence(s, self.local_ms)
        self.state = self.det.evaluate(self.local_ms)[0]
        return self.state

    def warm(self, n=spoofing.WARMUP_FIXES + 5):
        for _ in range(n):
            assert self.fix() == spoofing.OK
        assert self.det.armed


def test_parser_fix_fields():
    p = NMEA.Parser()
    assert p.parse_sentence(rmc(45045.5, -33.8688, -151.2093, sog=12.3), rx_ms=777)
    assert p.fix_count == 1
    assert p.lat_u < 0 and p.lon_u < 0
    assert abs(p.lat_u / 600000.0 + 33.8688) < 1e-4
    assert abs(p.lon_u / 600000.0 + 151.2093) < 1e-4
    assert p.sog_kn == 12.3
    assert p.utc_ms == (12 * 3600 + 30 * 60 + 45) * 1000 + 500
    assert p.utc_days == 20638  # 2026-07-04
    assert p.rx_ms == 777


def test_bad_optional_fix_fields_do_not_invalidate_sentence():
    p = NMEA.Parser()
    s = with_checksum('GNRMC,123519.000,A,4807.0380,N,01131.0000,E,,,,,,A')  # no date/speed
    assert p.parse_sentence(s, 1)
    assert p.fix_count == 0 and p.lat == '4807.0380'


def test_void_rmc_does_not_count_as_fix():
    p = NMEA.Parser()
    assert p.parse_sentence(rmc(1000.0, 59.0, 18.0, status='V'), 1)
    assert p.fix_count == 0


def test_gga_altitude_and_pmtkspf_and_bd_gsa():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,12.5,M,46.9,M,,'))
    assert p.alt_m == 12.5 and p.alt_version == 1
    assert p.parse_sentence(with_checksum('PMTKSPF,3'))
    assert p.module_jam_status == 3
    assert p.parse_sentence(with_checksum('GPGSA,A,3,01,02,03,04,05,06,,,,,,,1.0,1.0,1.0'))
    assert p.parse_sentence(with_checksum('BDGSA,A,3,01,02,03,,,,,,,,,,1.0,1.0,1.0'))
    assert p.birds_GPS == 6 and p.birds_BD == 3


def test_gsv_keeps_elevation():
    p = NMEA.Parser()
    assert p.parse_sentence(with_checksum('BDGSV,1,1,02,05,60,100,41,07,15,200,30'))
    assert p.sats_by_talker['BD'] == [(5, 60, 41), (7, 15, 30)]


def test_steady_track_stays_ok():
    sim = Sim()
    for _ in range(120):
        assert sim.fix(east_kn=5.0) == spoofing.OK


def test_fast_plausible_vessel_is_ok():
    sim = Sim()
    for _ in range(120):
        assert sim.fix(east_kn=35.0) == spoofing.OK


def test_persistent_jump_is_alert():
    sim = Sim()
    sim.warm()
    sim.fix(jump_lat=0.05)           # ~5.5 km north
    assert sim.state == spoofing.OK  # unconfirmed: could be a glitch
    assert sim.fix() == spoofing.ALERT
    assert 'K1' in sim.det.reason


def test_single_glitch_is_ignored():
    sim = Sim()
    sim.warm()
    sim.fix(jump_lat=0.05)
    sim.fix(jump_lat=-0.05)          # back where we were
    for _ in range(40):
        assert sim.fix() == spoofing.OK


def test_jump_during_warmup_is_ignored():
    sim = Sim()
    for _ in range(5):
        sim.fix()
    sim.fix(jump_lat=0.05)
    assert sim.fix() == spoofing.OK
    assert not sim.det.armed


def test_gps_time_step_is_alert():
    sim = Sim()
    sim.warm()
    assert sim.fix(gps_dt=0.8 + 3.0) == spoofing.ALERT   # GPS clock jumped 3 s, local did not
    assert 'T1' in sim.det.reason


def test_time_going_backwards_is_alert():
    sim = Sim()
    sim.warm()
    assert sim.fix(gps_dt=-20.0) == spoofing.ALERT


def test_movement_without_reported_speed_is_suspect():
    sim = Sim()
    sim.warm()
    states = [sim.fix(east_kn=30.0, sog=0.0) for _ in range(20)]
    assert spoofing.SUSPECT in states and spoofing.ALERT not in states
    assert 'K2' in sim.det.reason


def test_alert_latches_then_clears():
    sim = Sim()
    sim.warm()
    sim.fix(jump_lat=0.05)
    assert sim.fix() == spoofing.ALERT
    sim.local_ms += spoofing.WINDOW_MS + 5000        # evidence expired, latch still holds
    assert sim.det.evaluate(sim.local_ms)[0] == spoofing.ALERT
    assert sim.det.reason == 'K1'
    sim.local_ms += spoofing.LATCH_MS
    assert sim.det.evaluate(sim.local_ms)[0] == spoofing.OK


def test_altitude_step_alone_is_not_alarming():
    sim = Sim()
    sim.warm()
    for alt in (5.0, 80.0, 5.0):
        sim.p.parse_sentence(with_checksum('GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,{},M,46.9,M,,'.format(alt)))
        sim.det.evaluate(sim.local_ms)
    assert 'K3' in sim.det.reason
    assert sim.det.state == spoofing.OK    # one weak indicator class is not enough


def test_uniform_cn0_is_suspect():
    sim = Sim()
    sim.warm()
    gsv(sim.p, [40, 40, 41, 40, 40, 41, 40, 40])
    assert sim.det.evaluate(sim.local_ms)[0] == spoofing.SUSPECT
    assert 'S1' in sim.det.reason


def test_natural_cn0_spread_is_ok():
    sim = Sim()
    sim.warm()
    for _ in range(10):
        gsv(sim.p, [48, 44, 41, 38, 35, 31, 28, 25])
        assert sim.det.evaluate(sim.local_ms)[0] == spoofing.OK


def test_cross_constellation_shift_is_flagged():
    sim = Sim()
    sim.warm()
    for _ in range(spoofing.BASELINE_MIN_SAMPLES + 2):
        gsv(sim.p, [45, 40, 36, 30], talker='GP')
        gsv(sim.p, [43, 38, 34, 28], talker='BD')
        sim.det.evaluate(sim.local_ms)
    assert sim.det.state == spoofing.OK
    gsv(sim.p, [45, 40, 36, 30], talker='GP')
    gsv(sim.p, [30, 25, 21, 15], talker='BD')           # BeiDou level dropped ~13 dB vs GPS
    sim.det.evaluate(sim.local_ms)
    assert 'C1' in sim.det.reason


def test_module_jamming_status_feeds_jam_detector():
    p, det, clk = warmed_up()
    assert p.parse_sentence(with_checksum('PMTKSPF,3'))
    for _ in range(jamming.ENTER_CYCLES):
        gsv(p, [40, 38, 42, 36, 41, 39, 37, 40])
        state = clk.step(det)
    assert state == jamming.JAM
    assert 'M' in det.reason
