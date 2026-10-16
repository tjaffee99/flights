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
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE, BUILD = HERE / 'cache', HERE / 'build'
OUT = HERE.parent / 'data' / 'flights.json'
ABOUT = 'https://github.com/tjaffee99/flights#readme'

# A source listed earlier wins when two sources count the same route.
SOURCE_PRIORITY = ['us_bts_t100', 'uk_caa', 'br_anac', 'eu_eurostat_avia_par', 'au_bitre_dom', 'au_bitre_intl',
                   'in_dgca_dom', 'in_dgca_intl']
SHORT_NAMES = {'us_bts_t100': 'US DOT', 'uk_caa': 'UK CAA', 'br_anac': 'ANAC Brazil', 'eu_eurostat_avia_par': 'Eurostat',
               'au_bitre_dom': 'BITRE Australia', 'au_bitre_intl': 'BITRE Australia',
               'in_dgca_dom': 'DGCA India', 'in_dgca_intl': 'DGCA India'}
# Sources that count every route touching these countries' airports.
NATIONAL = {'us_bts_t100': {'US', 'PR', 'VI', 'GU', 'AS', 'MP'}, 'uk_caa': {'GB', 'JE', 'GG', 'IM'}, 'br_anac': {'BR'},
            'in_dgca_dom': {'IN'}, 'in_dgca_intl': {'IN'}}
# Sources counted by city pair, including passengers on one-stop "through" flights (Sydney–London via
# Singapore): a pair from these is only a route if Wikipedia lists it as nonstop.
THROUGH = {'au_bitre_intl'}
REPORTED = 'reported'         # source key for counts from Wikipedia's busiest-routes tables
LISTED = 'busiest_list'       # ... from Wikipedia's list of the world's busiest routes (passengers)
LISTED_SEATS = 'busiest_list_seats'   # ... the same list's seat counts, as passengers
ESTIMATE = 'estimate'
# A counted route Wikipedia doesn't list must look like a schedule, not charters, diversions or
# repositioning flights: about weekly or more in at least one direction, or (for sources without
# flight counts) 5,000+ passengers a year.
MIN_DEPARTURES = 40
MIN_PASSENGERS = 5000
RECENT_MONTHS = 2             # a measured route must have flown in the last N months of its data
STALE_MONTHS = 9              # data ending longer ago than this needs Wikipedia to confirm a route still flies
USABLE_YEARS = {2019} | set(range(2023, 2100))
# An airport's yearly total sets its size, and the scale its estimated routes are fitted to. An older
# figure (Iran's are from 2017) is far better than none, as long as it isn't from the pandemic.
TOTAL_YEARS = set(range(2010, 2020)) | set(range(2023, 2100))
airlines_seen = {}            # airline key -> the name most listings give it (filled by load_wikipedia)   # yearly figures to use (not the pandemic years)


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


def load_wikipedia(airports, overrides):
    """Routes flying now by unordered airport pair, routes that have stopped, listings per airport,
    infobox totals, and the airports whose pages list routes. Also sets each airport's 'label', the
    name other airports' route tables most often give it ("Caracas", "Tokyo–Haneda")."""
    labels = defaultdict(lambda: defaultdict(int))
    airline_names = defaultdict(lambda: defaultdict(int))   # airline key -> names its listings use
    pairs = {}
    stopped = set()   # pairs Wikipedia lists only as suspended, ended or not started yet
    resuming = set()  # pairs on a break, flying again on a future date
    listings = defaultdict(int)

    def airline_of(row):
        """An airline is its Wikipedia article where the table links one (not a list of airlines),
        else its name."""
        name = re.sub(r'\s*\[\s*\d+\s*\]', '', row['airline']).strip()
        title = row.get('airline_title') or ''
        return (title if title and 'redlink=1' not in title and not title.startswith('List of') else 'name:' + name), name

    # an airport replaced by a new one: listings that still name the old one mean the new one
    moved = {m['from']: m for m in overrides.get('moved_airports', [])}
    def place(row):
        for end, other in (('origin', 'dest'), ('dest', 'origin')):
            rule = moved.get(row[end])
            if rule and row[other] in airports and not (rule.get('international_only') and airports[row[other]]['cc'] == airports[row[end]]['cc']):
                row[end] = rule['to']
        return row
    # airlines whose own article says they've stopped flying (fetch_airline_status.py)
    status_path = BUILD / 'airline_status.json'
    gone = {t for t, s in json.loads(status_path.read_text()).items() if s['defunct']} if status_path.exists() else set()
    with open(BUILD / 'wiki_routes.csv', newline='') as f:
        rows = [place(row) for row in csv.DictReader(f)
                if row['dest'] and row['origin'] != row['dest'] and row['origin'] in airports and row['dest'] in airports
                and (row.get('airline_title') or '') not in gone]
    rows = [row for row in rows if row['origin'] != row['dest']]
    # An airline one airport lists as suspended or ended isn't flying the route, even if the other
    # airport's page still lists it (Frankfurt still had Lufthansa to Tehran, which Tehran's page has
    # as suspended). The note is the newer edit.
    halted = {(tuple(sorted((row['origin'], row['dest']))), airline_of(row)[0]) for row in rows
              if row['status'] in ('suspended', 'ended')}
    for row in rows:
        o, d = row['origin'], row['dest']
        key = tuple(sorted((o, d)))
        airline, name = airline_of(row)
        if row['status'] != 'current' or (key, airline) in halted:
            if row['status'] in ('suspended', 'ended', 'future', 'current'):
                stopped.add(key)
            elif row['status'] == 'resuming':
                resuming.add(key)
            continue
        listings[o] += 1
        if row['dest_name']:
            labels[d][row['dest_name'].strip()] += 1
        p = pairs.setdefault(key, {'by': set(), 'airlines': set(), 'modes': set()})
        p['by'].add(o)
        airline_names[airline][name] += 1
        p['airlines'].add(airline)
        p['modes'].add(row['mode'])
    # Charter-only listings (contract, mining, military and holiday charters) aren't flights the public
    # can book on a schedule; they stay only if the official counts show them flying regularly.
    charters = {k for k, p in pairs.items() if p['modes'] <= {'charter', 'seasonal_charter'}}
    for k in charters:
        del pairs[k]
    print(f'Wikipedia: left out {len(charters)} charter-only routes')
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
    airlines_seen.update({k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in airline_names.items()})
    return pairs, stopped, resuming, listings, totals, pages


