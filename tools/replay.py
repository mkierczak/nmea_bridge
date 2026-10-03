"""Replay a recorded NMEA log (one sentence per line) through Parser + JamDetector.

Usage: python3 tools/replay.py LOGFILE [seconds_per_gsv_cycle]
Prints every detector state change so thresholds in jamming.py can be tuned offline.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
from jamming import JamDetector


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cycle_s = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
    parser = NMEA.Parser()
    detector = JamDetector(parser)
    now_ms = 0
    last_version = 0
    last = None
    for line in open(sys.argv[1]):
        parser.parse_sentence(line.strip())
        if parser.cn0_version != last_version:
            last_version = parser.cn0_version
            now_ms += int(cycle_s * 1000)
            state = detector.evaluate(now_ms, parser.fix_type != 'NO')
            if state != last:
                tracked, mean, _ = parser.cn0_stats()
                print('{:7.1f}s {:5} why={:3} cn0={:4.1f}/{:4.1f} n={}/{:.0f}'.format(
                    now_ms / 1000, state[0], state[1], mean, detector.base_mean,
                    tracked, detector.base_tracked))
                last = state


if __name__ == '__main__':
    main()
