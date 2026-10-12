"""Turn the cached Wikipedia pages into route and airport tables.

Reads cache/wiki/<IATA>.html.gz (from fetch_wikipedia.py) and writes:

    build/wiki_routes.csv   one row per (airport, airline, destination) listed in a passenger table
    build/wiki_airports.csv each airport's annual passengers and year from its infobox
    build/wiki_route_stats.csv passengers per route from airports' "busiest routes" tables

Destinations are links to airport articles. They are resolved to IATA codes through Wikidata's
sitelinks, the pages we fetched, and (for redirects) the MediaWiki API, cached in
cache/redirects.json.

Each listing gets a status from its note: "begins <future date>" is not flying yet, "resumes
<future date>" is 'resuming' (flying again soon, unless it's a seasonal route, which is current),
"ends <past date>" and "suspended" no longer fly, and "via X" means the flight stops at X on the
way, so it is not a nonstop segment.

    python3 parse_wikipedia.py [--today 2026-10-12]
"""

import argparse
import csv
import datetime as dt
import gzip
import json
import re
import time
import unicodedata
import urllib.parse
from collections import defaultdict
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from dateutil import parser as dateparser

from fetch_wikipedia import CACHE, UA, airports_to_fetch, polite_get, title_from_url

HERE = Path(__file__).resolve().parent
BUILD = HERE / 'build'

SKIP_HEADINGS = re.compile(r'cargo|freight|former|chronolog|histor|defunct|terminated|ceased|busiest|statistic|traffic|'
                           r'helicopter|future|planned|proposed|incident|accident', re.I)
LABEL = re.compile(r"(seasonal\s+charter|charter|seasonal|year[- ]round|regular)\s*:", re.I)


def fold(text):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def norm_title(title):
    title = urllib.parse.unquote(title).replace('_', ' ').split('#')[0].strip()
    return title[:1].upper() + title[1:] if title else title


def link_title(a):
    href = a.get('href', '')
    if '/wiki/' in href:
        return norm_title(href.split('/wiki/', 1)[1])
    if href.startswith('./'):
        return norm_title(href[2:])
    return None


def parse_date(text, today):
    """A date in a route note, or None. Year-only and month-year dates take their first day."""
    text = re.sub(r'(?i)\b(early|mid|late|summer|winter|spring|autumn|fall|from|on|in|the)\b', ' ', text)
    text = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', text).strip(' ,.')
    if not re.search(r'\d{4}', text):
        return None
    try:
        return dateparser.parse(text, default=dt.datetime(today.year, 1, 1), fuzzy=True).date()
    except (ValueError, OverflowError):
        year = re.search(r'(20\d\d)', text)
        return dt.date(int(year.group(1)), 1, 1) if year else None


def classify(note, mode, today):
    """('current' | 'future' | 'ended' | 'suspended' | 'via', detail)."""
    lowered = note.lower()
    if not lowered:
        return 'current', ''
    if re.search(r'\bvia\b', lowered):
        return 'via', note
    if 'suspend' in lowered or 'cancel' in lowered:
        return 'suspended', note
    for word in ('begin', 'commence', 'start', 'launch', 'resum', 'restart', 'return'):
        if word in lowered:
            when = parse_date(note, today)
            if when is None or when > today:
                if word in ('resum', 'restart', 'return'):
                    # A seasonal route resuming next season flew this year; any other route
                    # resuming may have been gone for years (build_routes.py checks the counts).
                    return ('current' if mode.startswith('seasonal') else 'resuming'), note
                return 'future', note
            return 'current', note
    for word in ('end', 'terminat', 'cease', 'discontinu', 'until'):
        if word in lowered:
            when = parse_date(note, today)
            if when is not None and when < today:
                return 'ended', note
            return 'current', note
    return 'current', note


def clean(cell):
    for junk in cell.select('sup, style, link, .noprint, .mw-ref, .reference'):
        junk.decompose()
    return cell


