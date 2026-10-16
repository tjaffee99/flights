"""Checks on a freshly built data/flights.json, before it's published.

Compares the build with the last published one (git HEAD's data/flights.json) and with what the
sources say, and writes build/checks.txt. Warnings are things for a person to look at; a failure
means the build changed too much to publish unseen (a broken scrape, a source that vanished), and
the script exits with status 1.

    python3 checks.py            # after build_routes.py
    python3 checks.py --force    # report only, never fail
"""

import argparse
import csv
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = HERE / 'build'

# A build that moves these by more than this much against the last one isn't published unseen.
LIMITS = {'passengers': 0.08, 'routes': 0.05, 'airports': 0.05}
# The world's airlines carry about 5 billion passengers a year (IATA).
WORLD = (4.0e9, 6.0e9)


def load(data):
    a, r = data['airports'], data['routes']
    code, cc = a['code'], a['country']
    src = {s['id']: s['key'] for s in data['sources']}
    routes = {}
    for k, (x, y) in enumerate(zip(r['a'], r['b'])):
        routes[tuple(sorted((code[x], code[y])))] = {
            'pax': r['ab'][k] + r['ba'][k], 'src': src[r['src'][k]], 'airlines': [data['airlines'][i] for i in r['al'][k]],
            'cc': (cc[x], cc[y])}
    return routes


def previous():
    try:
        text = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:data/flights.json'],
                              capture_output=True, text=True, check=True).stdout
        return json.loads(text)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='report only; never fail')
    args = parser.parse_args()
    data = json.loads((ROOT / 'data' / 'flights.json').read_text())
    now = load(data)
    lines, failures = [], []
    warn = lines.append

    total = sum(r['pax'] for r in now.values())
    estimated = sum(r['pax'] for r in now.values() if r['src'] == 'estimate')
    warn(f'{len(now)} routes, {total / 1e9:.2f}B passengers a year, {100 * estimated / total:.0f}% estimated')
    if not WORLD[0] <= total <= WORLD[1]:
        failures.append(f'total of {total / 1e9:.2f}B passengers is outside {WORLD[0] / 1e9:.0f}–{WORLD[1] / 1e9:.0f}B')

    # --- against the last published build
    old_data = previous()
    if old_data:
        old = load(old_data)
        old_total = sum(r['pax'] for r in old.values())
        for name, a, b in (('passengers', old_total, total), ('routes', len(old), len(now)),
                           ('airports', len(old_data['airports']['code']), len(data['airports']['code']))):
            change = b / a - 1
            line = f'{name}: {a:,.0f} -> {b:,.0f} ({100 * change:+.1f}%)'
            (failures.append if abs(change) > LIMITS[name] else warn)(line)
        top = sorted(old, key=lambda k: -old[k]['pax'])[:100]
        gone = [k for k in top if k not in now]
        if gone:
            warn('busiest routes of the last build that are gone: ' + ', '.join('-'.join(k) for k in gone))
        moved = [(k, old[k]['pax'], now[k]['pax']) for k in top if k in now and not 0.5 < now[k]['pax'] / old[k]['pax'] < 2]
        for k, a, b in moved:
            warn(f'busy route changed more than 2x: {"-".join(k)} {a:,} -> {b:,} ({now[k]["src"]})')
        # A route of a million passengers doesn't appear from one month to the next: one that does is
        # nearly always a mapping slip (DGCA's "London" taken for Luton).
        new_big = sorted((k for k in now if k not in old and now[k]['pax'] > 300_000), key=lambda k: -now[k]['pax'])
        for k in new_big[:20]:
            line = f'new route over 300K: {"-".join(k)} {now[k]["pax"]:,} ({now[k]["src"]}, {", ".join(now[k]["airlines"][:3]) or "no airline on Wikipedia"})'
            (failures.append if now[k]['pax'] > 1_000_000 else warn)(line)
        # An official source that fails to download leaves its routes to the estimates: the totals
        # barely move, but the counted share does.
        by_src, old_by_src = defaultdict(float), defaultdict(float)
        for r in now.values():
            by_src[r['src']] += r['pax']
        for r in old.values():
            old_by_src[r['src']] += r['pax']
        for key in sorted(set(by_src) | set(old_by_src), key=lambda s: -old_by_src[s]):
            a, b = old_by_src[key], by_src[key]
            line = f'  {key}: {a / 1e6:,.0f}M -> {b / 1e6:,.0f}M'
            shrank = key != 'estimate' and a > 0.005 * old_total and b < 0.7 * a
            (failures.append if shrank else warn)(line + (' (source shrank or vanished)' if shrank else ''))
    else:
        warn('no published build to compare with')

    measured = BUILD / 'measured_sources.json'
    if measured.exists():
        for key, error in json.loads(measured.read_text()).get('failed_this_run', {}).items():
            warn(f'official source {key} failed to download this run; its previous rows were kept: {error}')

    # --- airports far from the totals they report (recent years only)
    totals = {}
    with open(BUILD / 'wiki_airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['passengers'] and row['passengers_year'] and int(row['passengers_year']) >= 2023:
                totals[row['iata']] = int(row['passengers'])
    at = defaultdict(float)
    for (x, y), r in now.items():
        at[x] += r['pax']
        at[y] += r['pax']
    far = sorted(((at[c] / t, c, at[c], t) for c, t in totals.items() if c in at and max(t, at[c]) > 2e6
                  and not 0.5 < at[c] / t < 2), key=lambda v: -abs(math.log(v[0])))
    for ratio, c, ours, theirs in far[:25]:
        warn(f'airport far from its reported total: {c} {ours / 1e6:.1f}M vs {theirs / 1e6:.1f}M')

    # --- estimates resting on very little
    for k, r in sorted(now.items(), key=lambda kv: -kv[1]['pax']):
        if r['src'] == 'estimate' and len(r['airlines']) == 1 and r['pax'] > 400_000:
            warn(f'big estimate on one airline: {"-".join(k)} {r["pax"]:,} ({r["airlines"][0]})')

    # --- what the sources dropped this time
    status = BUILD / 'airline_status.json'
    if status.exists():
        gone = sorted(t for t, s in json.loads(status.read_text()).items() if s['defunct'])
        warn(f'airlines left out as no longer flying ({len(gone)}): ' + ', '.join(gone))

    report = ['FAIL: ' + f for f in failures] + lines
    (BUILD / 'checks.txt').write_text('\n'.join(report) + '\n')
    print('\n'.join(report))
    if failures and not args.force:
        print(f'\n{len(failures)} check(s) failed: not publishing. Re-run with --force once a person has looked.')
        sys.exit(1)


if __name__ == '__main__':
    main()
