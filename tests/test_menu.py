import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import menu
import settings as S
from nav import UP_SHORT, UP_LONG, DN_SHORT, DN_LONG
from test_settings import full_defaults


class FakeOled:
    def __init__(self):
        self.calls = []

    def fill(self, c):
        self.calls = []

    def text(self, s, x, y, c=1):
        self.calls.append((s, x, y))

    def hline(self, *a):
        pass

    def texts(self):
        return [c[0] for c in self.calls]


class Rig:
    """Menu + settings in a temp dir, with recording hooks."""

    def __init__(self, wifi=True):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'settings.json')
        self.cfg = S.Settings(full_defaults(), self.path)
        self.log = []
        self.wifi_on = False
        hooks = {'apply': lambda k: self.log.append(('apply', k)),
                 'reset': lambda: (self.cfg.reset(), self.log.append(('reset',))),
                 'reboot': lambda: self.log.append(('reboot',))}
        if wifi:
            hooks['wifi_active'] = lambda: self.wifi_on
            hooks['wifi_toggle'] = self.toggle_wifi
            hooks['regen_password'] = lambda: self.log.append(('regen',))
        self.menu = menu.Menu(self.cfg, hooks)

    def toggle_wifi(self):
        self.wifi_on = not self.wifi_on
        self.log.append(('wifi', self.wifi_on))

    def send(self, *events):
        out = None
        for ev in events:
            out = self.menu.handle(ev)
        return out

    def go(self, label):
        """Move the cursor to the item with this label and enter it."""
        items = self.menu.items()
        for i, item in enumerate(items):
            if self.menu._row(item)[0].rstrip('*').strip() == label.rstrip('*').strip():
                while self.menu.cursor != i:
                    self.menu.handle(DN_SHORT)
                self.menu.handle(UP_LONG)
                return
        raise AssertionError('no item ' + label)

    def saved(self):
        return json.load(open(self.path)) if os.path.exists(self.path) else {}


def test_root_structure_with_and_without_wifi():
    r = Rig()
    assert [i[1] for i in r.menu.items() if i[0] == 'sub'] == [
        'GPS', 'Detection', 'Radio output', 'Display', 'Wi-Fi', 'Advanced', 'System']
    r2 = Rig(wifi=False)
    assert 'Wi-Fi' not in [i[1] for i in r2.menu.items()]


def test_navigation_wrap_enter_back_close():
    r = Rig()
    r.send(UP_SHORT)                                   # wraps to the last item
    assert r.menu.cursor == len(r.menu.items()) - 1
    r.send(DN_SHORT)
    assert r.menu.cursor == 0
    r.go('GPS')
    assert r.menu.name == 'GPS' and r.menu.cursor == 0
    assert r.send(DN_LONG) is None and r.menu.name == '' and r.menu.cursor == 0
    assert r.send(DN_LONG) == 'close'


def test_bool_toggle_applies_live_and_saves():
    r = Rig()
    r.go('Detection')
    r.go('Jamming')
    assert r.cfg.get('jam_detect') is False
    assert r.log == [('apply', 'jam_detect')]
    assert r.saved() == {'jam_detect': False}
    r.send(UP_LONG)                                    # cursor is still on Jamming: toggle back
    assert r.cfg.get('jam_detect') is True and r.saved() == {}


def test_choice_edit_confirm_and_reboot_banner():
    r = Rig()
    r.go('GPS')
    r.go('Baudrate')
    assert r.menu.edit and r.menu._row(r.menu.items()[0])[1] == '[4800]'
    r.send(UP_SHORT)
    assert r.cfg.get('gps_baud') == 9600
    assert ('apply', 'gps_baud') not in r.log          # reboot-class: no live hook
    assert r.cfg.pending_reboot
    r.send(UP_LONG)                                    # confirm
    assert r.menu.edit is None and r.saved() == {'gps_baud': 9600}
    r.send(DN_LONG)
    first = r.menu.items()[0]
    assert first[0] == 'act' and 'Reboot' in first[1]  # banner entry at the top of the root


def test_edit_cancel_reverts_and_does_not_save():
    r = Rig()
    r.go('GPS')
    r.go('GNSS mode')
    r.send(UP_SHORT)
    assert r.cfg.get('gnss_mode') == 'GPS'
    r.send(DN_LONG)
    assert r.cfg.get('gnss_mode') == 'GPS+BD' and not r.cfg.pending_reboot and r.saved() == {}


def test_int_edit_previews_live_and_cancel_restores():
    r = Rig()
    r.go('Display')
    r.go('Contrast')
    r.send(UP_SHORT, UP_SHORT)
    assert r.cfg.get('contrast') == 30
    assert r.log == [('apply', 'contrast'), ('apply', 'contrast')]
    r.send(DN_LONG)
    assert r.cfg.get('contrast') == 0 and r.log[-1] == ('apply', 'contrast') and r.saved() == {}
    r.send(DN_SHORT)                                   # not editing any more: moves the cursor


def test_int_edit_stops_at_range_ends():
    r = Rig()
    r.go('Advanced')
    r.go('CN0 drop')
    r.send(*([UP_SHORT] * 30))
    assert r.cfg.get('cn0_drop_db') == 15
    r.send(*([DN_SHORT] * 30))
    assert r.cfg.get('cn0_drop_db') == 3


def test_confirm_dialogs():
    r = Rig()
    r.go('System')
    r.go('Reset defaults')
    assert r.menu.confirm
    r.send(DN_LONG)                                    # "no"
    assert not r.menu.confirm and ('reset',) not in r.log
    r.go('Reset defaults')
    r.send(UP_LONG)                                    # "yes"
    assert ('reset',) in r.log and not r.menu.confirm
    r.go('Reboot now')
    r.send(UP_SHORT)                                   # ignored while confirming
    assert r.menu.confirm and ('reboot',) not in r.log
    r.send(UP_LONG)
    assert ('reboot',) in r.log