def mode_from_label(label):
    label = re.sub(r'\s+', ' ', label.lower())
    if label in ('year-round', 'year round', 'regular'):
        return 'scheduled'
    return label.replace(' ', '_')


def parse_destinations(cell, today):
    """[(title, display, mode, status, note)] for each linked destination in a table cell.

    Destinations are links separated by commas or line breaks, optionally followed by a note in
    parentheses. A "Seasonal:" or "Charter:" label applies to every destination after it. Links
    inside a note (the stop in "Dubai (via Muscat)") belong to the note, not the list.
    """
    results = []
    mode = 'scheduled'
    pending = None  # [title, display, mode, note] of the last destination, until its note ends
    depth = 0       # unclosed parentheses since the last destination

    def flush():
        nonlocal pending
        if pending:
            title, display, its_mode, note = pending
            note = re.sub(r'\s+', ' ', note.replace('(', ' ').replace(')', ' ')).strip(' ,')
            status, detail = classify(note, its_mode, today)
            results.append((title, display, its_mode, status, detail))
        pending = None

    for node in clean(cell).descendants:
        if isinstance(node, Tag) and node.name == 'a':
            title = link_title(node)
            if not title or ':' in title.split(' ')[0]:
                continue
            if depth > 0 and pending:
                pending[3] += node.get_text(' ', strip=True)
                continue
            flush()
            pending = [title, node.get_text(' ', strip=True), mode, '']
            depth = 0
            continue
        if not isinstance(node, NavigableString) or node.find_parent('a'):
            continue
        text = str(node)
        label = LABEL.search(text)
        if label and depth == 0:
            flush()
            mode = mode_from_label(label.group(1))
            continue
        if not pending:
            continue
        for char in text:
            if char == '(':
                depth += 1
            if depth > 0:
                pending[3] += char
            if char == ')':
                depth = max(0, depth - 1)
            elif depth == 0 and char in ',\n':
                flush()
                break
    flush()
    return results


def number(text):
    """The first count in an infobox value: "55,314,661", "42 540 000" or "43.5 million"."""
    match = re.search(r'(\d+(?:\.\d+)?)\s*million', text, re.I)
    if match:
        return round(float(match.group(1)) * 1e6)
    # Thousands grouped by commas, dots, or (only when nothing like a decimal or % follows) spaces;
    # the change from last year often comes right after ("1,539,674 186.97%").
    for pattern in (r'\d{1,3}(?:,\d{3})+(?!\d)', r'\d{1,3}(?:\.\d{3})+(?![\d,])',
                    r'\d{1,3}(?:[\s\u00a0\u202f]\d{3})+(?![\d.,%])', r'\d+'):
        match = re.search(pattern, text)
        if match:
            return int(re.sub(r'\D', '', match.group(0)))
    return None


def infobox_passengers(soup):
    """(passengers, year) from the infobox's statistics block, if any."""
    box = soup.select_one('table.infobox')
    if not box:
        return None, None
    year = None
    for row in box.find_all('tr'):
        header = row.find('th', class_='infobox-header')
        if header:
            found = re.search(r'(19|20)\d\d', header.get_text())
            if found and 'statistic' in header.get_text().lower():
                year = int(found.group(0))
            continue
        label = row.find('th', class_='infobox-label')
        data = row.find('td', class_='infobox-data')
        if not label or not data or year is None:
            continue
        name = label.get_text(' ', strip=True).lower()
        if 'passenger' in name and not re.search(r'change|growth|%|international|domestic|transit|cargo|movement', name):
            for junk in data.select('sup, img, span[typeof="mw:File"]'):
                junk.decompose()
            value = number(data.get_text(' ', strip=True))
            own_year = re.search(r'(19|20)\d\d', name)
            if value and value > 100:
                return value, int(own_year.group(0)) if own_year else year
    return None, None