def load_measured(airports):
    """Official counts by unordered pair (the highest-priority source's numbers for each), the
    sources' descriptions, and the airports each source counts completely."""
    path = BUILD / 'measured.csv'
    if not path.exists():
        return {}, {}, {}
    sources = json.loads((BUILD / 'measured_sources.json').read_text())
    by_source = defaultdict(lambda: defaultdict(list))  # source -> (o, d) -> rows (several reporters)
    periods = defaultdict(Counter)
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            o, d = row['origin_iata'], row['dest_iata']
            if o == d or o not in airports or d not in airports or not row['passengers']:
                continue
            by_source[row['source']][(o, d)].append(row)
            periods[row['source']][(row['period_start'], row['period_end'])] += 1
    # the period most of a source's routes cover (Eurostat's is 2024, though Bosnia's ends in 2023)
    sources['usual_period'] = {k: c.most_common(1)[0][0] for k, c in periods.items()}

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
            deps = max((float(r['departures']) for r in rows if r.get('departures')), default=None)
            key = tuple(sorted((o, d)))
            m = merged.setdefault(key, {'source': source, 'ab': 0.0, 'ba': 0.0, 'first': first, 'last': last,
                                        'months': months, 'start': start, 'end': end, 'deps': None})
            if deps is not None:
                m['deps'] = max(m['deps'] or 0, deps)
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


def load_frequency(airports):
    """How often each route flies and how big and full its planes are, from the official sources that
    count flights and seats (US T-100, Eurostat, ANAC, BITRE domestic). Taken from the highest-priority
    source that has them, which may not be the one the route's passengers come from: the UK CAA counts
    only passengers, so London–Paris gets its flights from Eurostat.
    Unordered pair -> {'source', 'per_week' (each way, in the weeks it flies), 'months', 'seats' (a flight),
    'full' (passengers / seats)}."""
    path = BUILD / 'measured.csv'
    if not path.exists():
        return {}
    by_source = defaultdict(lambda: defaultdict(list))
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            o, d = row['origin_iata'], row['dest_iata']
            if o != d and o in airports and d in airports and float(row['departures'] or 0) > 0:
                by_source[row['source']][(o, d)].append(row)
    out = {}
    for source in sorted(by_source, key=lambda s: SOURCE_PRIORITY.index(s) if s in SOURCE_PRIORITY else 99):
        totals = defaultdict(lambda: {'deps': 0.0, 'seats': 0.0, 'pax': 0.0, 'months': 0, 'ways': 0})
        for (o, d), rows in by_source[source].items():
            # both ends of an intra-EU route report the same flights: average the reporters
            n = len(rows)
            t = totals[tuple(sorted((o, d)))]
            t['deps'] += sum(float(r['departures']) for r in rows) / n
            t['seats'] += sum(float(r['seats'] or 0) for r in rows) / n
            t['pax'] += sum(float(r['passengers'] or 0) for r in rows) / n
            span = month_index(rows[0]['period_end']) - month_index(rows[0]['period_start']) + 1
            t['months'] = max(t['months'], max(int(r['months_with_traffic'] or 0) for r in rows) or span)
            t['ways'] += 1 if all(r.get('directional', '1') != '0' for r in rows) else 2
        for key, t in totals.items():
            if key in out or t['deps'] < 1:
                continue
            each_way = t['deps'] / 2 if t['ways'] >= 2 else t['deps']   # one direction reported: take it as each way
            weeks = min(t['months'], 12) * 365.25 / 12 / 7
            seats = t['seats'] / t['deps'] if t['seats'] else 0
            full = t['pax'] / t['seats'] if t['seats'] else 0
            out[key] = {'source': source, 'per_week': each_way / weeks, 'months': min(t['months'], 12),
                        'seats': seats if 4 <= seats <= 700 else 0, 'full': full if 0.15 <= full <= 1.02 else 0}
    return out


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


