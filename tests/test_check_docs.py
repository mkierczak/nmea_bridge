import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

import check_docs


def check_text(text, other=None):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'a.md')
        with open(path, 'w') as f:
            f.write(text)
        if other:
            with open(os.path.join(d, 'b.md'), 'w') as f:
                f.write(other)
        return check_docs.check(path)


def test_slug_matches_github_anchors():
    assert check_docs.slug("9. The module's own detector") == '9-the-modules-own-detector'
    assert check_docs.slug('K1 - persistent position jump (strong)') == 'k1---persistent-position-jump-strong'


def test_good_links_and_existing_files_pass():
    assert check_text('# Title\n\nSee [below](#more) and `bridge.py`.\n\n## More\n') == []
    assert check_text('[b](b.md#sec)', other='## Sec\n') == []


def test_broken_links_anchors_and_stale_file_names_are_reported():
    problems = check_text('# T\n[x](#nope) [y](missing.md) [z](b.md#nope) see `nothere.py`\n', other='## Sec\n')
    assert len(problems) == 4
    assert any('anchor: #nope' in p for p in problems)
    assert any('missing file' in p for p in problems)
    assert any('b.md#nope' in p for p in problems)
    assert any('nothere.py' in p for p in problems)


def test_code_blocks_are_not_checked_and_external_links_are_ignored():
    text = '# T\n```\n[x](#nope) `nothere.py`\n```\n[web](https://example.com/#frag)\n'
    assert check_text(text) == []


def test_the_real_docs_are_clean():
    assert check_docs.main() == 0
