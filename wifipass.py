"""Wi-Fi access point password: random on first boot, stored on the board, shown on the Wi-Fi screen.

Kept separate from wifi.py so it can be imported at boot (tiny) while the networking code is only
loaded when the access point is first switched on.
"""

# 31 symbols without look-alikes (no 0/o, 1/l/i): easy to type from a small OLED
ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
PASSWORD_LENGTH = 12                          # ~59 bits; fits one 16-character OLED line after "PW "
PASSWORD_FILE = 'wifi_password.txt'
_LIMIT = 256 - 256 % len(ALPHABET)            # reject bytes >= 248 so every symbol is equally likely


def generate_password(urandom, length=PASSWORD_LENGTH):
    """Random password from 'urandom' (os.urandom: hardware entropy on the Pico)."""
    out = ''
    while len(out) < length:
        for b in urandom(length * 2):
            if b < _LIMIT:
                out += ALPHABET[b % len(ALPHABET)]
                if len(out) == length:
                    break
    return out


def valid_password(pw):
    """WPA2 passphrase: 8..63 printable ASCII characters."""
    return 8 <= len(pw) <= 63 and all(32 <= ord(c) < 127 for c in pw)


def get_password(configured='', path=PASSWORD_FILE, urandom=None):
    """Return (password, persisted).

    A non-empty 'configured' password (the WIFI_PASSWORD constant) wins and is never stored.
    Otherwise the stored password is used; if there is none (first boot) or it is unusable, a new
    random one is generated and saved. If saving fails the password still works until the next
    reboot and 'persisted' is False. Delete the file to get a new password."""
    if configured:
        return configured, True
    try:
        with open(path) as f:
            pw = f.read().strip()
        if valid_password(pw):
            return pw, True
    except OSError:
        pass  # first boot: no file yet
    if urandom is None:
        import os
        urandom = os.urandom
    pw = generate_password(urandom)
    try:
        with open(path, 'w') as f:
            f.write(pw)
    except OSError:
        return pw, False
    return pw, True
