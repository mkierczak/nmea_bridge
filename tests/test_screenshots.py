import os
import sys
import tempfile

import pytest

pytest.importorskip('PIL')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

import screenshots  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), '..')


def test_every_scene_renders_and_the_docs_use_all_of_them():
    with tempfile.TemporaryDirectory() as d:
        names = [n for n, _ in screenshots.main(d)]
        for n in names:
            assert os.path.getsize(os.path.join(d, n + '.png')) > 0
    with open(os.path.join(ROOT, 'docs', 'screens.md')) as f:
        text = f.read()
    for n in names:
        assert 'img/{}.png'.format(n) in text, n
        assert os.path.exists(os.path.join(ROOT, 'docs', 'img', n + '.png')), n


def test_large_font_text_is_drawn_into_the_picture():
    oled = screenshots.SimOled()
    writer = screenshots.SimWriter(oled)
    screenshots.SimWriter.set_textpos(oled, 10, 0)
    writer.printstring('8')
    assert any(oled.px[y][x] for y in range(10, 24) for x in range(0, 12))