def test_wifi_submenu_toggle_and_password_confirm():
    r = Rig()
    r.go('Wi-Fi')
    assert r.menu._row(r.menu.items()[0]) == ('Wi-Fi now', 'off')
    r.send(UP_LONG)
    assert r.log == [('wifi', True)] and r.menu._row(r.menu.items()[0])[1] == 'on'
    r.send(DN_SHORT, UP_LONG)
    assert r.menu.confirm and ('regen',) not in r.log
    r.send(UP_LONG)
    assert ('regen',) in r.log


def test_scrolling_window_follows_cursor():
    r = Rig()
    r.go('Advanced')
    n = len(r.menu.items())
    assert n > menu.ROWS
    for i in range(n - 1):
        r.send(DN_SHORT)
        assert r.menu.top <= r.menu.cursor < r.menu.top + menu.ROWS
    oled = FakeOled()
    r.menu.draw(oled)
    marked = [c for c in oled.calls if c[0].startswith('>')]
    assert len(marked) == 1 and marked[0][0].split()[0] == '>S2'
    r.send(*([UP_SHORT] * (n - 1)))
    assert r.menu.top == 0


def test_everything_drawn_fits_the_display():
    r = Rig()
    r.cfg.set('gps_baud', 115200)
    screens = []
    for name in ('', 'GPS', 'Detection', 'Radio output', 'Display', 'Wi-Fi', 'Advanced', 'System'):
        r.menu.name, r.menu.cursor, r.menu.top = name, 0, 0
        for _ in range(len(r.menu.items())):
            oled = FakeOled()
            r.menu.draw(oled)
            screens.append(oled)
            r.menu.handle(DN_SHORT)
    r.menu.name = 'GPS'
    r.menu.handle(UP_LONG)                              # editing
    oled = FakeOled()
    r.menu.draw(oled)
    screens.append(oled)
    r.menu.edit = None
    r.menu.confirm = ('New Wi-Fi PW?', lambda: None)
    oled = FakeOled()
    r.menu.draw(oled)
    screens.append(oled)
    for o in screens:
        for text, x, y in o.calls:
            assert x + 8 * len(text) <= 128, (text, x)
            assert 0 <= y <= 56, (text, y)
    for hint in (menu.BROWSE_HINT, menu.EDIT_HINT, menu.CONFIRM_HINT):
        assert len(hint) <= 16


def test_screen_off_value_formatting():
    assert menu._fmt('screen_off_s', 0) == 'never'
    assert menu._fmt('screen_off_s', 30) == '30s'
    assert menu._fmt('screen_off_s', 60) == '1m' and menu._fmt('screen_off_s', 300) == '5m'
    assert menu._fmt('jam_detect', True) == 'on' and menu._fmt('gps_baud', 4800) == '4800'


def test_failed_save_does_not_crash_and_is_reported():
    r = Rig()

    def boom():
        raise OSError(28, 'no space left')
    r.cfg.save = boom
    r.go('Detection')
    r.go('Jamming')                                    # bool toggle triggers a save
    assert r.cfg.get('jam_detect') is False            # stays active in RAM
    assert r.menu.message == 'save failed'
    oled = FakeOled()
    r.menu.draw(oled)
    assert 'save failed' in oled.texts()
    r.send(DN_SHORT)
    assert r.menu.message == ''                        # cleared by the next key


def test_failed_save_while_confirming_an_edit_leaves_edit_mode():
    r = Rig()

    def boom():
        raise OSError(28, 'no space left')
    r.cfg.save = boom
    r.go('Display')
    r.go('Contrast')
    r.send(UP_SHORT)
    r.send(UP_LONG)                                    # confirm -> save fails
    assert r.menu.edit is None and r.menu.message == 'save failed'
    assert r.cfg.get('contrast') == 15


def test_cancel_reverts_an_unconfirmed_edit_and_closes_dialogs():
    r = Rig()
    r.go('Display')
    r.go('Contrast')
    r.send(UP_SHORT, UP_SHORT)
    assert r.cfg.get('contrast') == 30
    r.menu.cancel()                                    # what the menu timeout does
    assert r.cfg.get('contrast') == 0 and r.menu.edit is None
    assert r.log[-1] == ('apply', 'contrast') and r.saved() == {}
    r.menu.name = 'System'
    r.menu.confirm = ('Reboot now?', lambda: r.log.append(('reboot',)))
    r.menu.cancel()
    assert r.menu.confirm is None and ('reboot',) not in r.log


def test_parent_cursor_is_restored_by_identity_when_the_banner_appears():
    r = Rig()
    r.go('GPS')                                        # cursor was on GPS (index 0, no banner)
    r.go('Baudrate')
    r.send(UP_SHORT)                                   # 9600: reboot pending -> banner entry appears at the top
    r.send(UP_LONG)
    r.send(DN_LONG)                                    # back to the root
    item = r.menu.items()[r.menu.cursor]
    assert item == ('sub', 'GPS'), item                # not the "Reboot now" entry that shifted into index 0


def test_system_menu_offers_log_raw_before_the_actions():
    r = Rig()
    r.go('System')
    labels = [r.menu._row(i)[0] for i in r.menu.items()]
    assert labels == ['Log raw', 'Reset defaults', 'Reboot now']
    r.send(UP_LONG)                                    # cursor starts on Log raw: toggle it
    assert r.cfg.get('log_raw') is True and r.log == [('apply', 'log_raw')] and r.saved() == {'log_raw': True}
