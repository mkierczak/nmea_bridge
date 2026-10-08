"""The history of alerts (and a pattern for the buzzer): pure logic, no hardware imports.

AlertLog keeps the last few alerts in memory (they are gone after a reboot), newest last. Each entry is
(time text, label, detail): '12:34', 'SPF!', 'K1T1'. The buzzer pattern says whether the buzzer should sound at
a given moment for a given alarm level.
"""
LOG_MAX = 10

NONE, MEDIUM, HIGH, URGENT = 0, 1, 2, 3     # alarm levels: URGENT is the anchor drag and man overboard


class AlertLog(object):

    def __init__(self, size=LOG_MAX):
        self.size = size
        self.entries = []          # oldest first
        self.total = 0             # alerts since boot, also those that have scrolled out of the log

    def add(self, when, label, detail=''):
        self.entries.append((when, label, detail))
        self.total += 1
        if len(self.entries) > self.size:
            del self.entries[0]

    def newest(self, count):
        """The last 'count' entries, newest first."""
        return self.entries[::-1][:count]


def sound_on(level, t_ms):
    """Whether the buzzer should sound at t_ms (any steadily advancing clock) for an alarm level:
    MEDIUM a short beep every 3 s, HIGH a beep twice a second, URGENT rapid, nearly continuous beeping."""
    if level == MEDIUM:
        return t_ms % 3000 < 150
    if level == HIGH:
        return t_ms % 1000 < 250 or 500 <= t_ms % 1000 < 750
    if level == URGENT:
        return t_ms % 300 < 200
    return False
