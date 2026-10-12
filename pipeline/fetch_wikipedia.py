"""Download the route tables and infobox of every airport's English Wikipedia article.

Reads OurAirports (cache/airports.csv) and the Wikidata IATA list (cache/wikidata_iata.csv),
fetches https://en.wikipedia.org/wiki/<title> for every airport with scheduled service, and keeps
only the parts the parser needs: the canonical title, the infobox, and every "airlines and
destinations"-style or busiest-routes table with the headings it sits under. Each page is saved as
cache/wiki/<IATA>.html.gz, so re-running skips pages already fetched (pass --refresh to refetch).
Busiest-routes tables (with passenger counts) are kept too, as class "stat".

    python3 fetch_wikipedia.py [--refresh] [--only MIA,CCS]
"""

import argparse
import csv
import gzip
import json
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache'
OUT = CACHE / 'wiki'
UA = 'FlightAtlasBuilder/0.1 (https://flights.theojaffee.net/; data pipeline for a static map)'
WORKERS = 4


def title_from_url(url):
    if not url or '//en.wikipedia.org/wiki/' not in url:
        return None
    title = urllib.parse.unquote(url.split('/wiki/', 1)[1]).split('#')[0]
    return title.replace('_', ' ').strip() or None


def airports_to_fetch():
    """Every scheduled-service airport with an IATA code, with its English Wikipedia title.

    Wikidata's sitelink is preferred because OurAirports' links are often redirects or, now and
    then, disambiguation pages. When several Wikidata items share an IATA code, the ICAO code
    picks between them.
    """
    wikidata = {}
    with open(CACHE / 'wikidata_iata.csv', newline='') as f:
        for row in csv.DictReader(f):
            title = title_from_url(row['article'])
            if title:
                wikidata.setdefault(row['iata'], []).append((row['icao'], title))
    # Civil airports often share their codes with an air base on the same field.
    military = re.compile(r'\b(air (base|station|force)|joint base|naval|army|airfield \(military\)|list of)\b', re.I)
    out = {}
    with open(CACHE / 'airports.csv', newline='') as f:
        for row in csv.DictReader(f):
            iata = row['iata_code'].strip()
            if row['scheduled_service'] != 'yes' or not iata or row['type'] == 'closed':
                continue
            linked = title_from_url(row['wikipedia_link'])
            candidates = [t for _, t in wikidata.get(iata, [])]
            icao = row['icao_code'] or row['gps_code'] or row['ident']
            civil = [t for c, t in wikidata.get(iata, []) if not military.search(t)]
            same_icao = [t for c, t in wikidata.get(iata, []) if c == icao and not military.search(t)]
            if linked in candidates:
                title = linked
            elif same_icao:
                title = same_icao[0]
            elif len(set(civil)) == 1:
                title = civil[0]
            else:
                title = linked
            if title:
                out[iata] = title
    return out


def headings_for(table):
    """Headings a table sits under, outermost first (Parsoid sections, else preceding headings)."""
    chain = []
    for section in table.find_parents('section'):
        label = section.get('aria-labelledby')
        if label:
            chain.append(label.replace('_', ' '))
    if chain:
        return list(reversed(chain))
    previous = table.find_previous(['h2', 'h3', 'h4'])
    return [previous.get_text(' ', strip=True)] if previous else []


def table_kind(table):
    """'dest' for an airlines-and-destinations table, 'stat' for a busiest-routes table, else None."""
    header = table.find('tr')
    if not header:
        return None
    text = header.get_text(' ', strip=True).lower()
    if 'passeng' in text and re.search(r'destination|route|city|airport|rank', text):
        return 'stat'
    if 'airline' in text and 'destination' in text:
        return 'dest'
    return None


def trim(page_html):
    soup = BeautifulSoup(page_html, 'lxml')
    canonical = soup.find('link', rel='canonical')
    title = title_from_url(canonical['href']) if canonical else None
    parts = [f'<meta name="title" content="{title or ""}">']
    infobox = soup.select_one('table.infobox')
    if infobox:
        parts.append(str(infobox))
    for table in soup.find_all('table', class_='wikitable'):
        kind = table_kind(table)
        if kind:
            heads = json.dumps(headings_for(table)).replace("'", '&#39;')
            parts.append(f"<div class=\"{kind}\" data-headings='{heads}'>{table}</div>")
    return '\n'.join(parts)


_rate_lock = threading.Lock()
_next_slot = [0.0]


def polite_get(url):
    """GET with a shared ~4 requests/second budget and backoff on 429/5xx."""
    delay = 2.0
    for attempt in range(12):
        with _rate_lock:
            wait = _next_slot[0] - time.monotonic()
            _next_slot[0] = max(time.monotonic(), _next_slot[0]) + 0.25
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'gzip'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                if response.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                return body.decode('utf-8', 'replace')
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(delay + random.random() * delay)
        delay = min(delay * 1.7, 60)
    raise RuntimeError(f'gave up on {url}')


def fetch(iata, title, refresh):
    path = OUT / f'{iata}.html.gz'
    if path.exists() and not refresh:
        return 'cached'
    url = 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(title.replace(' ', '_'), safe="()_,'!-.")
    try:
        page = polite_get(url)
    except Exception as error:  # keep going; the next run retries
        print(f'{iata} {title}: {error}', file=sys.stderr)
        return 'failed'
    if page is None:
        return 'missing'
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        f.write(trim(page))
    return 'fetched'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true', help='refetch pages already cached')
    parser.add_argument('--only', help='comma-separated IATA codes')
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    todo = airports_to_fetch()
    if args.only:
        wanted = set(args.only.split(','))
        todo = {k: v for k, v in todo.items() if k in wanted}
    (CACHE / 'wiki_titles.json').write_text(json.dumps(todo, ensure_ascii=False, indent=0))

    counts = {}
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(fetch, iata, title, args.refresh): iata for iata, title in sorted(todo.items())}
        for future in futures:
            status = future.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if done % 200 == 0:
                print(f'{done}/{len(todo)} {counts}', flush=True)
    print(f'{len(todo)} airports: {counts}')


if __name__ == '__main__':
    main()