YEAR = re.compile(r'\b(19[89]\d|20[0-4]\d)\b')


def parse_route_stats(block, today):
    """Rows of a busiest-routes table: [(link titles, display text, passengers)], with the table's
    year and whether its counts are one-way (departing only). Returns (rows, year, one_way)."""
    table = block.table
    header = [c.get_text(' ', strip=True) for c in table.find('tr').find_all(['th', 'td'])]
    lowered = [h.lower() for h in header]
    place_col = next((i for i, h in enumerate(lowered) if re.search(r'destination|airport|route|city', h)
                      and 'airline' not in h and 'carrier' not in h), None)
    pax_cols = [i for i, h in enumerate(lowered) if 'passeng' in h and not re.search(r'change|%|share|growth|var', h)]
    if place_col is None or not pax_cols:
        return [], None, False
    # With a column per year, take the latest one.
    pax_col = max(pax_cols, key=lambda i: max((int(y) for y in YEAR.findall(header[i])), default=0))
    caption = table.find('caption')
    context = ' '.join(json.loads(block['data-headings']) + [caption.get_text(' ') if caption else '', header[pax_col]])
    years = [int(y) for y in YEAR.findall(context) if int(y) <= today.year]
    year = max(years) if years else None
    one_way = bool(re.search(r'depart|outbound|enplan|from\b', header[pax_col].lower()))
    rows = []
    for tr in table.find_all('tr')[1:]:
        cells = tr.find_all(['td', 'th'], recursive=False)
        if len(cells) != len(header):
            continue
        cell = clean(cells[place_col])
        titles = [link_title(a) for a in cell.find_all('a')]
        titles = [t for t in titles if t and ':' not in t.split(' ')[0]]
        value = number(cells[pax_col].get_text(' ', strip=True))
        if value and value >= 1000:
            rows.append((titles, cell.get_text(' ', strip=True), value))
    return rows, year, one_way


