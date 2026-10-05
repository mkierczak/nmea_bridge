"""Check the Markdown docs: relative links and anchors resolve, and every `file.py` mentioned in code
formatting exists in the repository.   python3 tools/check_docs.py   (exit status 1 if anything is wrong)"""
import glob
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SEARCH_DIRS = ('', 'tools', 'tests', 'docs')
# Files the docs may mention although a clean checkout does not contain them (generated, git-ignored)
GENERATED = ('version.py',)


def slug(heading):
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')


def anchors_of(text):
    return {slug(m.group(1)) for m in re.finditer(r'^#{1,6}\s+(.*)$', text, re.M)}


def code_blocks_removed(text):
    return re.sub(r'```.*?```', '', text, flags=re.S)


def check(path):
    problems = []
    with open(path) as f:
        text = f.read()
    here = os.path.dirname(path)
    prose = code_blocks_removed(text)
    own = anchors_of(prose)
    for m in re.finditer(r'\]\(([^)\s]+)\)', prose):
        target = m.group(1)
        if target.startswith(('http://', 'https://', 'mailto:')):
            continue
        file_part, _, anchor = target.partition('#')
        if file_part:
            full = os.path.normpath(os.path.join(here, file_part))
            if not os.path.exists(full):
                problems.append('missing file in link: ' + target)
                continue
            if anchor and full.endswith('.md'):
                with open(full) as f:
                    if anchor not in anchors_of(code_blocks_removed(f.read())):
                        problems.append('missing anchor in link: ' + target)
        elif anchor and anchor not in own:
            problems.append('missing anchor: #' + anchor)
    for m in re.finditer(r'`([A-Za-z0-9_./-]+\.py)`', prose):
        name = m.group(1)
        if name in GENERATED:
            continue
        if not any(os.path.exists(os.path.join(ROOT, d, name)) for d in SEARCH_DIRS) and \
                not os.path.exists(os.path.normpath(os.path.join(here, name))):
            problems.append('mentions a file that does not exist: ' + name)
    return problems


def main():
    bad = 0
    for path in [os.path.join(ROOT, 'README.md')] + sorted(glob.glob(os.path.join(ROOT, 'docs', '*.md'))):
        for problem in check(path):
            print('{}: {}'.format(os.path.relpath(path, ROOT), problem))
            bad += 1
    print('docs check: ' + ('all good' if not bad else '{} problem(s)'.format(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
