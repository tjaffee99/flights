"""Combine current routes and passenger counts into the site's data file, ../data/flights.json,
and list the airports without airline routes in ../data/others.json.

Inputs (see ../README.md for how each is made):
    build/wiki_routes.csv       routes flying now, from Wikipedia's airport pages (parse_wikipedia.py)
    build/wiki_airports.csv     each airport's annual passengers from its infobox
    build/wiki_route_stats.csv  passengers on an airport's busiest routes, from its Wikipedia page
    build/measured.csv          official passenger counts per airport pair (build_measured.py)
    cache/airports.csv          OurAirports: names, places, coordinates
    overrides.json              routes that cannot operate today, whatever the sources say

Which routes: every route Wikipedia lists as flying now, plus every route the official statistics
show flying in their latest months. A measured route that has stopped (no traffic in the last
months of data, and not listed on Wikipedia) is dropped, and so is one Wikipedia marks suspended.
One Wikipedia says resumes on a future date is kept only if the counts show it flying this year
(a seasonal break), not if it has been gone for years.

Passengers, best source first:
  1. official counts (US DOT, UK CAA, Eurostat) over their latest 12 months; a route that started
     partway through is scaled up to a year;
  2. the counts in airports' "busiest routes" tables on Wikipedia (from airport operators and
     national statistics), for the latest year given;
  3. an estimate, in two steps: a regression fitted on the official counts predicts a route's
     passengers from what Wikipedia says about it (how big its airports are, how many airlines fly
     it and which, distance, domestic or not, seasonal or charter-only); then each airport's
     estimated routes are scaled so that, with its counted routes, they add up to the airport's
     annual passenger total (iterative proportional fitting, since each route has two airports).

The estimates are checked by hiding each official source in turn, estimating its routes from
scratch, and comparing.

    python3 build_routes.py
"""

import csv
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE, BUILD = HERE / 'cache', HERE / 'build'
OUT = HERE.parent / 'data' / 'flights.json'
ABOUT = 'https://github.com/tjaffee99/flights#readme'

# A source listed earlier wins when two sources count the same route.
SOURCE_PRIORITY = ['us_bts_t100', 'uk_caa', 'br_anac', 'eu_eurostat_avia_par', 'au_bitre_dom', 'au_bitre_intl']
SHORT_NAMES = {'us_bts_t100': 'US DOT', 'uk_caa': 'UK CAA', 'br_anac': 'ANAC Brazil', 'eu_eurostat_avia_par': 'Eurostat',
               'au_bitre_dom': 'BITRE Australia', 'au_bitre_intl': 'BITRE Australia'}
# Sources that count every route touching these countries' airports.
NATIONAL = {'us_bts_t100': {'US', 'PR', 'VI', 'GU', 'AS', 'MP'}, 'uk_caa': {'GB', 'JE', 'GG', 'IM'}, 'br_anac': {'BR'}}
# Sources counted by city pair, including passengers on one-stop "through" flights (Sydney–London via
# Singapore): a pair from these is only a route if Wikipedia lists it as nonstop.
THROUGH = {'au_bitre_intl'}
REPORTED = 'reported'         # source key for counts from Wikipedia's busiest-routes tables
ESTIMATE = 'estimate'
MIN_PASSENGERS = 500          # measured routes below this a year are noise unless Wikipedia lists them
RECENT_MONTHS = 2             # a measured route must have flown in the last N months of its data
STALE_MONTHS = 9              # data ending longer ago than this needs Wikipedia to confirm a route still flies
USABLE_YEARS = {2019} | set(range(2023, 2100))   # yearly figures to use (not the pandemic years)


def month_index(ym):
    year, month = map(int, ym[:7].split('-'))
    return year * 12 + month - 1


