import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import jamming
import settings as S
import spoofing


def full_defaults():
    d = {'gps_baud': 4800, 'gnss_mode': 'GPS+BD', 'jam_detect': True, 'spoof_detect': True,
         'spoof_action': 'display', 'contrast': 0, 'screen_off_s': 0, 'night': False, 'log_raw': False}
    d.update({'fwd_' + t: True for t in ('RMC', 'GGA', 'GSA', 'GSV', 'ZDA')})
    d.update(S.threshold_defaults(jamming, spoofing))
    return d


def make(d):
    return S.Settings(full_defaults(), os.path.join(d, 'settings.json'))


def test_schema_defaults_are_all_valid_and_labels_fit():
    cfg = S.Settings(full_defaults(), '/nonexistent/settings.json')
    assert cfg.values == cfg.defaults
    for row in S.SCHEMA:
        assert len(row[1]) <= 10, row
        assert cfg.get(row[0]) == S.coerce(row[0], cfg.get(row[0]))   # defaults sit on the grid


def test_missing_default_raises():
    d = full_defaults()
    del d['gps_baud']
    try:
        S.Settings(d, '/x')
    except KeyError:
        return
    raise AssertionError


def test_coerce_rules():
    assert S.coerce('jam_detect', False) is False
    for bad in (1, 'yes', None):
        try:
            S.coerce('jam_detect', bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert S.coerce('gps_baud', 9600) == 9600
    try:
        S.coerce('gps_baud', 1200)
    except ValueError:
        pass
    else:
        raise AssertionError
    assert S.coerce('cn0_drop_db', 99) == 15 and S.coerce('cn0_drop_db', -4) == 3
    assert S.coerce('max_speed_kn', 63) == 65        # snapped to the step grid
    try:
        S.coerce('cn0_drop_db', True)
    except ValueError:
        pass
    else:
        raise AssertionError('bool accepted as int')


def test_next_value_choice_wraps_int_stops():
    assert S.next_value('gps_baud', 115200, 1) == 4800
    assert S.next_value('gps_baud', 4800, -1) == 115200
    assert S.next_value('jam_detect', True, 1) is False
    assert S.next_value('contrast', 255, 1) == 255 and S.next_value('contrast', 0, -1) == 0
    assert S.next_value('contrast', 0, 1) == 15


def test_save_load_roundtrip_stores_only_overrides():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        assert cfg.set('jam_detect', False) is True
        assert cfg.set('gps_baud', 4800) is False       # same as default
        cfg.save()
        assert json.load(open(os.path.join(d, 'settings.json'))) == {'jam_detect': False}
        assert not os.path.exists(os.path.join(d, 'settings.json.tmp'))
        cfg2 = make(d)
        cfg2.load()
        assert cfg2.get('jam_detect') is False and cfg2.get('spoof_detect') is True


def test_saving_back_to_defaults_removes_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        cfg.set('contrast', 45)
        cfg.save()
        cfg.set('contrast', 0)
        cfg.save()
        assert not os.path.exists(os.path.join(d, 'settings.json'))


def test_invalid_corrupt_and_unknown_entries_are_ignored():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'settings.json')
        open(path, 'w').write(json.dumps({'jam_detect': 'maybe', 'gps_baud': 1234,
                                          'cn0_drop_db': 99, 'bogus': 1, 'contrast': 30}))
        cfg = make(d)
        cfg.load()
        assert cfg.get('jam_detect') is True and cfg.get('gps_baud') == 4800
        assert cfg.get('cn0_drop_db') == 15 and cfg.get('contrast') == 30
        for junk in ('{not json', '[1, 2]', ''):
            open(path, 'w').write(junk)
            cfg = make(d)
            cfg.load()
            assert cfg.values == cfg.defaults


def test_changed_default_still_applies_to_untouched_settings():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        cfg.set('jam_detect', False)
        cfg.save()
        newer = full_defaults()
        newer['gps_baud'] = 9600                       # a later release changes a default
        cfg2 = S.Settings(newer, os.path.join(d, 'settings.json'))
        cfg2.load()
        assert cfg2.get('gps_baud') == 9600 and cfg2.get('jam_detect') is False


