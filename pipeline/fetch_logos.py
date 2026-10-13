"""Airline logos from Wikimedia Commons, found through Wikidata.

For every airline listed on a current route (build/wiki_routes.csv, by its English Wikipedia
article), asks Wikidata for its logo image (P154, falling back to its small logo or icon, P8972) and
downloads a 160-pixel-wide PNG rendering from Commons into cache/logos/. Writes
build/airline_logos.json: article title -> logo file in cache/logos/. Re-running only fetches
what's missing.

Commons hosts only freely licensed or public-domain files (airline logos are mostly simple
text-and-shape marks); the logos remain their owners' trademarks.

    python3 fetch_logos.py
"""

import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from fetch_wikipedia import CACHE, UA, polite_get

HERE = Path(__file__).resolve().parent
BUILD = HERE / 'build'
LOGOS = CACHE / 'logos'
SPARQL = 'https://query.wikidata.org/sparql'


def airline_titles():
    counts = Counter()
    with open(BUILD / 'wiki_routes.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['status'] == 'current' and row['airline_title']:
                counts[row['airline_title']] += 1
    return [t for t, _ in counts.most_common()]


def wikidata_logos(titles):
    """Title -> Commons file name, for the airlines Wikidata has a logo for."""
    found = {}
    for start in range(0, len(titles), 120):
        batch = titles[start:start + 120]
        values = ' '.join('"%s"@en' % t.replace('\\', '\\\\').replace('"', '\\"') for t in batch)
        query = f'''SELECT ?title ?logo ?icon WHERE {{
          VALUES ?title {{ {values} }}
          ?article schema:name ?title; schema:isPartOf <https://en.wikipedia.org/>; schema:about ?item.
          OPTIONAL {{ ?item wdt:P154 ?logo }}
          OPTIONAL {{ ?item wdt:P8972 ?icon }}
        }}'''
        url = SPARQL + '?' + urllib.parse.urlencode({'query': query, 'format': 'json'})
        data = json.loads(polite_get(url))
        for b in data['results']['bindings']:
            image = (b.get('logo') or b.get('icon') or {}).get('value')
            if image and b['title']['value'] not in found:
                found[b['title']['value']] = urllib.parse.unquote(image.rsplit('/', 1)[1])
        time.sleep(1)
    return found


def download(file_name):
    """A 160px-wide PNG of the Commons file, cached; None if it can't be had."""
    name = hashlib.sha1(file_name.encode()).hexdigest()[:16] + '.png'
    path = LOGOS / name
    if path.exists():
        return name
    url = 'https://commons.wikimedia.org/wiki/Special:FilePath/' + urllib.parse.quote(file_name.replace(' ', '_')) + '?width=160'
    try:
        request = urllib.request.Request(url, headers={'User-Agent': UA})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read()
                    kind = response.headers.get('Content-Type', '')
                break
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504):
                    time.sleep(2 * (attempt + 1))
                    continue
                return None
        else:
            return None
    except Exception:
        return None
    if not kind.startswith('image/') or len(body) > 200_000:
        return None
    path.write_bytes(body)
    time.sleep(0.3)
    return name


def main():
    LOGOS.mkdir(parents=True, exist_ok=True)
    out_path = BUILD / 'airline_logos.json'
    known = json.loads(out_path.read_text()) if out_path.exists() else {}
    titles = airline_titles()
    files = wikidata_logos([t for t in titles if t not in known])
    for k, (title, file_name) in enumerate(files.items()):
        name = download(file_name)
        if name:
            known[title] = name
        if k % 50 == 49:
            print(f'{k + 1}/{len(files)} logos', flush=True)
            out_path.write_text(json.dumps(known, ensure_ascii=False, indent=0))
    out_path.write_text(json.dumps(known, ensure_ascii=False, indent=0))
    print(f'{len(titles)} airlines; Wikidata has logos for {len(files)} more; {len(known)} logos in all')


if __name__ == '__main__':
    main()