def resolve_redirects(titles, cache_path):
    """Map titles to their redirect targets through the MediaWiki API (cached)."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = sorted(t for t in titles if t not in cache)
    for start in range(0, len(todo), 50):
        batch = todo[start:start + 50]
        query = urllib.parse.urlencode({'action': 'query', 'redirects': 1, 'format': 'json',
                                        'formatversion': 2, 'titles': '|'.join(batch)})
        data = json.loads(polite_get('https://en.wikipedia.org/w/api.php?' + query))
        mapping = {t: t for t in batch}
        for item in data['query'].get('normalized', []):
            for t in batch:
                if mapping[t] == item['from']:
                    mapping[t] = item['to']
        redirects = {r['from']: r['to'].split('#')[0] for r in data['query'].get('redirects', [])}
        for t in batch:
            target = mapping[t]
            for _ in range(3):
                target = redirects.get(target, target)
            cache[t] = target
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
        time.sleep(0.5)
    return cache


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--today', default=dt.date.today().isoformat())
    args = parser.parse_args()
    today = dt.date.fromisoformat(args.today)
    BUILD.mkdir(exist_ok=True)

    # Title -> IATA, from Wikidata sitelinks and from the pages themselves.
    title_to_iata = {}
    with open(CACHE / 'wikidata_iata.csv', newline='') as f:
        for row in csv.DictReader(f):
            title = title_from_url(row['article'])
            if title:
                title_to_iata.setdefault(norm_title(title), row['iata'])
    for iata, title in airports_to_fetch().items():
        title_to_iata[norm_title(title)] = iata

    listings, airports, stats = [], [], []
    for path in sorted((CACHE / 'wiki').glob('*.html.gz')):
        iata = path.name.split('.')[0]
        soup = BeautifulSoup(gzip.open(path, 'rt', encoding='utf-8').read(), 'lxml')
        meta = soup.find('meta', attrs={'name': 'title'})
        own_title = norm_title(meta['content']) if meta and meta.get('content') else ''
        if own_title:
            title_to_iata[own_title] = iata
        passengers, year = infobox_passengers(soup)
        tables = 0
        for block in soup.select('div.dest'):
            headings = json.loads(block['data-headings'])
            if any(SKIP_HEADINGS.search(h) for h in headings[1:] or headings):
                continue
            header = [c.get_text(' ', strip=True).lower() for c in block.table.find('tr').find_all(['th', 'td'])]
            if any(re.search(r'rank|passengers|frequency|weekly', h) for h in header):
                continue
            tables += 1
            for row in block.table.find_all('tr')[1:]:
                cells = row.find_all(['td', 'th'], recursive=False)
                if len(cells) < 2:
                    continue
                airline = cells[0].get_text(' ', strip=True)
                for title, display, mode, status, note in parse_destinations(cells[1], today):
                    listings.append({'origin': iata, 'airline': airline, 'dest_title': title,
                                     'dest_name': display, 'mode': mode, 'status': status, 'note': note})
        for block in soup.select('div.stat'):
            rows, year, one_way = parse_route_stats(block, today)
            for titles, text, value in rows:
                stats.append({'airport': iata, 'titles': titles, 'text': text, 'passengers': value,
                              'year': year or '', 'one_way': int(one_way),
                              'table': ' / '.join(json.loads(block['data-headings']))})
        airports.append({'iata': iata, 'title': own_title, 'tables': tables,
                         'passengers': passengers or '', 'passengers_year': year or ''})

    unresolved = {l['dest_title'] for l in listings if l['dest_title'] not in title_to_iata}
    unresolved |= {t for row in stats for t in row['titles'] if t not in title_to_iata}
    redirects = resolve_redirects(unresolved, CACHE / 'redirects.json')
    missing = {}
    for listing in listings:
        title = listing['dest_title']
        dest = title_to_iata.get(title) or title_to_iata.get(norm_title(redirects.get(title, '')))
        listing['dest'] = dest or ''
        if not dest:
            missing[title] = missing.get(title, 0) + 1

    # Busiest-routes tables: the destination is the linked airport that isn't this one; failing a
    # link, the name this airport's own route table uses for it ("Seoul–Gimpo").
    names = defaultdict(dict)
    for listing in listings:
        if listing['dest']:
            names[listing['origin']][fold(listing['dest_name'])] = listing['dest']
    for row in stats:
        found = [title_to_iata.get(t) or title_to_iata.get(norm_title(redirects.get(t, ''))) for t in row['titles']]
        found = [x for x in found if x and x != row['airport']]
        row['dest'] = found[0] if len(set(found)) == 1 else names[row['airport']].get(fold(row['text']), '')
    with open(BUILD / 'wiki_route_stats.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['airport', 'dest', 'passengers', 'year', 'one_way', 'text', 'table'],
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(row for row in stats if row['dest'])
    print(f'{len(stats)} busiest-route rows, {sum(1 for r in stats if r["dest"])} resolved to an airport')

    fields = ['origin', 'dest', 'airline', 'mode', 'status', 'note', 'dest_title', 'dest_name']
    with open(BUILD / 'wiki_routes.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(listings)
    with open(BUILD / 'wiki_airports.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['iata', 'title', 'tables', 'passengers', 'passengers_year'])
        writer.writeheader()
        writer.writerows(airports)

    statuses = {}
    for listing in listings:
        statuses[listing['status']] = statuses.get(listing['status'], 0) + 1
    print(f'{len(airports)} pages, {sum(1 for a in airports if a["tables"])} with route tables, '
          f'{len(listings)} listings {statuses}')
    print(f'{sum(missing.values())} listings to {len(missing)} titles not resolved to an airport; most common:')
    for title, count in sorted(missing.items(), key=lambda kv: -kv[1])[:25]:
        print(f'  {count:4d}  {title}')


if __name__ == '__main__':
    main()