def month_name(index):
    return dt.date(index // 12, index % 12 + 1, 1).strftime('%b %Y')


def haversine(a, b):
    f1, f2 = math.radians(a['lat']), math.radians(b['lat'])
    dl = math.radians(b['lon'] - a['lon'])
    s = math.sin((f2 - f1) / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(min(1, math.sqrt(s)))


def log_error(pred, actual):
    """Median and passenger-weighted error factors (x1.5 means off by 50% either way)."""
    pred, actual = np.asarray(pred), np.asarray(actual)
    err = np.abs(np.log(pred) - np.log(actual))
    weighted = np.sum(err * actual) / np.sum(actual)
    return math.exp(np.median(err)), math.exp(weighted), math.exp(np.percentile(err, 80))


# ---------------------------------------------------------------------------------------- inputs

def load_airports():
    rank = {'large_airport': 0, 'medium_airport': 1, 'small_airport': 2, 'seaplane_base': 3, 'heliport': 4}
    airports = {}
    with open(CACHE / 'airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            code = row['iata_code'].strip()
            if not code or row['type'] == 'closed':
                continue
            key = (row['scheduled_service'] != 'yes', rank.get(row['type'], 5))
            if code in airports and airports[code]['key'] <= key:
                continue
            airports[code] = {'key': key, 'name': row['name'], 'city': row['municipality'], 'cc': row['iso_country'],
                              'lat': float(row['latitude_deg']), 'lon': float(row['longitude_deg'])}
    countries = {}
    with open(CACHE / 'countries.csv', newline='') as f:
        for row in csv.DictReader(f):
            countries[row['code']] = row['name']
    return airports, countries


def load_wikipedia(airports):
    """Routes flying now by unordered airport pair, routes that have stopped, listings per airport,
    infobox totals, and the airports whose pages list routes. Also sets each airport's 'label', the
    name other airports' route tables most often give it ("Caracas", "Tokyo–Haneda")."""
    labels = defaultdict(lambda: defaultdict(int))
    pairs = {}
    stopped = set()   # pairs Wikipedia lists only as suspended, ended or not started yet
    resuming = set()  # pairs on a break, flying again on a future date
    listings = defaultdict(int)
    with open(BUILD / 'wiki_routes.csv', newline='') as f:
        for row in csv.DictReader(f):
            o, d = row['origin'], row['dest']
            if not d or o == d or o not in airports or d not in airports:
                continue
            key = tuple(sorted((o, d)))
            if row['status'] != 'current':
                if row['status'] in ('suspended', 'ended', 'future'):
                    stopped.add(key)
                elif row['status'] == 'resuming':
                    resuming.add(key)
                continue
            listings[o] += 1
            if row['dest_name']:
                labels[d][row['dest_name'].strip()] += 1
            p = pairs.setdefault(key, {'by': set(), 'airlines': set(), 'modes': set()})
            p['by'].add(o)
            p['airlines'].add(row['airline'].strip())
            p['modes'].add(row['mode'])
    resuming -= set(pairs)
    stopped -= set(pairs) | resuming
    for code, names in labels.items():
        airports[code]['label'] = max(names.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
    totals, pages = {}, set()
    with open(BUILD / 'wiki_airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['passengers'] and row['passengers_year']:
                totals[row['iata']] = (int(row['passengers']), int(row['passengers_year']))
            if int(row['tables'] or 0) > 0:
                pages.add(row['iata'])
    return pairs, stopped, resuming, listings, totals, pages


def load_measured(airports):
    """Official counts by unordered pair (the highest-priority source's numbers for each), the
    sources' descriptions, and the airports each source counts completely."""
    path = BUILD / 'measured.csv'
    if not path.exists():
        return {}, {}, {}
    sources = json.loads((BUILD / 'measured_sources.json').read_text())
    by_source = defaultdict(lambda: defaultdict(list))  # source -> (o, d) -> rows (several reporters)
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            o, d = row['origin_iata'], row['dest_iata']
            if o == d or o not in airports or d not in airports or not row['passengers']:
                continue
            by_source[row['source']][(o, d)].append(row)

    pairs = {}
    order = sorted(by_source, key=lambda s: SOURCE_PRIORITY.index(s) if s in SOURCE_PRIORITY else 99)
    for source in order:
        merged = {}
        for (o, d), rows in by_source[source].items():
            # Two reporters (both ends of an intra-EU route) count the same flights: average them.
            pax = sum(float(r['passengers']) for r in rows) / len(rows)
            directional = all(r.get('directional', '1') != '0' for r in rows)
            start = min(month_index(r['period_start']) for r in rows)
            end = max(month_index(r['period_end']) for r in rows)
            # Annual-only rows have no months: take them as flying the whole period.
            first = min((month_index(r['first_month']) for r in rows if r['first_month']), default=start)
            last = max((month_index(r['last_month']) for r in rows if r['last_month']), default=end)
            months = max(int(r['months_with_traffic'] or 0) for r in rows) or end - start + 1
            key = tuple(sorted((o, d)))
            m = merged.setdefault(key, {'source': source, 'ab': 0.0, 'ba': 0.0, 'first': first, 'last': last,
                                        'months': months, 'start': start, 'end': end})
            if not directional:
                m['ab'] += pax / 2
                m['ba'] += pax / 2
            elif (o, d) == key:
                m['ab'] += pax
            else:
                m['ba'] += pax
            m['first'], m['last'] = min(m['first'], first), max(m['last'], last)
            m['months'] = max(m['months'], months)
        for key, m in merged.items():
            pairs.setdefault(key, m)

    # Airports whose routes a source counts completely: every airport in the country for a national
    # source (T-100 covers every flight touching the US), every reporting airport for Eurostat.
    covered = defaultdict(set)
    for source, rows in by_source.items():
        for (o, d), rs in rows.items():
            if source in NATIONAL:
                covered[source] |= {x for x in (o, d) if airports[x]['cc'] in NATIONAL[source]}
            for r in rs:
                if ':' in (r.get('reporter') or ''):
                    covered[source].add(r['reporter'].split(':', 1)[1])    # Eurostat: "ES:MAD"
    return pairs, sources, covered


def load_reported(airports, today):
    """Counts from busiest-routes tables by unordered pair: the latest usable year's figure,
    averaged when both airports' pages give one. One-way (departing) counts are doubled."""
    found = defaultdict(list)
    path = BUILD / 'wiki_route_stats.csv'
    if not path.exists():
        return {}
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            a, b = row['airport'], row['dest']
            if not row['year'] or a == b or a not in airports or b not in airports:
                continue
            year = int(row['year'])
            if year not in USABLE_YEARS or year > today.year:
                continue
            value = int(row['passengers']) * (2 if row['one_way'] == '1' else 1)
            found[tuple(sorted((a, b)))].append((year, value, a))
    out = {}
    for key, values in found.items():
        latest = max(y for y, _, _ in values)
        same = [(v, a) for y, v, a in values if y == latest]
        # One page can list a route twice (a domestic and an all-routes table): one figure per page.
        per_page = {}
        for v, a in same:
            per_page.setdefault(a, v)
        out[key] = {'pax': sum(per_page.values()) / len(per_page), 'year': latest, 'by': set(per_page)}
    return out


def check_reported(reported, measured, airports):
    """Compare the tables with official counts where both exist, by the country of the page.

    A country whose tables come out at about half the official figure counts one way only, so its
    figures are doubled; one that disagrees badly is not used. Countries without official counts to
    compare against are taken as they are.
    """
    ratios = defaultdict(list)
    for key, r in reported.items():
        if key in measured:
            m = measured[key]
            for a in r['by']:
                ratios[airports[a]['cc']].append(r['pax'] / max(1.0, m['ab'] + m['ba']))
    verdict = {}
    for cc, values in ratios.items():
        if len(values) < 5:
            continue
        median = float(np.median(values))
        verdict[cc] = 2.0 if 0.38 < median < 0.62 else (1.0 if 0.62 <= median <= 1.6 else 0.0)
    print('Busiest-routes tables vs official counts, by country (median ratio, routes):')
    print('  ' + ', '.join(f'{cc} {np.median(v):.2f} ({len(v)})' for cc, v in sorted(ratios.items(), key=lambda kv: -len(kv[1]))[:20]))
    out = {}
    for key, r in reported.items():
        factors = [verdict.get(airports[a]['cc'], 1.0) for a in r['by']]
        factor = min(factors)
        if factor > 0:
            out[key] = dict(r, pax=r['pax'] * factor)
    return out


# ---------------------------------------------------------------------------------------- model

class Model:
    """Log passengers on a route from what Wikipedia says about it. The airline term is each
    airline's average residual on the routes it's counted on, shrunk toward zero when it has few."""

    SHRINK = 15

    def __init__(self, airports, listings, totals):
        self.airports, self.listings, self.totals = airports, listings, totals
        sizes = [(math.log1p(listings[x]), math.log(t)) for x, (t, year) in totals.items()
                 if year in USABLE_YEARS and listings[x]]
        sx, sy = np.array(sizes).T
        self.size_fit = np.polyfit(sx, sy, 1)
        self.airline_effect = {}

    def size(self, x):
        if x in self.totals and self.totals[x][1] in USABLE_YEARS:
            return math.log(self.totals[x][0])
        return float(np.polyval(self.size_fit, math.log1p(self.listings[x])))

    def base_features(self, key, p):
        a, b = (self.airports[x] for x in key)
        km = max(haversine(a, b), 30)
        size = [self.size(x) for x in key]
        count = [math.log1p(self.listings[x]) for x in key]
        modes = p['modes']
        return [
            1.0,
            min(size), max(size),
            min(count), max(count),
            math.log1p(len(p['airlines'])),
            math.log(km), math.log(km) ** 2,
            1.0 if a['cc'] == b['cc'] else 0.0,
            1.0 if modes <= {'seasonal', 'seasonal_charter'} else 0.0,
            1.0 if modes <= {'charter', 'seasonal_charter'} else 0.0,
            1.0 if len(p['by']) == 2 else 0.0,
        ]

    def airline_term(self, p, effects):
        known = [effects[a] for a in p['airlines'] if a in effects]
        return sum(known) / len(known) if known else 0.0

    @staticmethod
    def solve(X, y, ridge=1.0):
        penalty = ridge * np.eye(X.shape[1])
        penalty[0, 0] = 0
        return np.linalg.solve(X.T @ X + penalty, X.T @ y)

    def effects(self, keys, pairs, residuals):
        sums, counts = defaultdict(float), defaultdict(int)
        for key, res in zip(keys, residuals):
            for a in pairs[key]['airlines']:
                sums[a] += res
                counts[a] += 1
        return {a: sums[a] / (counts[a] + self.SHRINK) for a in sums}

    def fit(self, keys, pairs, y):
        X0 = np.array([self.base_features(k, pairs[k]) for k in keys])
        y = np.asarray(y)
        base = self.solve(X0, y)
        residuals = y - X0 @ base
        # Out-of-fold airline effects for the training rows, so a route doesn't explain itself.
        folds = np.arange(len(keys)) % 5
        term = np.zeros(len(keys))
        for k in range(5):
            effects = self.effects([keys[i] for i in np.where(folds != k)[0]], pairs, residuals[folds != k])
            for i in np.where(folds == k)[0]:
                term[i] = self.airline_term(pairs[keys[i]], effects)
        X = np.column_stack([X0, term])
        self.coef = self.solve(X, y)
        self.airline_effect = self.effects(keys, pairs, residuals)
        return X @ self.coef

    def predict(self, key, p):
        x = self.base_features(key, p) + [self.airline_term(p, self.airline_effect)]
        return math.exp(float(np.array(x) @ self.coef))


def calibrate(priors, fixed_at, targets, rounds=80):
    """Scale estimated routes so each target airport's routes add up to its total.

    priors: {pair: estimate}; fixed_at: {airport: counted passengers already there};
    targets: {airport: annual total}. Each route is scaled by f[a] * f[b], each f kept within
    1/5..5. A total that disagrees with the routes by more than 25x is a bad number, not a signal.
    """
    f = defaultdict(lambda: 1.0)
    by_airport = defaultdict(list)
    for key in priors:
        for x in key:
            by_airport[x].append(key)
    goal = {}
    for x, total in targets.items():
        rest = total - fixed_at.get(x, 0)
        prior = sum(priors[k] for k in by_airport.get(x, ()))
        if x in by_airport and rest > 0.1 * total and 1 / 25 < rest / prior < 25:
            goal[x] = rest
    for _ in range(rounds):
        for x, want in goal.items():
            have = sum(priors[k] * f[k[0]] * f[k[1]] for k in by_airport[x])
            if have > 0:
                f[x] = min(5.0, max(0.2, f[x] * (want / have) ** 0.7))
    return {k: v * f[k[0]] * f[k[1]] for k, v in priors.items()}, goal


def estimate(fixed, wiki, model, totals, covered, train_sources):
    """Estimates for every Wikipedia route without a count, given the counted routes `fixed`."""
    keys = [k for k, r in fixed.items() if k in wiki and r['source'] in train_sources]
    model.fit(keys, wiki, [math.log(fixed[k]['ab'] + fixed[k]['ba']) for k in keys])
    priors = {k: model.predict(k, p) for k, p in wiki.items() if k not in fixed}
    fixed_at = defaultdict(float)
    for key, r in fixed.items():
        for x in key:
            fixed_at[x] += r['ab'] + r['ba']
    targets = {x: t for x, (t, year) in totals.items() if year in USABLE_YEARS and x not in covered}
    return calibrate(priors, fixed_at, targets)


# ---------------------------------------------------------------------------------------- main

def main():
    today = dt.date.today()
    this_month = today.year * 12 + today.month - 1
    airports, countries = load_airports()
    wiki, stopped, resuming, listings, totals, pages = load_wikipedia(airports)
    measured, measured_sources, covered_by = load_measured(airports)
    reported = check_reported(load_reported(airports, today), measured, airports)
    overrides = json.loads((HERE / 'overrides.json').read_text())
    covered = set().union(*covered_by.values()) if covered_by else set()
    print(f'Wikipedia: {len(wiki)} current routes; official counts: {len(measured)} pairs '
          f'({len(covered)} airports fully counted); busiest-routes tables: {len(reported)} pairs')

    # --- official counts for routes still flying
    routes = {}
    dropped = defaultdict(int)
    for key, m in measured.items():
        pax = m['ab'] + m['ba']
        if pax < MIN_PASSENGERS and key not in wiki:
            dropped['too small'] += 1
            continue
        recent = m['last'] >= m['end'] - (RECENT_MONTHS - 1)
        if key in stopped and key not in wiki:
            dropped['suspended per Wikipedia'] += 1
            continue
        on_break = key in resuming and m['months'] >= 2   # flew this year, back soon
        if not recent and key not in wiki and not on_break:
            dropped['stopped flying'] += 1
            continue
        if m['source'] in THROUGH and key not in wiki:
            dropped['not a nonstop route'] += 1
            continue
        # Old data (Eurostat's routes end in 2024) can't say a route still flies; Wikipedia can,
        # where either airport's page lists its routes.
        stale = m['end'] < this_month - STALE_MONTHS
        if stale and key not in wiki and (key[0] in pages or key[1] in pages):
            dropped['not listed on Wikipedia since the data ended'] += 1
            continue
        scale, months = 1.0, 0
        seasonal = key in wiki and wiki[key]['modes'] <= {'seasonal', 'seasonal_charter'}
        if recent and m['first'] > m['start'] + 1 and not seasonal and m['months'] >= 2:
            scale, months = 12 / m['months'], m['months']   # started (or restarted) partway through
        routes[key] = {'ab': m['ab'] * scale, 'ba': m['ba'] * scale, 'source': m['source'], 'months': months, 'year': 0}
    print(f'  kept {len(routes)} counted routes; dropped {dict(dropped)}')

    # --- busiest-routes tables, for current routes without official counts
    used = 0
    for key, r in reported.items():
        if key in wiki and key not in routes:
            routes[key] = {'ab': r['pax'] / 2, 'ba': r['pax'] / 2, 'source': REPORTED, 'months': 0, 'year': r['year']}
            used += 1
    print(f'  {used} routes from busiest-routes tables')

    # --- how good are the estimates? Hide each official source, estimate its routes, compare.
    print('Estimates checked against official counts they did not see:')
    for held in SOURCE_PRIORITY:
        if not covered_by.get(held):
            continue
        hidden = {k: r for k, r in routes.items() if r['source'] == held and k in wiki}
        rest = {k: r for k, r in routes.items() if r['source'] != held}
        guesses, _ = estimate(rest, wiki, Model(airports, listings, totals), totals,
                              set().union(*(v for s, v in covered_by.items() if s != held)),
                              set(SOURCE_PRIORITY) - {held})
        keys = [k for k in hidden if k in guesses]
        median, weighted, p80 = log_error([guesses[k] for k in keys], [hidden[k]['ab'] + hidden[k]['ba'] for k in keys])
        print(f'  {SHORT_NAMES[held]:12s} {len(keys):6d} routes: median off by x{median:.2f}, '
              f'by passengers x{weighted:.2f}, 80% within x{p80:.2f}')

    # --- estimate every Wikipedia route without a count
    model = Model(airports, listings, totals)
    estimates, goal = estimate(routes, wiki, model, totals, covered, set(SOURCE_PRIORITY))
    print(f'Estimated {len(estimates)} routes; calibrated to the totals of {len(goal)} airports')
    for key, pax in estimates.items():
        routes[key] = {'ab': pax / 2, 'ba': pax / 2, 'source': ESTIMATE, 'months': 0, 'year': 0}

    # --- hard rules
    closed = set(overrides['closed_countries'])
    blocked = [(set(rule['a']), set(rule['b'])) for rule in overrides['blocked_country_pairs']]
    removed = []
    for key in list(routes):
        ca, cb = (airports[x]['cc'] for x in key)
        if ca in closed or cb in closed or any((ca in s and cb in t) or (cb in s and ca in t) for s, t in blocked):
            removed.append('-'.join(key))
            del routes[key]
    if removed:
        print(f'Overrides removed {len(removed)} routes: {", ".join(sorted(removed)[:30])}')

    write(routes, wiki, airports, countries, measured_sources, today)


def write(routes, wiki, airports, countries, measured_sources, today):
    used = sorted({x for key in routes for x in key})
    index = {x: i for i, x in enumerate(used)}
    airline_names = sorted({a for k, p in wiki.items() if k in routes for a in p['airlines']})
    airline_index = {a: i for i, a in enumerate(airline_names)}

    source_list = [{'id': 0, 'key': ESTIMATE, 'name': 'Estimate', 'short': 'estimates'}]
    for s in sorted(measured_sources.get('sources', []), key=lambda s: SOURCE_PRIORITY.index(s['id']) if s['id'] in SOURCE_PRIORITY else 99):
        start, end = month_index(s['period_start']), month_index(s['period_end'])
        source_list.append({'id': len(source_list), 'key': s['id'], 'name': s.get('publisher', s['name']),
                            'short': SHORT_NAMES.get(s['id'], s['name']), 'url': s.get('url', ''),
                            'period': f'{month_name(start)} – {month_name(end)}',
                            'directional': bool(s.get('directional', True))})
    source_list.append({'id': len(source_list), 'key': REPORTED, 'short': 'airport statistics',
                        'name': "The airport's published statistics, from its Wikipedia page"})
    source_id = {s['key']: s['id'] for s in source_list}

    out = {'a': [], 'b': [], 'ab': [], 'ba': [], 'src': [], 'al': [], 's': [], 'n': [], 'y': []}
    for key in sorted(routes, key=lambda k: -(routes[k]['ab'] + routes[k]['ba'])):
        r = routes[key]
        rough = r['source'] in (ESTIMATE, REPORTED)
        digits = lambda v: int(round(v, -2)) if rough and v >= 1000 else int(round(v))
        p = wiki.get(key)
        out['a'].append(index[key[0]])
        out['b'].append(index[key[1]])
        out['ab'].append(digits(r['ab']))
        out['ba'].append(digits(r['ba']))
        out['src'].append(source_id[r['source']])
        out['al'].append(sorted(airline_index[a] for a in p['airlines']) if p else [])
        out['s'].append(1 if p and p['modes'] <= {'seasonal', 'seasonal_charter'} else 0)
        out['n'].append(r['months'])
        out['y'].append(r['year'])

    data = {
        'routes_as_of': today.strftime('%B %Y'),
        'built': today.isoformat(),
        'about': ABOUT,
        'sources': source_list,
        'countries': {c: countries.get(c, c) for c in sorted({airports[x]['cc'] for x in used})},
        'airlines': airline_names,
        'airports': {
            'code': used,
            'name': [airports[x]['name'] for x in used],
            'city': [airports[x].get('label') or airports[x]['city'] for x in used],
            'town': [airports[x]['city'] if airports[x].get('label') and airports[x]['city'] != airports[x]['label'] else ''
                     for x in used],
            'country': [airports[x]['cc'] for x in used],
            'lat': [round(airports[x]['lat'], 4) for x in used],
            'lon': [round(airports[x]['lon'], 4) for x in used],
        },
        'routes': out,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    write_others(set(used), countries)
    total = sum(r['ab'] + r['ba'] for r in routes.values())
    share = {s: sum(r['ab'] + r['ba'] for r in routes.values() if r['source'] == s) / total
             for s in {r['source'] for r in routes.values()}}
    print(f'Wrote {OUT.relative_to(HERE.parent)}: {len(used)} airports, {len(routes)} routes, '
          f'{total / 1e9:.2f}B passengers a year, {OUT.stat().st_size / 1e6:.1f} MB')
    print('  share of passengers by source: ' + ', '.join(f'{s} {100 * v:.0f}%' for s, v in sorted(share.items(), key=lambda kv: -kv[1])))


def write_others(used, countries):
    """../data/others.json: airports with a code but no airline routes (general aviation fields,
    air bases, closed or unfinished airports), which the map shows in grey."""
    others = {}
    with open(CACHE / 'airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            code = row['iata_code'].strip()
            if code and code not in used and code not in others and row['type'] in ('large_airport', 'medium_airport', 'small_airport'):
                others[code] = row
    codes = sorted(others)
    data = {
        'code': codes,
        'name': [others[c]['name'] for c in codes],
        'city': [others[c]['municipality'] for c in codes],
        'country': [others[c]['iso_country'] for c in codes],
        'lat': [round(float(others[c]['latitude_deg']), 4) for c in codes],
        'lon': [round(float(others[c]['longitude_deg']), 4) for c in codes],
        'countries': {cc: countries.get(cc, cc) for cc in sorted({others[c]['iso_country'] for c in codes})},
    }
    path = OUT.parent / 'others.json'
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    print(f'Wrote {path.relative_to(HERE.parent)}: {len(codes)} airports without airline routes, {path.stat().st_size / 1e3:.0f} KB')


if __name__ == '__main__':
    main()
