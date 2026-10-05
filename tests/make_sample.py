"""Generates tests/data/sample.nmea (timestamped input) and tests/data/sample_forwarded.nmea (the exact
bytes the radio must receive). The expected output comes from reference_forwarded(), an independent,
deliberately simple statement of the forwarding policy (it shares no code with the parser or the bridge).

Run `python3 tests/make_sample.py` to regenerate after changing the policy on purpose."""
import os

HERE = os.path.dirname(__file__)
FORWARD_TYPES = ('RMC', 'GGA', 'GSA', 'GSV', 'ZDA')
FORWARD_TALKERS = ('GP', 'GN')


def xor(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return '{:02X}'.format(c)


def sentence(body):
    return '${}*{}'.format(body, xor(body))


def reference_forwarded(lines, forward_types=FORWARD_TYPES, talkers=FORWARD_TALKERS):
    """The policy in plain words: a line is forwarded if it is a well-formed NMEA sentence ('$', '*', two
    correct hex digits, at most 82 characters), its type is wanted and its talker is allowed; $GN becomes
    $GP with a recomputed checksum; every sentence is CRLF terminated."""
    out = []
    for line in lines:
        s = line.strip()
        if not s.startswith('$') or '*' not in s or len(s) > 82:
            continue
        body, _, ck = s[1:].partition('*')
        if len(ck) != 2 or ck.upper() != xor(body):
            continue
        talker, kind = body[0:2], body[2:5]
        if kind not in forward_types or talker not in talkers:
            continue
        if talker == 'GN':
            body = 'GP' + body[2:]
            ck = xor(body)
        out.append('${}*{}\r\n'.format(body, ck))
    return out


def build():
    """[(arrival_ms, line)] for a few seconds of realistic traffic plus the oddities seen on real links."""
    lines = []
    t0 = 100000
    prns = [(1, 45), (3, 41), (8, 38), (11, 33), (14, 30), (22, 27), (28, 24), (30, 21)]
    for k in range(12):
        t = t0 + k * 1000
        hh, mm, ss = 12, 0, k
        utc = '{:02d}{:02d}{:02d}.000'.format(hh, mm, ss)
        lon = 1131.0 + k * 0.0020
        lat_s, lon_s = '4807.0380', '{:09.4f}'.format(lon)
        lines.append((t, sentence('GNRMC,%s,A,%s,N,%s,E,5.00,90.00,040726,,,A' % (utc, lat_s, lon_s))))
        lines.append((t + 20, sentence('GNGGA,%s,%s,N,%s,E,1,10,0.9,12.5,M,46.9,M,,' % (utc, lat_s, lon_s))))
        lines.append((t + 40, sentence('GNZDA,%s,04,07,2026,00,00' % utc)))
        if k % 5 == 0:
            lines.append((t + 60, sentence('GPGSA,A,3,01,03,08,11,14,22,,,,,,,1.8,0.9,1.5')))
            lines.append((t + 70, sentence('BDGSA,A,3,06,09,13,,,,,,,,,,1.8,0.9,1.5')))
            for m in (1, 2):
                chunk = prns[(m - 1) * 4:m * 4]
                fields = ''.join(',%02d,40,083,%d' % p for p in chunk)
                lines.append((t + 80 + m, sentence('GPGSV,2,%d,08%s' % (m, fields))))
            lines.append((t + 90, sentence('BDGSV,1,1,03,06,40,120,36,09,35,200,33,13,20,310,29')))
            lines.append((t + 95, sentence('PMTKSPF,1')))
        if k == 3:   # oddities
            lines.append((t + 100, '$GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00'))  # bad checksum
            lines.append((t + 105, 'garbage without a dollar sign'))
            lines.append((t + 110, sentence('GNGGA,,,,,,,,,,,,,,')))     # valid envelope, nothing readable inside
            lines.append((t + 115, sentence('GPTXT,01,01,02,ANTENNA OK')))
            lines.append((t + 120, sentence('PMTK001,286,3')))
            lines.append((t + 125, sentence('GNRMC,123519')))            # truncated but well-formed
    return lines


def write():
    lines = build()
    with open(os.path.join(HERE, 'data', 'sample.nmea'), 'w') as f:
        for ms, text in lines:
            f.write('{} {}\n'.format(ms, text))
    with open(os.path.join(HERE, 'data', 'sample_forwarded.nmea'), 'w', newline='') as f:
        f.write(''.join(reference_forwarded([t for _, t in lines])))


if __name__ == '__main__':
    write()
    print('written to', os.path.join(HERE, 'data'))
