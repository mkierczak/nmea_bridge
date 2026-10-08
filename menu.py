"""On-device settings menu for the two-key UI (imported lazily: it is only needed while open).

Keys: UP/DN short = move (or change a value while editing), UP long = select/confirm,
DN long = back/cancel. Pure logic apart from drawing on the given oled object.
"""
import settings as S
from nav import UP_SHORT, UP_LONG, DN_SHORT, DN_LONG

ROWS = 4                         # visible item rows
GROUPS = ('GPS', 'Detection', 'Radio output', 'Anchor', 'Display')
BROWSE_HINT = 'hold UP:ok DN:bk'
EDIT_HINT = 'UP/DN chg hld:ok'
CONFIRM_HINT = 'hold UP=y DN=n'


def _fmt(key, value):
    if isinstance(value, bool):
        return 'on' if value else 'off'
    if key == 'screen_off_s':
        return 'never' if value == 0 else '{}s'.format(value) if value < 60 else '{}m'.format(value // 60)
    return str(value)


def default_hint(cfg, key):
    """'def 30 kn' (and ' *chg' when the setting differs from it): fits the 16-character bottom line."""
    text = 'def {}{}'.format(_fmt(key, cfg.defaults[key]), S.unit(key))
    if cfg.is_overridden(key):
        text += ' *chg'
    return text[:16]


class Menu(object):
    """hooks: 'apply'(key) after a live setting changed, 'reset', 'reboot', and optionally
    'wifi_toggle', 'wifi_active' (a bool-returning callable) and 'regen_password'."""

    def __init__(self, cfg, hooks):
        self.cfg = cfg
        self.hooks = hooks
        self.stack = []              # [(name, selected item, top)] of the parent menus
        self.name = ''               # '' = root
        self.cursor = 0
        self.top = 0
        self.edit = None             # (key, value before editing)
        self.confirm = None          # (question, callable)
        self.message = ''            # one-line notice (e.g. 'save failed') shown until the next key

    # --- structure -------------------------------------------------------------------------
    def items(self):
        name = self.name
        if name == '':
            out = [('act', 'Reboot now *', 'reboot', 'Reboot now?')] if self.cfg.pending_reboot else []
            out += [('sub', g) for g in GROUPS]
            if 'wifi_toggle' in self.hooks:
                out.append(('sub', 'Wi-Fi'))
            out += [('sub', 'Advanced'), ('sub', 'System')]
            return out
        if name == 'Wi-Fi':
            return [('wifi',), ('act', 'New password', 'regen_password', 'New Wi-Fi PW?')]
        if name == 'System':
            return ([('set', row[0]) for row in S.SCHEMA if row[3] == 'System'] +
                    [('act', 'Reset defaults', 'reset', 'Reset all?'),
                     ('act', 'Reboot now', 'reboot', 'Reboot now?')])
        return [('set', row[0]) for row in S.SCHEMA if row[3] == name]

    def _row(self, item):
        """(label, value text) for one item."""
        kind = item[0]
        if kind == 'sub':
            return item[1], '>'
        if kind == 'act':
            return item[1], ''
        if kind == 'wifi':
            return 'Wi-Fi now', 'on' if self.hooks['wifi_active']() else 'off'
        key = item[1]
        row = S.spec(key)
        label = row[1][:9] + '*' if row[4] == S.REBOOT else row[1]
        text = _fmt(key, self.cfg.get(key))
        if self.edit and self.edit[0] == key:
            text = '[' + text + ']'
        return label, text

    # --- input -----------------------------------------------------------------------------
    def handle(self, event):
        """Process one key event; returns 'close' when the menu should be left, else None."""
        self.message = ''
        if self.confirm:
            _, action = self.confirm
            if event == UP_LONG:
                self.confirm = None
                action()
            elif event == DN_LONG:
                self.confirm = None
            return None
        if self.edit:
            self._handle_edit(event)
            return None
        items = self.items()
        if event == UP_SHORT:
            self.cursor = (self.cursor - 1) % len(items)
        elif event == DN_SHORT:
            self.cursor = (self.cursor + 1) % len(items)
        elif event == UP_LONG:
            self._select(items[self.cursor])
        elif event == DN_LONG:
            if not self.stack:
                return 'close'
            self.name, item, self.top = self.stack.pop()
            items = self.items()
            # restore by identity: the reboot banner may have been added/removed at the top meanwhile
            self.cursor = items.index(item) if item in items else 0
        self._scroll()
        return None

    def _scroll(self):
        n = len(self.items())
        self.cursor = min(self.cursor, n - 1)
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + ROWS:
            self.top = self.cursor - ROWS + 1

    def _select(self, item):
        kind = item[0]
        if kind == 'sub':
            self.stack.append((self.name, item, self.top))
            self.name, self.cursor, self.top = item[1], 0, 0
        elif kind == 'wifi':
            self.hooks['wifi_toggle']()
        elif kind == 'act':
            self.confirm = (item[3], self.hooks[item[2]])
        else:
            key = item[1]
            if S.spec(key)[2] == S.BOOL:
                self._set(key, not self.cfg.get(key))
                self._save()
            else:
                self.edit = (key, self.cfg.get(key))

    def _save(self):
        """Persist the settings; a full or read-only filesystem must not take the menu (or the loop) down."""
        try:
            self.cfg.save()
            return True
        except OSError:
            self.message = 'save failed'   # the change stays active until the next reboot
            return False

    def cancel(self):
        """Abandon an unconfirmed edit and any dialog (used when the menu times out)."""
        if self.edit:
            key, original = self.edit
            self._set(key, original)
            self.edit = None
        self.confirm = None

    def _set(self, key, value):
        if self.cfg.set(key, value) and S.spec(key)[4] == S.LIVE:
            self.hooks['apply'](key)

    def _handle_edit(self, event):
        key, original = self.edit
        if event == UP_SHORT or event == DN_SHORT:
            direction = 1 if event == UP_SHORT else -1
            self._set(key, S.next_value(key, self.cfg.get(key), direction))
        elif event == UP_LONG:
            self._save()
            self.edit = None
        elif event == DN_LONG:
            self._set(key, original)
            self.edit = None

    # --- output ----------------------------------------------------------------------------
    def draw(self, oled):
        oled.fill(0)
        oled.text((self.name or 'Menu')[:9], 0, 0, 1)
        if self.cfg.pending_reboot:
            oled.text('*reboot', 72, 0, 1)
        oled.hline(0, 9, 128, 1)
        if self.confirm:
            oled.text(self.confirm[0][:16], 0, 20, 1)
            oled.text(CONFIRM_HINT, 0, 44, 1)
            return
        items = self.items()
        for i in range(ROWS):
            idx = self.top + i
            if idx >= len(items):
                break
            label, value = self._row(items[idx])
            width = 15 - len(value)
            text = label[:width]
            line = text + ' ' * (width - len(text)) + value   # (MicroPython's str has no ljust)
            oled.text(('>' if idx == self.cursor else ' ') + line, 0, 11 + 10 * i, 1)
        oled.text(self.message or (EDIT_HINT if self.edit else self._browse_hint(items)), 0, 56, 1)

    def _browse_hint(self, items):
        """In Advanced the cryptic labels get their default value (and a change marker) instead of the key hint."""
        if self.name == 'Advanced' and items[self.cursor][0] == 'set':
            return default_hint(self.cfg, items[self.cursor][1])
        return BROWSE_HINT
