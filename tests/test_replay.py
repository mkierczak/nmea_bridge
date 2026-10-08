import contextlib
import io
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

import jamming
import replay
import spoofing
from test_jamming import with_checksum
from test_spoofing import _fmt

SAMPLE = os.path.join(os.path.dirname(__file__), 'data', 'sample.nmea')


def make_log(fixes=60, jump_at=None, jump_deg=0.05, timestamps=True):
    """Lines of a vessel doing 5 kn east for `fixes` seconds, with an optional persistent position jump."""
    lines = []
    lat, lon = 59.0, 18.0
    for k in range(fixes):
        lon += 2.57 / (111320.0 * math.cos(math.radians(lat)))
        if jump_at is not None and k == jump_at:
            lat += jump_deg
        t = 12 * 3600 + k
        utc = '{:02d}{:02d}{:02d}.000'.format(t // 3600, t // 60 % 60, t % 60)
        rmc = with_checksum('GNRMC,%s,A,%s,N,%s,E,5.00,90.00,040726,,,A' % (utc, _fmt(lat, 2), _fmt(lon, 3)))
        gga = with_checksum('GNGGA,%s,%s,N,%s,E,1,10,0.9,12.5,M,46.9,M,,' % (utc, _fmt(lat, 2), _fmt(lon, 3)))
        for offset, text in ((0, rmc), (30, gga)):
            lines.append('{} {}'.format(100000 + 1000 * k + offset, text) if timestamps else text)
    return lines


def run(lines, **kw):
    return replay.replay(replay.parse_log(lines), **kw)


def spoof_states(result):
    return [(t[2], t[3]) for t in result['transitions'] if t[1] == 'spoof']


def jam_states(result):
    return [(t[2], t[3]) for t in result['transitions'] if t[1] == 'jam']


def alert_with(result, code):
    return any(state == 'HIGH' and code in reason for state, reason in spoof_states(result))


def test_a_quiet_log_raises_no_alarms_and_the_summary_says_so():
    r = run(make_log())
    assert r['transitions'] == [] and r['episodes'] == {'jam': 0, 'spoof': 0}
    assert abs(r['seconds']['spoof']['OK'] - r['duration_s']) < 1e-6
    text = replay.format_summary(r)
    assert '--- summary ---' in text and 'alarm episodes: 0 (0.00 per hour)' in text


def test_a_persistent_jump_in_the_log_is_reported_as_an_alert():
    r = run(make_log(jump_at=40))
    assert alert_with(r, 'K1')                              # K2 usually joins on the same fix: 'K1K2'
    assert r['episodes']['spoof'] == 1
    assert 'HIGH' in replay.format_transition(r['transitions'][-1])
    assert 'alarm episodes: 1' in replay.format_summary(r)


def test_logs_without_timestamps_replay_the_same_way():
    r = run(make_log(jump_at=40, timestamps=False))
    assert alert_with(r, 'K1')


def test_threshold_overrides_change_the_outcome_and_are_undone_afterwards():
    lines = make_log(jump_at=40, jump_deg=0.0006)          # about 67 m: just above what 60 kn allows
    assert alert_with(run(lines), 'K1')
    r = run(lines, overrides={'max_speed_kn': 100})        # 100 kn allows about 81 m in a second
    assert not alert_with(r, 'K1') and not any('K1' in reason for _, reason in spoof_states(r))
    assert spoofing.MAX_SPEED_KN == 60                     # the modules were left as found
    assert jamming.CN0_DROP_DB == 6 and jamming.TRACKED_DROP_FRACTION == 0.6


def test_override_parsing_validates_and_clamps():
    assert replay.parse_overrides(['cn0_drop_db=500', 'max_speed_kn=45']) == {'cn0_drop_db': 15, 'max_speed_kn': 45}
    for bad in (['nonsense=1'], ['cn0_drop_db'], ['max_speed_kn=fast'], ['gps_baud=9600']):
        try:
            replay.parse_overrides(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_jamming_at_boot_is_seen_by_the_replay():
    lines = ['100000 ' + with_checksum('PMTKSPF,3')]
    for k in range(12):                                     # no fix, nine satellites overhead, none tracked
        lines.append('{} {}'.format(100000 + 5000 * k, with_checksum('GPGSV,1,1,09')))
    r = run(lines)
    assert any(state == 'HIGH' and 'M' in reason and 'F' in reason for state, reason in jam_states(r)), r['transitions']
    assert r['episodes']['jam'] == 1


def test_repl_noise_is_ignored_and_untimestamped_lines_are_kept():
    entries = replay.parse_log(['MEM boot: free 100 B', 'PMTK286 ack: 3', '123 $GPGGA,1*00', '$GPRMC,2*00', '',
                                '456 not a sentence'])
    assert entries == [(123, '$GPGGA,1*00'), (None, '$GPRMC,2*00')]


def test_mixed_logs_reuse_the_previous_arrival_time():
    out = replay.assign_times([(100, 'a'), (None, 'b'), (250, 'c'), (None, 'd')])
    assert out == [(100, 'a'), (100, 'b'), (250, 'c'), (250, 'd')]


def test_the_bundled_sample_replays_quietly():
    with open(SAMPLE) as f:
        r = replay.replay(replay.parse_log(f))
    assert r['episodes'] == {'jam': 0, 'spoof': 0}
    # 60 lines, but the one without a '$' is skipped like REPL noise; one more has a bad checksum
    assert r['counts'] == {'received': 59, 'valid': 58, 'invalid': 1, 'unreadable': 2}


def test_command_line_prints_transitions_and_a_summary(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'log.txt')
        with open(path, 'w') as f:
            f.write('\n'.join(make_log(jump_at=40)) + '\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            replay.main([path, '--baud', '9600', '--gnss', 'gps'])
        text = out.getvalue()
        assert 'SPOOF HIGH' in text and '--- summary ---' in text
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            replay.main([path, '-q'])
        assert 'SPOOF' not in quiet.getvalue() and '--- summary ---' in quiet.getvalue()
        for bad in ([path, '--set', 'nonsense=1'], [os.path.join(d, 'empty.txt')]):
            if len(bad) == 1:
                open(bad[0], 'w').write('only noise\n')
            err = io.StringIO()
            try:
                with contextlib.redirect_stderr(err):
                    replay.main(bad)
            except SystemExit as e:
                assert e.code == 2
            else:
                raise AssertionError('should have exited')