def load_listed(measured):
    """Wikipedia's "List of busiest passenger air routes" (Beijing–Shanghai, Delhi–Mumbai, Hanoi–
    Ho Chi Minh City...), as passengers by pair. Seat counts become passengers at the load factor the
    table's routes show where official counts exist (0.8 if too few do); a table of passengers that
    comes out at half the official counts counts one direction, and is doubled."""
    path = BUILD / 'list_routes.csv'
    if not path.exists():
        return {}
    by_table = defaultdict(list)
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            by_table[row['table']].append(row)
    out = {}
    print('List of busiest routes, by table (factor applied, routes checked against official counts):')
    # passenger counts first, so a route in a table of seats too keeps its passenger count
    for table, rows in sorted(by_table.items(), key=lambda kv: kv[1][0]['basis'] == 'seats'):
        seats = rows[0]['basis'] == 'seats'
        ratios = [(measured[k]['ab'] + measured[k]['ba']) / float(r['value'])
                  for r in rows if (k := tuple(sorted((r['a'], r['b'])))) in measured]
        median = float(np.median(ratios)) if len(ratios) >= 3 else None
        if seats:
            factor = median if median and 0.5 < median < 1.0 else 0.8
        else:
            factor = 2.0 if median and 1.6 < median < 2.5 else 1.0
        print(f'  {table[:60]:60} x{factor:.2f} ({len(ratios)})')
        for r in rows:
            key = tuple(sorted((r['a'], r['b'])))
            if r['a'] != r['b'] and key not in out:
                out[key] = {'pax': float(r['value']) * factor, 'year': int(r['year']), 'source': LISTED_SEATS if seats else LISTED}
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
                 if year in TOTAL_YEARS and listings[x]]
        sx, sy = np.array(sizes).T
        self.size_fit = np.polyfit(sx, sy, 1)
        self.airline_effect = {}

    def size(self, x):
        if x in self.totals and self.totals[x][1] in TOTAL_YEARS:
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
        # predictions keep inside what training saw: an Iranian route listing 20 airlines is not
        # five times a route with four
        self.lo, self.hi = X0.min(axis=0), X0.max(axis=0)
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
        x = list(np.clip(self.base_features(key, p), self.lo, self.hi)) + [self.airline_term(p, self.airline_effect)]
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
                f[x] = min(5.0, max(0.05, f[x] * (want / have) ** 0.7))
    return {k: v * f[k[0]] * f[k[1]] for k, v in priors.items()}, goal


