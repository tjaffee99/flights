"""The routes a build adds to the map and takes off it, kept for the site's Changes list.

Compares the freshly built data/flights.json with the last published one (git HEAD's, or --base),
and adds an entry to data/changes.json: the date, and each route that started or stopped, with its
passengers and airlines. Two builds on the same day make one entry. Only the last WEEKS entries are
kept. Also writes build/changes.txt, a short summary for the commit message.

    python3 changes.py                 # after build_routes.py and checks.py
    python3 changes.py --base 3be1a66  # against an older build
"""

import argparse
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / 'data' / 'changes.json'
WEEKS = 26


def routes(data):
    """Unordered pair of codes -> what the site needs to show a route that's no longer in the data."""
    a, r = data['airports'], data['routes']
    code, label, cc = a['code'], a.get('city') or a['name'], a['country']
    out = {}
    for k, (x, y) in enumerate(zip(r['a'], r['b'])):
        (x, y) = sorted((x, y), key=lambda i: code[i])
        out[(code[x], code[y])] = {'a': code[x], 'b': code[y], 'an': label[x] or a['name'][x], 'bn': label[y] or a['name'][y],
                                   'cc': [cc[x], cc[y]], 'pax': r['ab'][k] + r['ba'][k],
                                   'airlines': [data['airlines'][i] for i in r['al'][k]]}
    return out


def published(ref):
    text = subprocess.run(['git', '-C', str(ROOT), 'show', f'{ref}:data/flights.json'],
                          capture_output=True, text=True, check=True).stdout
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='HEAD', help='the git revision to compare with (default: HEAD)')
    args = parser.parse_args()
    data = json.loads((ROOT / 'data' / 'flights.json').read_text())
    now, before = routes(data), routes(published(args.base))
    added = {k: now[k] for k in now.keys() - before.keys()}
    removed = {k: before[k] for k in before.keys() - now.keys()}

    log = json.loads(OUT.read_text()) if OUT.exists() else {'updates': []}
    updates = log['updates']
    if updates and updates[0]['date'] == data['built']:
        # a second build the same day: one entry for the day, from the last week's map to this one
        last = updates.pop(0)
        was_added = {(e['a'], e['b']): e for e in last['added']}
        was_removed = {(e['a'], e['b']): e for e in last['removed']}
        added, removed = ({**{k: v for k, v in added.items() if k not in was_removed},
                           **{k: v for k, v in was_added.items() if k not in removed}},
                          {**{k: v for k, v in removed.items() if k not in was_added},
                           **{k: v for k, v in was_removed.items() if k not in added}})
    if added or removed:
        order = lambda d: sorted(d.values(), key=lambda e: -e['pax'])
        updates.insert(0, {'date': data['built'], 'added': order(added), 'removed': order(removed)})
    log['updates'] = updates[:WEEKS]
    OUT.write_text(json.dumps(log, ensure_ascii=False, separators=(',', ':')))

    top = lambda d: ', '.join(f"{e['a']}-{e['b']}" for e in sorted(d.values(), key=lambda e: -e['pax'])[:12])
    lines = [f'{len(added)} routes added, {len(removed)} removed']
    if added:
        lines.append('Added: ' + top(added) + (' …' if len(added) > 12 else ''))
    if removed:
        lines.append('Removed: ' + top(removed) + (' …' if len(removed) > 12 else ''))
    (HERE / 'build').mkdir(exist_ok=True)
    (HERE / 'build' / 'changes.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
