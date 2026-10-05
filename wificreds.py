"""Per-device Wi-Fi credentials, both unique to the board and shown on the Wi-Fi screen.

SSID: 'NMEABridge-' plus 4 characters derived from the board's unique ID (stable across reflashes,
nothing stored). Password: 12 random characters made on first boot and stored in a file.
Kept separate from wifi.py so it can be imported at boot (tiny) while the networking code is only
loaded when the access point is first switched on.
"""

# 31 symbols without look-alikes (no 0/o, 1/l/i): easy to type from a small OLED
ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
PASSWORD_LENGTH = 12                          # ~59 bits; fits one 16-character OLED line after "PW "
PASSWORD_FILE = 'wifi_password.txt'
SSID_PREFIX = 'NMEABridge-'
SSID_SUFFIX_LENGTH = 4                        # 31^4 = ~920k combinations: unique enough among nearby boats
SSID_ALPHABET = ALPHABET.upper()


def random_chars(urandom, length, alphabet):
    """'length' symbols from 'alphabet', each equally likely (bytes that would bias it are skipped)."""
    limit = 256 - 256 % len(alphabet)
    out = ''
    while len(out) < length:
        for b in urandom(length * 2):
            if b < limit:
                out += alphabet[b % len(alphabet)]
                if len(out) == length:
                    break
    return out


def generate_password(urandom, length=PASSWORD_LENGTH):
    """Random password from 'urandom' (os.urandom: hardware entropy on the Pico)."""
    return random_chars(urandom, length, ALPHABET)


def uid_suffix(uid, length=SSID_SUFFIX_LENGTH, alphabet=SSID_ALPHABET):
    """Deterministic 'length' symbols from the board's unique ID bytes.

    A polynomial hash modulo len(alphabet)**length keeps every intermediate value below 2**30 (no
    big integers on MicroPython) and maps IDs uniformly onto all combinations."""
    n = len(alphabet)
    modulus = n ** length
    h = 0
    for b in uid:
        h = (h * 257 + b + 1) % modulus
    out = ''
    for _ in range(length):
        out = alphabet[h % n] + out
        h //= n
    return out


def valid_password(pw):
    """WPA2 passphrase: 8..63 printable ASCII characters."""
    return 8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw)


def _load_or_create(path, is_valid, make):
    """Return (value, persisted): the stored value if usable, else a new one that is saved."""
    try:
        with open(path) as f:
            value = f.read().strip()
        if is_valid(value):
            return value, True
    except OSError:
        pass  # first boot: no file yet
    value = make()
    try:
        with open(path, 'w') as f:
            f.write(value)
    except OSError:
        return value, False
    return value, True


def _default_urandom(urandom):
    if urandom is None:
        import os
        urandom = os.urandom
    return urandom


def get_password(configured='', path=PASSWORD_FILE, urandom=None):
    """Return (password, persisted).

    A non-empty 'configured' password (the WIFI_PASSWORD constant) wins and is never stored.
    Otherwise the stored password is used; if there is none (first boot) or it is unusable, a new
    random one is generated and saved. If saving fails it still works until the next reboot and
    'persisted' is False. Delete the file to get a new password."""
    if configured:
        return configured, True
    urandom = _default_urandom(urandom)
    return _load_or_create(path, valid_password, lambda: generate_password(urandom))


def get_ssid(configured='', uid=None):
    """SSID for this board: 'configured' (the WIFI_SSID constant) if set, else 'NMEABridge-XXXX' where
    XXXX comes from machine.unique_id(). The suffix is only ~20 bits of the 64-bit ID, so two boards
    can in principle collide; set WIFI_SSID to pick another name in that case."""
    if configured:
        return configured
    if uid is None:
        import machine
        uid = machine.unique_id()
    return SSID_PREFIX + uid_suffix(uid)


def regenerate_password(path=PASSWORD_FILE, urandom=None):
    """Replace the stored password with a new random one. Returns (password, persisted)."""
    urandom = _default_urandom(urandom)
    pw = generate_password(urandom)
    try:
        with open(path, 'w') as f:
            f.write(pw)
    except OSError:
        return pw, False
    return pw, True
