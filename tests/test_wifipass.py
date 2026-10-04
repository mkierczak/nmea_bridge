import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import wifipass


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
        pw = wifipass.generate_password(os.urandom)
        assert len(pw) == wifipass.PASSWORD_LENGTH
        assert set(pw) <= set(wifipass.ALPHABET)
        assert not set(pw) & set('0o1li')
        assert wifipass.valid_password(pw)


def test_passwords_differ():
    assert len({wifipass.generate_password(os.urandom) for _ in range(20)}) == 20


def test_rejection_sampling_skips_biased_bytes_and_covers_alphabet():
    # bytes >= 248 must never be used (they would favour the first symbols)
    pw = wifipass.generate_password(fake_urandom([255, 250, 248, 0, 1, 2]), length=6)
    assert pw == 'abc' * 2   # only 0, 1, 2 accepted, in order
    seen = set()
    for _ in range(300):
        seen |= set(wifipass.generate_password(os.urandom))
    assert seen == set(wifipass.ALPHABET)


def test_first_boot_creates_and_second_boot_reuses():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        pw1, stored1 = wifipass.get_password('', path, os.urandom)
        assert stored1 and wifipass.valid_password(pw1)
        assert open(path).read() == pw1
        pw2, stored2 = wifipass.get_password('', path, fake_urandom([0]))   # must not regenerate
        assert pw2 == pw1 and stored2


def test_configured_password_wins_and_is_not_stored():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        assert wifipass.get_password('my-own-secret', path, os.urandom) == ('my-own-secret', True)
        assert not os.path.exists(path)


def test_invalid_stored_password_is_replaced():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        for bad in ('', 'short', 'x' * 64, 'has\x01control'):
            open(path, 'w').write(bad)
            pw, stored = wifipass.get_password('', path, os.urandom)
            assert stored and wifipass.valid_password(pw) and pw != bad
            assert open(path).read() == pw


def test_trailing_newline_in_file_is_tolerated():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'pw.txt')
        open(path, 'w').write('abcdefgh23\n')
        assert wifipass.get_password('', path, os.urandom) == ('abcdefgh23', True)


def test_unwritable_storage_still_returns_a_working_password():
    pw, stored = wifipass.get_password('', '/nonexistent-dir/pw.txt', os.urandom)
    assert not stored and wifipass.valid_password(pw)
