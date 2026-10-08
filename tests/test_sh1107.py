import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class _FrameBuffer(object):
    def __init__(self, buf, width, height, fmt):
        self.buf = buf

    def fill(self, c):
        for i in range(len(self.buf)):
            self.buf[i] = c * 0xFF


def _load():
    fb = types.ModuleType('framebuf')
    fb.FrameBuffer = _FrameBuffer
    fb.MONO_VLSB, fb.MONO_HMSB = 0, 4
    mp = types.ModuleType('micropython')
    mp.const = lambda x: x
    saved = {k: sys.modules.get(k) for k in ('framebuf', 'micropython', 'sh1107')}
    sys.modules.update(framebuf=fb, micropython=mp)
    sys.modules.pop('sh1107', None)
    try:
        import sh1107
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return sh1107


class Spy(object):
    def __init__(self, sh1107, rotate=180):
        class Oled(sh1107.SH1107):
            def __init__(self):
                self.commands, self.data = [], []
                self.dc = self.res = None
                super().__init__(128, 64, False, 0, rotate)

            def write_command(self, cmd):
                self.commands.append(bytes(cmd))

            def write_data(self, buf):
                self.data.append(bytes(buf))

            def reset(self):
                pass

        self.oled = Oled()
        sh1107.time.sleep_ms = lambda ms: None
        self.oled.show(True)
        self.clear()

    def clear(self):
        del self.oled.commands[:], self.oled.data[:]


def _spy(rotate=180):
    mod = _load()
    mod.time.sleep_ms = lambda ms: None
    return Spy(mod, rotate)


def test_unchanged_frame_sends_nothing():
    s = _spy()
    s.oled.pages_to_update = 0xFF          # everything marked dirty, like fill() does
    s.oled.show()
    assert s.oled.data == []


def test_only_changed_rows_are_sent():
    s = _spy()
    s.oled.displaybuf[3 * 16 + 2] = 0x55    # row 3, byte 2
    s.oled.displaybuf[20 * 16] = 0x01       # row 20
    s.oled.pages_to_update = 0xFF
    s.oled.show()
    assert len(s.oled.data) == 2
    assert s.oled.commands[0][0] == 3 and s.oled.data[0][2] == 0x55
    assert s.oled.commands[1] == bytes([20 & 0x0f, 0x10 | (20 >> 4)])
    s.clear()
    s.oled.pages_to_update = 0xFF
    s.oled.show()                           # shadow now matches: nothing more to send
    assert s.oled.data == []


def test_full_update_sends_every_row():
    s = _spy()
    s.oled.show(True)
    assert len(s.oled.data) == 64


def test_power_on_forces_full_update():
    s = _spy()
    s.oled.sleep(True)
    s.oled.sleep(False)
    s.clear()
    s.oled.pages_to_update = 0x01
    s.oled.show()                           # the display RAM is not trusted after power-up
    assert len(s.oled.data) == 64


def test_rotated_pages_are_compared_as_a_whole():
    s = _spy(90)
    s.oled.show(True)
    s.clear()
    s.oled.displaybuf[5] = 0xFF             # page 0
    s.oled.pages_to_update = (1 << s.oled.pages) - 1
    s.oled.show()
    assert len(s.oled.data) == 1 and s.oled.data[0][5] == 0xFF
