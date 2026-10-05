import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import wificreds


def fake_urandom(values):
    """urandom replacement cycling through a fixed byte pattern."""
    state = {'i': 0}

    def urandom(n):
        out = bytes(values[(state['i'] + k) % len(values)] for k in range(n))
        state['i'] += n
        return out
    return urandom


def test_generated_password_shape_and_alphabet():
    for _ in range(50):
        pw = wificreds.generate_password(os.urandom)
        assert len(pw) == wificreds.PASSWORD_LENGTH
        assert set(pw) <= set(wificreds.ALPHABET)
        assert not set(pw) & set('0o1li')
        assert wificreds.valid_password(pw)


def test_passwords_differ():
    assert len({wificreds.generate_password(os.urandom) for _ in range(20)}) == 20


def test_rejection_sampling_skips_biased_bytes_and_covers_alphabet():
    # bytes >= 248 must never be used (they would favour the first symbols)
    pw = wificreds.generate_password(fake_urandom([255, 250, 248, 0, 1, 2]), length=6)
    assert pw == 'abc' * 2   # only 0, 1, 2 accepted, in order
    seen = set()
    for _ in range(300):
        seen |= set(wificreds.generate_password(os.urandom))
    assert seen == set(wificreds.ALPHABET)


def test_first_boot_creates_and_second_boot_reuses():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        pw1, stored1 = wificreds.get_password('', path, os.urandom)
        assert stored1 and wificreds.valid_password(pw1)
        assert open(path).read() == pw1
        pw2, stored2 = wificreds.get_password('', path, fake_urandom([0]))   # must not regenerate
        assert pw2 == pw1 and stored2


def test_configured_password_wins_and_is_not_stored():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        assert wificreds.get_password('my-own-secret', path, os.urandom) == ('my-own-secret', True)
        assert not os.path.exists(path)


def test_invalid_stored_password_is_replaced():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        for bad in ('', 'short', 'x' * 64, 'has\x01control'):
            open(path, 'w').write(bad)
            pw, stored = wificreds.get_password('', path, os.urandom)
            assert stored and wificreds.valid_password(pw) and pw != bad
            assert open(path).read() == pw


def test_trailing_newline_in_file_is_tolerated():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        open(path, 'w').write('abcdefgh23\n')
        assert wificreds.get_password('', path, os.urandom) == ('abcdefgh23', True)


def test_unwritable_storage_still_returns_a_working_password():
    pw, stored = wificreds.get_password('', '/nonexistent-dir/pw.txt', os.urandom)
    assert not stored and wificreds.valid_password(pw)


def test_ssid_is_derived_from_uid_and_stable():
    uid = bytes([0xE6, 0x61, 0x64, 0x08, 0x43, 0x7A, 0x1B, 0x26])
    ssid = wificreds.get_ssid('', uid)
    assert ssid == wificreds.get_ssid('', uid)                         # deterministic
    assert ssid.startswith('NMEABridge-') and len(ssid) == len('NMEABridge-') + 4 and len(ssid) <= 32
    assert set(ssid[len('NMEABridge-'):]) <= set(wificreds.SSID_ALPHABET)
    assert ssid == 'NMEABridge-' + wificreds.uid_suffix(uid)
    # regression vector: the mapping must never change between releases (SSIDs would change)
    assert wificreds.uid_suffix(bytes([1, 2, 3, 4, 5, 6, 7, 8])) == 'PW96'


def test_different_ids_give_different_suffixes_mostly():
    seen = {}
    collisions = 0
    for _ in range(1000):
        uid = os.urandom(8)
        s = wificreds.uid_suffix(uid)
        assert len(s) == 4 and set(s) <= set(wificreds.SSID_ALPHABET)
        collisions += s in seen
        seen[s] = uid
    assert collisions < 6      # ~0.5 expected for 1000 ids over 923,521 values
    base = bytes([9, 8, 7, 6, 5, 4, 3, 2])
    for k in range(8):         # changing any single byte changes the suffix
        other = bytearray(base)
        other[k] ^= 0x01
        assert wificreds.uid_suffix(bytes(other)) != wificreds.uid_suffix(base)


def test_suffix_covers_the_whole_alphabet_roughly_uniformly():
    counts = {}
    for _ in range(3000):
        for c in wificreds.uid_suffix(os.urandom(8)):
            counts[c] = counts.get(c, 0) + 1
    assert set(counts) == set(wificreds.SSID_ALPHABET)
    assert max(counts.values()) < 2 * min(counts.values())


def test_ssid_configured_wins():
    assert wificreds.get_ssid('MyBoat', bytes(8)) == 'MyBoat'


def test_ssid_and_password_are_independent():
    with tempfile.TemporaryDirectory() as d:
        pw, _ = wificreds.get_password('', os.path.join(d, 'pw.txt'), os.urandom)
    ssid = wificreds.get_ssid('', os.urandom(8))
    assert pw not in ssid and ssid[len('NMEABridge-'):] not in pw.upper()


def test_password_file_is_written_atomically_and_leaves_no_temp_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        pw, stored = wificreds.get_password('', path, os.urandom)
        assert stored and open(path).read() == pw
        assert os.listdir(d) == ['pw.txt']                    # the temp file was renamed away
        pw2, _ = wificreds.regenerate_password(path, os.urandom)
        assert pw2 != pw and open(path).read() == pw2 and os.listdir(d) == ['pw.txt']


def test_failed_write_keeps_the_old_password_file_untouched(monkeypatch=None):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        wificreds.get_password('', path, os.urandom)
        old = open(path).read()
        real_rename = os.rename

        def boom(a, b):
            raise OSError(5, 'io error')
        os.rename = boom
        try:
            pw, stored = wificreds.regenerate_password(path, os.urandom)
        finally:
            os.rename = real_rename
        assert not stored and wificreds.valid_password(pw)
        assert open(path).read() == old                       # the previous password was not destroyed


def test_invalid_configured_password_is_ignored():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        for bad in ('short', 'x' * 64, 'has\x01control'):
            pw, stored = wificreds.get_password(bad, path, os.urandom)
            assert pw != bad and wificreds.valid_password(pw)
        assert wificreds.get_password('my-own-secret', path, os.urandom) == ('my-own-secret', True)
