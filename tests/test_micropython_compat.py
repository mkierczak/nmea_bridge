"""Guards against Python features that CPython has but MicroPython lacks. The tests run on CPython, which
happily accepts them, so the failure only shows up on the board: a menu that could not draw (`str.ljust`)
froze the UI until it timed out. The list below was verified on a real Pico W (MicroPython 1.24.1) by asking
the interpreter with hasattr()."""
import ast
import glob
import os

ROOT = os.path.join(os.path.dirname(__file__), '..')
# attribute names that do not exist on the board's str / int / float
MISSING = {'ljust', 'rjust', 'zfill', 'expandtabs', 'isascii', 'casefold', 'removeprefix', 'removesuffix',
           'title', 'capitalize', 'swapcase', 'translate', 'bit_length', 'is_integer', 'as_integer_ratio'}
VENDORED = {'sh1107.py', 'writer.py', 'roboto14.py'}


def offenders(source):
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in MISSING:
            out.append((node.lineno, node.attr))
    return out


def app_modules():
    for path in sorted(glob.glob(os.path.join(ROOT, '*.py'))):
        if os.path.basename(path) not in VENDORED:
            yield path


def test_the_scan_finds_the_offending_calls():
    assert offenders("x = 'a'.ljust(5)\ny = 3.0.is_integer()") == [(1, 'ljust'), (2, 'is_integer')]
    assert offenders("print('{:<5}'.format('a'))") == []


def test_app_modules_use_only_methods_the_board_has():
    problems = []
    for path in app_modules():
        with open(path) as f:
            for line, name in offenders(f.read()):
                problems.append('{}:{} uses .{}() which MicroPython lacks'.format(os.path.basename(path), line, name))
    assert problems == []


def test_there_are_modules_to_scan():
    names = {os.path.basename(p) for p in app_modules()}
    assert {'menu.py', 'screens.py', 'bridge.py', 'ui.py', 'NMEA.py'} <= names
