import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import alerts


def test_log_keeps_the_newest_and_counts_all():
    log = alerts.AlertLog(3)
    for i in range(5):
        log.add('12:0{}'.format(i), 'SPF?', 'K{}'.format(i))
    assert [e[2] for e in log.entries] == ['K2', 'K3', 'K4'] and log.total == 5
    assert [e[2] for e in log.newest(2)] == ['K4', 'K3']
    assert alerts.AlertLog().newest(5) == []


def test_buzzer_patterns_by_level():
    def on_ms(level, span=3000):
        return sum(1 for t in range(0, span, 10) if alerts.sound_on(level, t)) * 10
    assert on_ms(alerts.NONE) == 0
    assert alerts.sound_on(alerts.MEDIUM, 0) and not alerts.sound_on(alerts.MEDIUM, 200)
    assert on_ms(alerts.MEDIUM) == 150                                  # one short beep in three seconds
    assert on_ms(alerts.HIGH, 1000) == 500                              # two beeps a second
    assert on_ms(alerts.URGENT, 3000) == 2000                           # rapid, two thirds of the time on
    assert on_ms(alerts.HIGH) > on_ms(alerts.MEDIUM) * 6 and on_ms(alerts.URGENT) > on_ms(alerts.HIGH)
    for level in (alerts.MEDIUM, alerts.HIGH, alerts.URGENT):
        assert alerts.sound_on(level, 10 ** 9 + 3) in (True, False)    # any clock value works