def eurostat_withheld():
    """Airports (both ends) of routes whose yearly passenger count Eurostat withholds as confidential
    (every Norwegian domestic route, for one), from its cached yearly tables."""
    icao = {}
    with open(CACHE / 'airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['iata_code'].strip():
                for code in {row['icao_code'], row['gps_code'], row['ident']} - {''}:
                    icao.setdefault(code, row['iata_code'].strip())
    out = set()
    for path in (CACHE / 'eurostat').glob('avia_par_*_A_*.csv'):
        with open(path, newline='') as f:
            for row in csv.DictReader(f):
                if row['tra_meas'].startswith('PAS_BRD') and not row['OBS_VALUE'] and row['CONF_STATUS'].startswith('C'):
                    parts = row['airp_pr'].split('_')
                    out |= {icao[c] for c in parts[1::2] if c in icao}
    return out


def estimate(fixed, wiki, model, totals, covered, national, train_sources, withheld=frozenset()):
    """Estimates for every Wikipedia route without a count, given the counted routes `fixed`."""
    keys = [k for k, r in fixed.items() if k in wiki and r['source'] in train_sources]
    model.fit(keys, wiki, [math.log(fixed[k]['ab'] + fixed[k]['ba']) for k in keys])
    priors = {k: model.predict(k, p) for k, p in wiki.items() if k not in fixed}
    fixed_at = defaultdict(float)
    for key, r in fixed.items():
        for x in key:
            fixed_at[x] += r['ab'] + r['ba']
    # An airport a national source counts in full has no routes left to fit. Eurostat airports can:
    # it withholds confidential routes (all of Norway's domestic ones), so where the counts fall well
    # short of the airport's total, the estimated routes there make up the rest.
    # Only there: where nothing is withheld, a shortfall is old or differently counted traffic
    # (Umeå's 2019 total, from before Swedish domestic flying shrank), not missing routes.
    targets = {x: t for x, (t, year) in totals.items() if year in TOTAL_YEARS
               and (x not in covered or (x in withheld and x not in national and fixed_at[x] < 0.75 * t))}
    return calibrate(priors, fixed_at, targets)


# ---------------------------------------------------------------------------------------- main

def main():
    today = dt.date.today()
    this_month = today.year * 12 + today.month - 1
    airports, countries = load_airports()
    overrides = json.loads((HERE / 'overrides.json').read_text())
    wiki, stopped, resuming, listings, totals, pages = load_wikipedia(airports, overrides)
    # airports reopened in part: their totals are from before (Khartoum's is 2017's)
    limited = {c: v for c, v in overrides.get('limited_airports', {}).items() if not c.startswith('_')}
    for c, rule in limited.items():
        totals.pop(c, None)
        for key in [k for k in wiki if c in k]:   # and their tables still list airlines that left
            if rule.get('airlines'):
                wiki[key]['airlines'] = {a for a in wiki[key]['airlines'] if airlines_seen.get(a, a) in rule['airlines']}
            if not wiki[key]['airlines']:
                del wiki[key]
    measured, measured_sources, covered_by = load_measured(airports)
    reported = check_reported(load_reported(airports, today), measured, airports)
    covered = set().union(*covered_by.values()) if covered_by else set()
    print(f'Wikipedia: {len(wiki)} current routes; official counts: {len(measured)} pairs '
          f'({len(covered)} airports fully counted); busiest-routes tables: {len(reported)} pairs')

    # --- official counts for routes still flying
    routes = {}
    dropped = defaultdict(int)
    for key, m in measured.items():
        pax = m['ab'] + m['ba']
        regular = m['deps'] >= MIN_DEPARTURES if m['deps'] is not None else pax >= MIN_PASSENGERS
        if not regular and key not in wiki:
            dropped['no regular schedule'] += 1
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
        # started (or restarted) partway through: scale up to a year; one month is enough when
        # Wikipedia confirms it's a new route that began in the data's last month
        just_started = key in wiki and m['months'] == 1 and m['first'] == m['end']
        if recent and m['first'] > m['start'] + 1 and not seasonal and (m['months'] >= 2 or just_started):
            scale, months = 12 / m['months'], m['months']
        routes[key] = {'ab': m['ab'] * scale, 'ba': m['ba'] * scale, 'source': m['source'], 'months': months, 'year': 0}
    print(f'  kept {len(routes)} counted routes; dropped {dict(dropped)}')

    # --- busiest-routes tables, for current routes without official counts
    used = 0
    for key, r in reported.items():
        if key in wiki and key not in routes:
            routes[key] = {'ab': r['pax'] / 2, 'ba': r['pax'] / 2, 'source': REPORTED, 'months': 0, 'year': r['year']}
            used += 1
    print(f'  {used} routes from busiest-routes tables')
    listed = load_listed(measured)
    used = 0
    for key, r in listed.items():
        if key in wiki and key not in routes:
            routes[key] = {'ab': r['pax'] / 2, 'ba': r['pax'] / 2, 'source': r['source'], 'months': 0, 'year': r['year']}
            used += 1
    print(f'  {used} routes from the list of busiest routes')

    withheld = eurostat_withheld()
    print(f'  {len(withheld)} airports have routes Eurostat withholds as confidential')

    # --- how good are the estimates? Hide each official source, estimate its routes, compare.
    print('Estimates checked against official counts they did not see:')
    checked = ([], [])
    for held in SOURCE_PRIORITY:
        if not covered_by.get(held):
            continue
        hidden = {k: r for k, r in routes.items() if r['source'] == held and k in wiki}
        rest = {k: r for k, r in routes.items() if r['source'] != held}
        guesses, _ = estimate(rest, wiki, Model(airports, listings, totals), totals,
                              set().union(*(v for s, v in covered_by.items() if s != held)),
                              set().union(*(v for s, v in covered_by.items() if s != held and s in NATIONAL)),
                              set(SOURCE_PRIORITY) - {held}, withheld)
        keys = [k for k in hidden if k in guesses]
        median, weighted, p80 = log_error([guesses[k] for k in keys], [hidden[k]['ab'] + hidden[k]['ba'] for k in keys])
        print(f'  {held:22s} {len(keys):6d} routes: median off by x{median:.2f}, '
              f'by passengers x{weighted:.2f}, 80% within x{p80:.2f}')
        checked[0].extend(guesses[k] for k in keys)
        checked[1].extend(hidden[k]['ab'] + hidden[k]['ba'] for k in keys)
    median, weighted, p80 = log_error(*checked)
    print(f'  {"all":22s} {len(checked[0]):6d} routes: median off by x{median:.2f}, '
          f'by passengers x{weighted:.2f}, 80% within x{p80:.2f}')
    # shown on the site, next to the estimates
    estimate_error = {'median': round(median, 2), 'p80': round(p80, 2)}

    # --- estimate every Wikipedia route without a count
    model = Model(airports, listings, totals)
    national = set().union(*(v for s, v in covered_by.items() if s in NATIONAL))
    estimates, goal = estimate(routes, wiki, model, totals, covered, national, set(SOURCE_PRIORITY), withheld)
    print(f'Estimated {len(estimates)} routes; calibrated to the totals of {len(goal)} airports')
    for key, pax in estimates.items():
        routes[key] = {'ab': pax / 2, 'ba': pax / 2, 'source': ESTIMATE, 'months': 0, 'year': 0}

    # --- hard rules
    closed = set(overrides['closed_countries'])
    shut = {c for c in overrides.get('closed_airports', {}) if not c.startswith('_')}
    blocked = [(set(rule['a']), set(rule['b'])) for rule in overrides['blocked_country_pairs']]
    blocked_routes = {tuple(sorted(rule['pair'])) for rule in overrides.get('blocked_routes', [])}
    removed = []
    for key in list(routes):
        ca, cb = (airports[x]['cc'] for x in key)
        if (ca in closed or cb in closed or key in blocked_routes or shut & set(key)
                or any(x in limited and y not in limited[x]['routes'] for x, y in (key, key[::-1]))
                or any((ca in s and cb in t) or (cb in s and ca in t) for s, t in blocked)):
            removed.append('-'.join(key))
            del routes[key]
    if removed:
        print(f'Overrides removed {len(removed)} routes: {", ".join(sorted(removed)[:30])}')

    frequency = load_frequency(airports)
    print(f'Flights and seats for {sum(1 for k in routes if k in frequency)} routes')
    ways = flight_ways(routes, wiki, airports, overrides)
    times = flight_times(ways, load_block_times(airports), airports)
    write(routes, wiki, airports, countries, measured_sources, today, overrides, estimate_error, frequency, ways, times)


def wikipedia_titles():
    """IATA code -> English Wikipedia title: the page actually fetched (redirects resolved), else
    the best match from Wikidata and OurAirports."""
    from fetch_wikipedia import wikipedia_titles as guess
    titles = guess()
    with open(BUILD / 'wiki_airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['title'] and int(row['tables'] or 0) + (1 if row['passengers'] else 0) > 0:
                titles[row['iata']] = row['title']
    return titles


def wiki_column(codes, airports, titles):
    """Each airport's Wikipedia title, or '' where it's the same as the airport's name."""
    return ['' if titles.get(x, '') == airports[x]['name'] else titles.get(x, '-') for x in codes]


def shrink_logo(src, dest):
    """A copy of a logo at most 240x72 (twice the size it's shown at), as a small palette PNG."""
    from PIL import Image
    with Image.open(src) as image:
        image = image.convert('RGBA')
        image.thumbnail((240, 72), Image.LANCZOS)
        image.quantize(colors=96, method=Image.Quantize.FASTOCTREE).save(dest, optimize=True)


def ground_links(routes, airports, used):
    """[a, b] pairs (indices into `used`) joining each network no flight connects to the rest (the Aran
    Islands, Ketchikan's floatplanes) to the nearest airport on the main network, so "flights away"
    can count the trip there by road or ferry."""
    neighbours = defaultdict(set)
    for a, b in routes:
        neighbours[a].add(b)
        neighbours[b].add(a)
    parts, seen = [], set()
    for start in used:
        if start in seen:
            continue
        part, stack = [], [start]
        seen.add(start)
        while stack:
            x = stack.pop()
            part.append(x)
            for y in neighbours[x] - seen:
                seen.add(y)
                stack.append(y)
        parts.append(part)
    parts.sort(key=len, reverse=True)
    main, index = parts[0], {x: i for i, x in enumerate(used)}
    links = []
    for part in parts[1:]:
        # the nearest pair, preferring one in the same country (Kinshasa's N'Dolo to N'Djili, not Brazzaville)
        cost = lambda xy: haversine(airports[xy[0]], airports[xy[1]]) * (1 if airports[xy[0]]['cc'] == airports[xy[1]]['cc'] else 3)
        a, b = min(((x, y) for x in part for y in main), key=cost)
        links.append([index[a], index[b], round(haversine(airports[a], airports[b]))])
    print(f'{len(parts) - 1} networks unconnected by air, joined by road or ferry: ' +
          ', '.join(f'{used[a]}–{used[b]} {km} km' for a, b, km in links))
    return links


def flight_ways(routes, wiki, airports, overrides):
    """The way each route is flown, around the airspace its airlines avoid (overrides.json's 'airspace'):
    unordered pair -> [{'airlines': [keys], 'km', 'pts': [(lat, lon) waypoints], 'east'}], one for
    each set of airlines that fly it the same way, busiest way first. An airline's country is the
    one most of its routes touch (Lufthansa's are German), and a route never avoids a country it
    flies to or from."""
    from airspace import Airspace, eastward
    rules = overrides.get('airspace', {})
    groups, regions = rules.get('groups', {}), rules.get('regions', {})
    members = lambda spec: None if spec == 'all' else {c for g in spec for c in groups.get(g, [g])}
    rule_list = [(set(r['avoid']), members(r['airlines'])) for r in rules.get('rules', [])]
    space = Airspace({c for avoid, _ in rule_list for c in avoid}, regions)
    homes = defaultdict(Counter)
    for (a, b), p in wiki.items():
        for al in p['airlines']:
            homes[al][airports[a]['cc']] += 1
            homes[al][airports[b]['cc']] += 1
    home = {al: c.most_common(1)[0][0] for al, c in homes.items()}

    def avoided(cc, ends):
        out = set()
        for avoid, who in rule_list:
            if who is None or cc in who:
                out |= {c for c in avoid if c != cc}
        # a route flies into the countries (and regions) its ends are in
        return frozenset(c for c in out if not any(airports[x]['cc'] == c or (c in regions and space.inside(c, airports[x]['lat'], airports[x]['lon']))
                                                   for x in ends))

    asks = defaultdict(list)    # (origin, avoid) -> [(route, airlines)]
    variants = {}
    for key in routes:
        als = sorted(wiki[key]['airlines']) if key in wiki else []
        by_avoid = defaultdict(list)
        for al in als or [None]:
            by_avoid[avoided(home.get(al), key)].append(al)
        variants[key] = by_avoid
        for avoid in by_avoid:
            if avoid:
                asks[(key[0], avoid)].append(key)
    found = {}
    for n, ((origin, avoid), keys) in enumerate(sorted(asks.items(), key=lambda kv: (kv[0][0], sorted(kv[0][1])))):
        o = airports[origin]
        got = space.ways((o['lat'], o['lon']), [(airports[k[1]]['lat'], airports[k[1]]['lon']) for k in keys], avoid)
        for i, way in got.items():
            found[(keys[i], avoid)] = way
    ways = {}
    for key, by_avoid in variants.items():
        a, b = (airports[x] for x in key)
        direct = haversine(a, b)
        out = []
        for avoid, als in by_avoid.items():
            km, pts = found.get((key, avoid), (direct, []))
            # what it goes around: the avoided places the great circle would have crossed
            around = sorted(c for c in avoid if not space.clear((a['lat'], a['lon']), (b['lat'], b['lon']), space.closed([c]), (True, True))) if pts else []
            out.append({'airlines': [al for al in als if al is not None], 'km': km, 'pts': pts, 'around': around,
                        'east': eastward([(a['lat'], a['lon']), *pts, (b['lat'], b['lon'])])})
        # ways flown alike merge (an airline that avoids Russia on a route nowhere near it)
        merged = {}
        for w in out:
            sig = tuple((round(lat, 1), round(lon, 1)) for lat, lon in w['pts'])
            if sig in merged:
                merged[sig]['airlines'] += w['airlines']
            else:
                merged[sig] = w
        ways[key] = sorted(merged.values(), key=lambda w: (-len(w['airlines']), w['km']))
    detoured = sum(1 for w in ways.values() if any(v['pts'] for v in w))
    print(f'Airspace: {detoured} routes flown around closed airspace by some or all of their airlines')
    return ways


def load_block_times(airports):
    """Average gate-to-gate minutes by direction, from US T-100: (origin, dest) -> (minutes, departures)."""
    out = {}
    path = BUILD / 'measured.csv'
    if not path.exists():
        return out
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            if row['source'] == 'us_bts_t100' and row.get('block_minutes') and float(row.get('timed_departures') or 0) >= 20:
                deps = float(row['timed_departures'])
                out[(row['origin_iata'], row['dest_iata'])] = (float(row['block_minutes']) / deps, deps)
    return out


TIME_MODEL = {}   # how close the fitted times come to the US averages, for the site


def flight_times(ways, block, airports):
    """Minutes each way for every route: the US average where T-100 has one, else a fit to those
    averages from the distance flown and how far it runs with or against the jet streams.
    unordered pair -> (a to b, b to a, measured both ways); and each way's own (a to b, b to a)."""
    sample = [(w[0]['km'], w[0]['east'] if (o, d) == key else -w[0]['east'], minutes, deps)
              for key, w in ways.items() if len(w) == 1 and not w[0]['pts']
              for (o, d) in (key, key[::-1]) if (o, d) in block for minutes, deps in [block[(o, d)]] if deps >= 50]
    X = np.array([[1, k, e] for k, e, _, _ in sample])
    y = np.array([m for _, _, m, _ in sample])
    w = np.sqrt([d for *_, d in sample])
    coef = np.linalg.lstsq(X * w[:, None], y * w, rcond=None)[0]
    err = np.abs(X @ coef - y)
    print(f'Flight times: {coef[0]:.0f} min + {coef[1] * 1000:.1f} min per 1,000 km {coef[2] * 1000:+.1f} min per 1,000 km '
          f'east in the jet streams, fitted to {len(sample)} US routes; median off by {np.median(err):.0f} min, 90% within {np.percentile(err, 90):.0f}')
    model = lambda km, east: max(25.0, coef[0] + coef[1] * km + coef[2] * east)
    TIME_MODEL.update(median=round(float(np.median(err))), p90=round(float(np.percentile(err, 90))), routes=len(sample))
    times, each = {}, {}
    for key, w in ways.items():
        each[key] = [(model(v['km'], v['east']), model(v['km'], -v['east'])) for v in w]
        ab, ba = each[key][0]
        mab, mba = block.get(key), block.get(key[::-1])
        times[key] = (mab[0] if mab else ab, mba[0] if mba else ba, bool(mab and mba))
    return times, each


def city_name(place):
    """'New York metropolitan area' -> 'New York', 'Greater Boston' -> 'Boston', 'Victoria, British
    Columbia' -> 'Victoria'."""
    name = re.sub(r'^(The |Greater )', '', place.split(',')[0])
    name = re.sub(r' (metropolitan area|Metropolitan Area|Metropolitan Region|metroplex|metro area|area|Region)$', '', name)
    return name.strip() or place


def cities(routes, airports, overrides):
    """Cities with several airports: [{'name', 'cc', 'airports': [codes, busiest first]}], busiest first.
    See overrides.json's 'cities' for the rules."""
    rules = overrides.get('cities', {})
    pax = defaultdict(float)
    for (a, b), r in routes.items():
        pax[a] += r['ab'] + r['ba']
        pax[b] += r['ab'] + r['ba']
    serves = {}
    with open(BUILD / 'wiki_airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row.get('serves') and pax[row['iata']] >= 100_000:
                serves[row['iata']] = row['serves']
    groups = defaultdict(list)
    for code, place in serves.items():
        groups[rules.get('rename', {}).get(place) or city_name(place)].append(code)
    added = {name: [c for c in codes if pax[c]] for name, codes in rules.get('add', {}).items()}
    taken = {c for codes in added.values() for c in codes}
    found = []
    for name, codes in groups.items():
        if name in added:
            continue   # 'add' redefines it
        codes = sorted((c for c in codes if c not in taken), key=lambda c: -pax[c])
        if codes:
            top = airports[codes[0]]
            codes = [c for c in codes if airports[c]['cc'] == top['cc'] and haversine(top, airports[c]) <= 85]
        found.append((name, codes))
    found += [(name, sorted(codes, key=lambda c: -pax[c])) for name, codes in added.items()]
    out = [{'name': name, 'cc': airports[codes[0]]['cc'], 'airports': codes, 'pax': sum(pax[c] for c in codes)}
           for name, codes in found if len(codes) > 1]
    out.sort(key=lambda c: -c['pax'])
    print(f'{len(out)} cities with several airports: ' + ', '.join(f"{c['name']} ({' '.join(c['airports'])})" for c in out[:12]) + ' …')
    return out


def write(routes, wiki, airports, countries, measured_sources, today, overrides, estimate_error, frequency, ways, times):
    titles = wikipedia_titles()
    used = sorted({x for key in routes for x in key})
    index = {x: i for i, x in enumerate(used)}
    # airlines: display name, Wikipedia title ('' when it's the name, '-' when none), logo file
    keys = sorted({a for k, p in wiki.items() if k in routes for a in p['airlines']}, key=lambda a: airlines_seen[a].lower())
    airline_index = {a: i for i, a in enumerate(keys)}
    logos = json.loads((BUILD / 'airline_logos.json').read_text()) if (BUILD / 'airline_logos.json').exists() else {}
    logo_dir = OUT.parent / 'logos'
    if logo_dir.exists():
        shutil.rmtree(logo_dir)
    logo_dir.mkdir(parents=True)
    airline_logo = []
    for a in keys:
        file = logos.get(a, '')
        if file and (CACHE / 'logos' / file).exists():
            shrink_logo(CACHE / 'logos' / file, logo_dir / file)
        else:
            file = ''
        airline_logo.append(file)
    airline_wiki = ['-' if a.startswith('name:') else '' if a == airlines_seen[a] else a for a in keys]

    source_list = [{'id': 0, 'key': ESTIMATE, 'name': 'Estimate', 'short': 'estimates'}]
    for s in sorted(measured_sources.get('sources', []), key=lambda s: SOURCE_PRIORITY.index(s['id']) if s['id'] in SOURCE_PRIORITY else 99):
        start, end = (month_index(m) for m in measured_sources.get('usual_period', {}).get(s['id'], (s['period_start'], s['period_end'])))
        source_list.append({'id': len(source_list), 'key': s['id'], 'name': s.get('publisher', s['name']),
                            'short': SHORT_NAMES.get(s['id'], s['name']), 'url': s.get('url', ''),
                            'period': f'{month_name(start)} – {month_name(end)}',
                            'directional': bool(s.get('directional', True))})
    source_list.append({'id': len(source_list), 'key': REPORTED, 'short': 'airport statistics',
                        'name': "The airport's published statistics, from its Wikipedia page"})
    source_list.append({'id': len(source_list), 'key': LISTED, 'short': 'list of busiest routes',
                        'name': "Wikipedia's list of the busiest passenger air routes"})
    source_list.append({'id': len(source_list), 'key': LISTED_SEATS, 'short': 'list of busiest routes (seats)',
                        'name': "Seats in Wikipedia's list of the busiest passenger air routes, at the load factor of its counted routes"})
    source_id = {s['key']: s['id'] for s in source_list}

    # fw: flights a week each way x10, fm: months a year it flies, fs: seats a flight, ff: % of seats
    # filled, fsrc: the source of these (0 where there's none)
    # ta, tb: minutes from a to b and back; tq: 1 where both are US averages, not the fit
    out = {'a': [], 'b': [], 'ab': [], 'ba': [], 'src': [], 'al': [], 's': [], 'n': [], 'y': [],
           'fw': [], 'fm': [], 'fs': [], 'ff': [], 'fsrc': [], 'ta': [], 'tb': [], 'tq': []}
    # routes some airlines fly around closed airspace: route index -> [[airlines, km, minutes a to b,
    # b to a, waypoints as lon, lat, lon, lat...], ...], the way most of its airlines go first
    around = {}
    minutes, each = times
    for key in sorted(routes, key=lambda k: -(routes[k]['ab'] + routes[k]['ba'])):
        r = routes[key]
        rough = r['source'] in (ESTIMATE, REPORTED, LISTED, LISTED_SEATS)
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
        f = frequency.get(key)
        out['fw'].append(max(1, round(10 * f['per_week'])) if f else 0)
        out['fm'].append(f['months'] if f else 0)
        out['fs'].append(round(f['seats']) if f else 0)
        out['ff'].append(round(100 * f['full']) if f else 0)
        out['fsrc'].append(source_id.get(f['source'], 0) if f else 0)
        ta, tb, measured = minutes[key]
        out['ta'].append(round(ta))
        out['tb'].append(round(tb))
        out['tq'].append(1 if measured else 0)
        w = ways[key]
        if any(v['pts'] for v in w):
            around[len(out['a']) - 1] = [[sorted(airline_index[a] for a in v['airlines'] if a in airline_index), round(v['km']),
                                          round(t[0]), round(t[1]), [round(c, 1) for lat, lon in v['pts'] for c in (lon, lat)],
                                          [countries.get(c, c) for c in v['around']]]
                                         for v, t in zip(w, each[key])]

    data = {
        'routes_as_of': today.strftime('%B %Y'),
        'built': today.isoformat(),
        'about': ABOUT,
        'estimate_error': estimate_error,
        'sources': source_list,
        'countries': {c: countries.get(c, c) for c in sorted({airports[x]['cc'] for x in used})},
        'airlines': [airlines_seen[a] for a in keys],
        'airline_wiki': airline_wiki,
        'airline_logo': airline_logo,
        'airports': {
            'code': used,
            'name': [airports[x]['name'] for x in used],
            'city': [airports[x].get('label') or airports[x]['city'] for x in used],
            'town': [airports[x]['city'] if airports[x].get('label') and airports[x]['city'] != airports[x]['label'] else ''
                     for x in used],
            'country': [airports[x]['cc'] for x in used],
            'lat': [round(airports[x]['lat'], 4) for x in used],
            'lon': [round(airports[x]['lon'], 4) for x in used],
            'wiki': wiki_column(used, airports, titles),
        },
        'routes': out,
        'around': around,
        'time_model': TIME_MODEL,
        'airspace': [{'name': r['name'], 'source': r['source']} for r in overrides.get('airspace', {}).get('rules', [])],
        'airspace_as_of': overrides.get('airspace', {}).get('as_of', ''),
        # why an airport has fewer routes than its Wikipedia page lists
        'airport_notes': {c: v['note'] for c, v in overrides.get('limited_airports', {}).items() if not c.startswith('_') and c in index},
        'ground': ground_links(routes, airports, used),
        # cities with several airports: name, country, airports (indices, busiest first)
        'cities': [{'name': c['name'], 'cc': c['cc'], 'airports': [index[x] for x in c['airports']]}
                   for c in cities(routes, airports, overrides)],
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    write_others(set(used), countries, titles, overrides)
    total = sum(r['ab'] + r['ba'] for r in routes.values())
    share = {s: sum(r['ab'] + r['ba'] for r in routes.values() if r['source'] == s) / total
             for s in {r['source'] for r in routes.values()}}
    print(f'Wrote {OUT.relative_to(HERE.parent)}: {len(used)} airports, {len(routes)} routes, '
          f'{total / 1e9:.2f}B passengers a year, {OUT.stat().st_size / 1e6:.1f} MB')
    print('  share of passengers by source: ' + ', '.join(f'{s} {100 * v:.0f}%' for s, v in sorted(share.items(), key=lambda kv: -kv[1])))


def write_others(used, countries, titles, overrides):
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
        'wiki': ['' if titles.get(c, '') == others[c]['name'] else titles.get(c, '-') for c in codes],
        'countries': {cc: countries.get(cc, cc) for cc in sorted({others[c]['iso_country'] for c in codes})},
        'closed': overrides['closed_countries'],
        'closed_airports': {c: note for c, note in overrides.get('closed_airports', {}).items() if not c.startswith('_')},
    }
    path = OUT.parent / 'others.json'
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    print(f'Wrote {path.relative_to(HERE.parent)}: {len(codes)} airports without airline routes, {path.stat().st_size / 1e3:.0f} KB')


if __name__ == '__main__':
    main()
