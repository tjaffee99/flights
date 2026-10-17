"""The routes a build adds to the map and takes off it, for the commit message.

Compares the freshly built data/flights.json with the last published one (git HEAD's, or --base),
and writes build/changes.txt: how many routes started and stopped, and the busiest of each.

    python3 changes.py                 # after build_routes.py and checks.py
    python3 changes.py --base 3be1a66  # against an older build
"""

import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOWN = 12


def routes(data):
    """Unordered pair of codes -> passengers a year."""
    code, r = data['airports']['code'], data['routes']
    return {tuple(sorted((code[x], code[y]))): r['ab'][k] + r['ba'][k] for k, (x, y) in enumerate(zip(r['a'], r['b']))}


def published(ref):
    text = subprocess.run(['git', '-C', str(ROOT), 'show', f'{ref}:data/flights.json'],
                          capture_output=True, text=True, check=True).stdout
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='HEAD', help='the git revision to compare with (default: HEAD)')
    args = parser.parse_args()
    now = routes(json.loads((ROOT / 'data' / 'flights.json').read_text()))
    before = routes(published(args.base))
    added = sorted(now.keys() - before.keys(), key=lambda k: -now[k])
    removed = sorted(before.keys() - now.keys(), key=lambda k: -before[k])
    top = lambda keys: ', '.join('-'.join(k) for k in keys[:SHOWN]) + (' …' if len(keys) > SHOWN else '')
    lines = [f'{len(added)} routes added, {len(removed)} removed']
    if added:
        lines.append('Added: ' + top(added))
    if removed:
        lines.append('Removed: ' + top(removed))
    (HERE / 'build').mkdir(exist_ok=True)
    (HERE / 'build' / 'changes.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
