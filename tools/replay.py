"""Replay a recorded NMEA log through Parser + JamDetector + SpoofDetector.

Usage: python3 tools/replay.py LOGFILE [seconds_per_gsv_cycle]

One sentence per line. A line may start with a local millisecond timestamp ("123456 $GNRMC,...");
without timestamps the local clock is synthesised from GPS time, which disables the time-step check
(T1). Prints every detector state change so thresholds in jamming.py / spoofing.py can be tuned
offline against real recordings (record a long log underway to measure false alarms).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
from jamming import JamDetector
from spoofing import SpoofDetector


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cycle_s = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    parser = NMEA.Parser()
    jam = JamDetector(parser)
    spoof = SpoofDetector(parser)
    gsv_clock_ms = 0
    last_version = 0
    last_jam = last_spoof = None
    first_gps_ms = None
    for line in open(sys.argv[1]):
        line = line.strip()
        stamp = None
        head, _, rest = line.partition(' ')
        if head.isdigit() and rest.startswith('$'):
            stamp, line = int(head), rest
        before = parser.fix_count
        parser.parse_sentence(line, stamp)
        if parser.fix_count != before and stamp is None:
            gps_ms = parser.utc_days * 86400000 + parser.utc_ms
            first_gps_ms = gps_ms if first_gps_ms is None else first_gps_ms
            parser.rx_ms = gps_ms - first_gps_ms   # synthetic local clock
        now = parser.rx_ms if parser.rx_ms is not None else gsv_clock_ms
        result = spoof.evaluate(now)
        if result != last_spoof:
            print('{:9.1f}s SPOOF {:7} {}'.format(now / 1000, *result))
            last_spoof = result
        if parser.cn0_version != last_version:
            last_version = parser.cn0_version
            gsv_clock_ms += int(cycle_s * 1000)
            state = jam.evaluate(gsv_clock_ms, parser.fix_type != 'NO')
            if state != last_jam:
                tracked, mean, _ = parser.cn0_stats()
                print('{:9.1f}s JAM   {:5} why={:4} cn0={:4.1f}/{:4.1f} n={}/{:.0f}'.format(
                    gsv_clock_ms / 1000, state[0], state[1], mean, jam.base_mean,
                    tracked, jam.base_tracked))
                last_jam = state


if __name__ == '__main__':
    main()
