import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import anchor
import spoofing

# one arc-minute of latitude is 1852 m; positions are in 1e-4 arc-minutes: 1 m = 5.4 units
HOME = (59 * 600000 + 183420, 18 * 600000 + 32190)


def north(m):
    return (HOME[0] + round(m / 0.1852), HOME[1])


class P:
    """A parser stand-in: position and fix counter."""

    def __init__(self, position=HOME):
        self.fix_count = 0
        self.lat_u, self.lon_u = position

    def fix(self, position):
        self.lat_u, self.lon_u = position
        self.fix_count += 1


def test_distance_and_bearing_agree_with_the_spoofing_detector_and_the_compass():
    assert abs(anchor.distance_m(HOME, north(100)) - 100) < 0.5
    assert abs(anchor.distance_m(HOME, north(100)) - spoofing._dist_m(HOME, north(100))) < 1e-6
    assert anchor.bearing_deg(HOME, north(100)) == 0
    assert anchor.bearing_deg(north(100), HOME) == 180
    east = (HOME[0], HOME[1] + 1000)
    assert anchor.bearing_deg(HOME, east) == 90 and anchor.bearing_deg(east, HOME) == 270
    far = (HOME[0] + 1000, HOME[1] - 1000)
    assert anchor.bearing_deg(HOME, far) in range(330, 337)           # north-west (a degree of longitude is shorter up here)
    across = ((0), 108000000 - 100)                                    # either side of the antimeridian
    other = (0, -108000000 + 100)
    assert anchor.distance_m(across, other) < 50


def test_anchor_alarm_needs_a_persistent_drift_and_clears_when_back_inside():
    w, p = anchor.AnchorWatch(radius_m=50), P()
    assert w.update(p, 0) == anchor.OFF
    w.set(HOME)
    assert w.update(p, 500) == anchor.OK and w.is_set
    for pos, expected in ((north(30), anchor.OK), (north(80), anchor.OK), (north(85), anchor.OK),
                          (north(90), anchor.DRAG)):                   # three fixes in a row outside the radius
        p.fix(pos)
        assert w.update(p, 500) == expected
    assert w.alarming and w.distance > 80 and w.bearing == 0 and abs(w.max_distance - 90) < 1
    for pos in (north(40), north(45)):
        p.fix(pos)
        assert w.update(p, 500) == anchor.DRAG                          # not yet: three fixes back inside
    p.fix(north(20))
    assert w.update(p, 500) == anchor.OK and not w.alarming


def test_a_single_glitch_outside_the_radius_is_ignored():
    w, p = anchor.AnchorWatch(radius_m=50), P()
    w.set(HOME)
    for pos in (north(200), HOME, north(200), HOME, north(200), HOME):
        p.fix(pos)
        assert w.update(p, 500) == anchor.OK
    assert not w.alarming


def test_no_fix_for_two_minutes_raises_the_alarm_and_the_fix_coming_back_ends_it():
    w, p = anchor.AnchorWatch(radius_m=50), P()
    w.set(HOME)
    assert w.update(p, anchor.NOFIX_ALARM_MS - 1) == anchor.OK
    assert w.update(p, anchor.NOFIX_ALARM_MS) == anchor.NOFIX and w.alarming
    assert w.update(p, None) == anchor.NOFIX                            # never a fix since boot
    p.fix(north(10))
    assert w.update(p, 1000) == anchor.OK and not w.alarming


def test_set_clear_and_persistence():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'anchor.json')
        w = anchor.AnchorWatch(path, 50)
        assert not w.load()                                              # nothing stored yet
        w.set(HOME)
        assert os.path.exists(path)
        again = anchor.AnchorWatch(path, 80)
        assert again.load() and again.anchor == HOME and again.state == anchor.OK and again.radius_m == 80
        w.clear()
        assert not os.path.exists(path) and w.state == anchor.OFF and not w.is_set
        with open(path, 'w') as f:
            f.write('not json')
        assert not anchor.AnchorWatch(path).load()                       # a corrupt file is no anchor
    nowhere = anchor.AnchorWatch('/nonexistent-dir/anchor.json')
    nowhere.set(HOME)                                                    # a read-only file system: still works
    assert nowhere.is_set


def test_mob_mark():
    m = anchor.MobMark()
    assert not m.active and not m.alerting
    m.set(HOME, 5000)
    assert m.active and m.alerting and m.seconds(65000) == 60
    m.acknowledge()
    assert m.active and not m.alerting
    m.clear()
    assert not m.active and m.position is None