def test_pending_reboot_only_for_reboot_class_and_reverts():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        cfg.set('jam_detect', False)
        assert not cfg.pending_reboot                  # live setting
        cfg.set('gps_baud', 9600)
        assert cfg.pending_reboot
        cfg.set('gps_baud', 4800)
        assert not cfg.pending_reboot
        cfg.set('gnss_mode', 'GPS')
        cfg.save()
        cfg2 = make(d)
        cfg2.load()                                    # next boot starts from the saved value
        assert cfg2.get('gnss_mode') == 'GPS' and not cfg2.pending_reboot


def test_reset_restores_defaults_and_deletes_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        cfg.set('contrast', 90)
        cfg.save()
        cfg.reset()
        assert cfg.values == cfg.defaults and not os.path.exists(os.path.join(d, 'settings.json'))


def test_apply_thresholds_pushes_values_and_defaults_are_neutral():
    saved = {(m, n): getattr(m, n) for m, names in (
        (jamming, ('CN0_DROP_DB', 'TRACKED_DROP_FRACTION', 'ENTER_CYCLES', 'EXIT_CYCLES')),
        (spoofing, ('MAX_SPEED_KN', 'TIME_JUMP_MS', 'UNIFORM_STD_DB', 'ALT_STEP_M', 'LATCH_MS',
                    'WARMUP_FIXES', 'ELEV_CORR_MAX', 'ELEV_CYCLES'))) for n in names}
    try:
        cfg = S.Settings(full_defaults(), '/nonexistent/s.json')
        S.apply_thresholds(cfg, jamming, spoofing)       # defaults must leave the constants unchanged
        assert {k: getattr(*k) for k in saved} == saved
        cfg.set('cn0_drop_db', 9)
        cfg.set('tracked_drop_pct', 50)
        cfg.set('latch_min', 2)
        cfg.set('uniform_std_x10', 20)
        cfg.set('elev_corr_x100', -30)
        cfg.set('elev_cycles', 20)
        S.apply_thresholds(cfg, jamming, spoofing)
        assert jamming.CN0_DROP_DB == 9 and jamming.TRACKED_DROP_FRACTION == 0.5
        assert spoofing.LATCH_MS == 120000 and spoofing.UNIFORM_STD_DB == 2.0
        assert spoofing.ELEV_CORR_MAX == -0.3 and spoofing.ELEV_CYCLES == 20
    finally:
        for (m, n), v in saved.items():
            setattr(m, n, v)


def test_failed_rename_keeps_the_old_settings_file_and_removes_the_temp_file():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        cfg.set('contrast', 30)
        cfg.save()
        old = open(os.path.join(d, 'settings.json')).read()
        cfg.set('contrast', 45)
        real = os.rename

        def boom(a, b):
            raise OSError(5, 'io error')
        os.rename = boom
        try:
            try:
                cfg.save()
            except OSError:
                pass
            else:
                raise AssertionError('save should have failed')
        finally:
            os.rename = real
        assert open(os.path.join(d, 'settings.json')).read() == old
        assert os.listdir(d) == ['settings.json']


def test_rename_over_existing_file_fallback_only_for_eexist():
    with tempfile.TemporaryDirectory() as d:
        cfg = make(d)
        cfg.set('contrast', 30)
        cfg.save()
        cfg.set('contrast', 45)
        real = os.rename
        calls = {'n': 0}

        def fat_like(a, b):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OSError(17, 'exists')
            real(a, b)
        os.rename = fat_like
        try:
            cfg.save()
        finally:
            os.rename = real
        assert json.load(open(os.path.join(d, 'settings.json'))) == {'contrast': 45}


def test_choice_values_must_have_the_exact_type():
    assert S.coerce('screen_off_s', 60) == 60 and S.coerce('screen_off_s', 0) == 0
    for key, bad in (('screen_off_s', False), ('screen_off_s', 60.0), ('screen_off_s', '60'), ('gps_baud', 4800.0),
                     ('gps_baud', True), ('gnss_mode', 'gps+bd'), ('spoof_action', None)):
        try:
            S.coerce(key, bad)
        except ValueError:
            continue
        raise AssertionError((key, bad))
    assert S.coerce('gnss_mode', 'GPS+BD') == 'GPS+BD'


def test_hand_edited_lookalike_values_in_the_file_are_ignored():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, 'settings.json'), 'w').write(
            json.dumps({'screen_off_s': False, 'gps_baud': 9600.0, 'contrast': 30}))
        cfg = make(d)
        cfg.load()
        assert cfg.get('screen_off_s') == 0 and cfg.get('gps_baud') == 4800   # both ignored
        assert cfg.get('contrast') == 30                                      # the valid entry still applies
