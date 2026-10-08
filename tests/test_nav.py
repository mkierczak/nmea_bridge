import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import nav
from nav import Navigator, classify, UP_SHORT, UP_LONG, DN_SHORT, DN_LONG, WIFI


def test_classify_presses():
    assert classify('UP', 100) == UP_SHORT
    assert classify('UP', 1000) == UP_LONG and classify('UP', 2999) == UP_LONG
    assert classify('UP', 3000) == WIFI
    assert classify('DN', 100) == DN_SHORT and classify('DN', 1500) == DN_LONG
    assert classify('DN', 9000) == DN_LONG            # the Wi-Fi gesture is UP only
    assert classify('UP', 5000, wifi_ms=None) == UP_LONG


def test_page_cycle_both_directions_and_wrap():
    n = Navigator()
    assert n.page == nav.PAGE_MAIN
    for expected in nav.PAGES[1:]:
        assert n.handle(UP_SHORT) is None and n.page == expected
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_MAIN                    # wrapped
    n.handle(DN_SHORT)
    assert n.page == nav.PAGES[-1]


def test_dn_long_returns_to_main():
    n = Navigator()
    n.handle(UP_SHORT)
    n.handle(UP_SHORT)
    n.handle(DN_LONG)
    assert n.page == nav.PAGE_MAIN


def test_long_down_on_the_main_page_opens_the_menu_then_events_are_forwarded():
    n = Navigator()
    assert n.page == nav.PAGE_MAIN
    assert n.handle(UP_LONG) is None and not n.in_menu         # a long UP has no function on the pages
    assert n.handle(DN_LONG) == nav.OPEN_MENU and n.in_menu
    for ev in (UP_SHORT, DN_SHORT, UP_LONG, DN_LONG):
        assert n.handle(ev) == nav.TO_MENU
    assert n.page == nav.PAGE_MAIN                    # pages untouched while in the menu
    n.close_menu()
    assert not n.in_menu and n.handle(UP_SHORT) is None and n.page != nav.PAGE_MAIN


def test_wifi_gesture_toggles_outside_the_menu_but_is_a_long_up_inside_it():
    n = Navigator()
    assert n.normalize(WIFI) == WIFI and n.handle(n.normalize(WIFI)) == nav.TOGGLE_WIFI
    n.handle(DN_LONG)                                  # open the menu (long DOWN on the Main page)
    ev = n.normalize(WIFI)
    assert ev == UP_LONG                               # a slightly long confirm is just a confirm
    assert n.handle(ev) == nav.TO_MENU and n.in_menu
    assert n.normalize(DN_SHORT) == DN_SHORT


def test_custom_page_list_and_unknown_page():
    pages = (nav.PAGE_MAIN, nav.PAGE_STATS)
    n = Navigator(pages)
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_STATS
    n.page = nav.PAGE_WIFI                            # not in this build's list
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_MAIN


def test_button_tracker_pairs_press_and_release_and_classifies():
    b = nav.ButtonTracker('UP')
    assert b.edge(0, 1000) is None
    assert b.edge(1, 1200) == UP_SHORT
    assert b.edge(0, 2000) is None and b.edge(1, 3100) == UP_LONG
    assert b.edge(0, 5000) is None and b.edge(1, 8100) == WIFI
    d = nav.ButtonTracker('DN')
    d.edge(0, 0)
    assert d.edge(1, 1500) == DN_LONG


def test_button_tracker_ignores_bounce():
    b = nav.ButtonTracker('UP')
    assert b.edge(0, 1000) is None
    assert b.edge(1, 1005) is None                     # bounce right after the press edge
    assert b.edge(0, 1012) is None
    assert b.edge(1, 1300) == UP_SHORT                 # the real release, exactly one event
    assert b.edge(0, 2000) is None
    assert b.edge(1, 2400) == UP_SHORT
    assert b.edge(1, 2410) is None                     # bounce after the release: no second event
    assert b.edge(0, 2415) is None                     # ... and it must not start a phantom press
    assert b.edge(0, 3000) is None                     # a later, real press still works
    assert b.edge(1, 3200) == UP_SHORT


def test_release_without_a_press_is_ignored():
    b = nav.ButtonTracker('UP')
    assert b.edge(1, 123456789) is None                # used to be classified as a WIFI hold of "uptime" length
    assert b.edge(0, 200000000) is None
    assert b.edge(1, 200000100) == UP_SHORT


def test_button_tracker_survives_tick_wrap():
    saved = nav._ticks_diff
    nav._ticks_diff = lambda a, b: ((a - b + (1 << 29)) & ((1 << 30) - 1)) - (1 << 29)
    try:
        b = nav.ButtonTracker('UP')
        b.edge(0, (1 << 30) - 100)
        assert b.edge(1, 400) == UP_SHORT              # 500 ms across the wrap
    finally:
        nav._ticks_diff = saved


def test_event_queue_fifo_full_and_empty():
    q = nav.EventQueue(4)
    assert q.get() is None and q.drain() == []
    assert q.put('a') and q.put('b') and q.put('c')
    assert not q.put('d')                              # one slot is kept free: full, newest dropped
    assert q.drain() == ['a', 'b', 'c']
    for i in range(10):                                # wraps around many times
        assert q.put(i) and q.get() == i
    assert q.drain() == []


def test_long_down_elsewhere_goes_back_to_main_and_only_then_opens_the_menu():
    n = Navigator()
    n.handle(UP_SHORT)
    n.handle(UP_SHORT)
    assert n.page != nav.PAGE_MAIN
    assert n.handle(DN_LONG) is None and n.page == nav.PAGE_MAIN and not n.in_menu
    assert n.handle(DN_LONG) == nav.OPEN_MENU and n.in_menu     # the second long press enters the menu
    n.handle(DN_LONG)                                           # in the menu it is "back"
    n.close_menu()
    assert n.handle(DN_LONG) == nav.OPEN_MENU                   # and again from the Main page


def test_button_tracker_reports_how_long_a_key_has_been_held():
    t = nav.ButtonTracker('UP', 1000, 3000)
    assert t.held_ms(500) is None
    t.edge(0, 1000)
    assert t.held_ms(1700) == 700
    assert t.edge(1, 2000) == nav.UP_LONG
    assert t.held_ms(2100) is None
