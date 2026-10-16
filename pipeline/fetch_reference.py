"""Download the reference lists the rest of the pipeline reads, into cache/.

    cache/airports.csv, countries.csv, regions.csv   OurAirports (public domain)
    cache/wikidata_iata.csv                          every Wikidata item with an IATA airport code,
                                                     its ICAO code and English Wikipedia article

    python3 fetch_reference.py            # skips files fetched in the last week
    python3 fetch_reference.py --refresh
"""

import argparse
import csv
import io
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fetch_wikipedia import CACHE, UA

OURAIRPORTS = 'https://davidmegginson.github.io/ourairports-data/{}.csv'
SPARQL = 'https://query.wikidata.org/sparql'
QUERY = ('SELECT ?item ?iata ?icao ?article WHERE { ?item wdt:P238 ?iata . '
         'OPTIONAL { ?item wdt:P239 ?icao } '
         'OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> } }')
# A short or truncated download is never put in place of a good one.
MIN_ROWS = {'airports': 50_000, 'countries': 200, 'regions': 3_000, 'wikidata_iata': 8_000}


def get(url, data=None, headers=None):
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, data=data, headers={'User-Agent': UA, **(headers or {})})
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read().decode('utf-8')
        except OSError as error:
            if attempt == 4:
                raise
            print(f'  {error}; retrying')
            time.sleep(10 * (attempt + 1))


def save(name, text):
    rows = sum(1 for _ in csv.reader(io.StringIO(text))) - 1
    if rows < MIN_ROWS[name]:
        raise SystemExit(f'{name}: only {rows} rows, expected at least {MIN_ROWS[name]}; keeping the old file')
    (CACHE / f'{name}.csv').write_text(text)
    print(f'{name}.csv: {rows:,} rows')


def fresh(name, refresh):
    path = CACHE / f'{name}.csv'
    return not refresh and path.exists() and time.time() - path.stat().st_mtime < 7 * 86400


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true', help='download even if fetched this week')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    for name in ('airports', 'countries', 'regions'):
        if not fresh(name, args.refresh):
            save(name, get(OURAIRPORTS.format(name)))
    if not fresh('wikidata_iata', args.refresh):
        body = urllib.parse.urlencode({'query': QUERY}).encode()
        save('wikidata_iata', get(SPARQL, body, {'Accept': 'text/csv'}))


if __name__ == '__main__':
    main()
