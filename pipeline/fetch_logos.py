"""Airline logos, for every airline listed on a current route (build/wiki_routes.csv, by its
English Wikipedia article), from the first of:

  1. Wikidata's logo image (P154, else its small logo or icon, P8972), a file on Wikimedia Commons;
  2. the logo file the airline's English Wikipedia article shows (skipping old and former logos).
     Many of these are English Wikipedia's own "non-free" files, which it uses to identify the
     airline; Flight Atlas uses them the same way;
  3. for a brand without a logo of its own (American Eagle), its parent airline's logo.

Each is downloaded as a 160-pixel-wide PNG into cache/logos/. Writes build/airline_logos.json
(article title -> file) and build/airline_logo_sources.json (title -> where it came from).
Re-running only fetches what's missing. The logos remain their owners' trademarks.

    python3 fetch_logos.py
"""

import csv
import hashlib
import re
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


def download(file_name, url=None):
    """A 160px-wide PNG of the file (Commons unless a URL is given), cached; None if it can't be had."""
    name = hashlib.sha1(file_name.encode()).hexdigest()[:16] + '.png'
    path = LOGOS / name
    if path.exists():
        return name
    url = url or 'https://commons.wikimedia.org/wiki/Special:FilePath/' + urllib.parse.quote(file_name.replace(' ', '_')) + '?width=160'
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


GENERIC = {'air', 'airline', 'airlines', 'airways', 'aviation', 'the', 'brand', 'airline brand', 'de', 'express', 'logo', 'svg', 'png'}
NOT_LOGOS = re.compile(r'commons-logo|wiki|star alliance|oneworld|skyteam|alliance logo|flag|question|ambox|disambig', re.I)
OLD = re.compile(r'\b(old|former|previous|historic|retro|until|pre-)\b', re.I)


def words(text):
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in GENERIC and len(w) > 1}


def article_logos(titles):
    """Title -> (file, thumbnail URL) for the logo each English Wikipedia article shows."""
    api = 'https://en.wikipedia.org/w/api.php?'
    choice = {}
    for start in range(0, len(titles), 50):
        batch = titles[start:start + 50]
        data = json.loads(polite_get(api + urllib.parse.urlencode({'action': 'query', 'prop': 'images', 'imlimit': 'max',
                                                                     'titles': '|'.join(batch), 'format': 'json', 'formatversion': 2})))
        aliases = {n['to']: n['from'] for n in data['query'].get('normalized', [])}
        for page in data['query']['pages']:
            title = aliases.get(page['title'], page['title'])
            name = words(re.sub(r'\(.*?\)', '', title))
            best = None
            for image in page.get('images', []):
                file = image['title']
                if 'logo' not in file.lower() or NOT_LOGOS.search(file) or OLD.search(file):
                    continue
                score = len(name & words(file))
                if score and (best is None or (score, -len(file)) > best[0]):
                    best = ((score, -len(file)), file)
            if best:
                choice[title] = best[1]
        time.sleep(0.5)
    files = sorted(set(choice.values()))
    urls = {}
    for start in range(0, len(files), 50):
        data = json.loads(polite_get(api + urllib.parse.urlencode({'action': 'query', 'prop': 'imageinfo', 'iiprop': 'url',
            'iiurlwidth': 160, 'titles': '|'.join(files[start:start + 50]), 'format': 'json', 'formatversion': 2})))
        for page in data['query']['pages']:
            info = (page.get('imageinfo') or [{}])[0]
            if info.get('thumburl'):
                urls[page['title']] = info['thumburl']
        time.sleep(0.5)
    return {t: (f, urls[f]) for t, f in choice.items() if f in urls}


def parents(titles):
    """Title -> parent airline's title (Wikidata: parent organization, else owned by)."""
    out = {}
    for start in range(0, len(titles), 120):
        values = ' '.join('"%s"@en' % t.replace('\\', '\\\\').replace('"', '\\"') for t in titles[start:start + 120])
        query = f'''SELECT ?title ?parentTitle WHERE {{
          VALUES ?title {{ {values} }}
          ?article schema:name ?title; schema:isPartOf <https://en.wikipedia.org/>; schema:about ?item.
          ?item wdt:P749|wdt:P127 ?parent.
          ?parentArticle schema:about ?parent; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?parentTitle.
        }}'''
        data = json.loads(polite_get(SPARQL + '?' + urllib.parse.urlencode({'query': query, 'format': 'json'})))
        for b in data['results']['bindings']:
            out.setdefault(b['title']['value'], b['parentTitle']['value'])
        time.sleep(1)
    return out


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
    sources_path = BUILD / 'airline_logo_sources.json'
    sources = json.loads(sources_path.read_text()) if sources_path.exists() else {}
    for title in files:
        if title in known:
            sources.setdefault(title, 'commons')
    print(f'{len(titles)} airlines; Wikidata has logos for {len(files)} more', flush=True)

    found = article_logos([t for t in titles if t not in known])
    for title, (file_name, url) in found.items():
        name = download(file_name, url)
        if name:
            known[title], sources[title] = name, 'wikipedia'
    print(f'{len(found)} more from the articles themselves', flush=True)

    family = parents([t for t in titles if t not in known])
    for title, parent in family.items():
        if parent in known:
            known[title], sources[title] = known[parent], 'parent:' + parent
    # regional brands flown for a mainline airline borrow its logo: American Eagle -> American Airlines
    for title in [t for t in titles if t not in known]:
        brand = re.match(r'^(.+?)\s+(Eagle|Connection|Express|Regional|Link|Shuttle|Commuter)\b', title)
        if brand:
            base = brand.group(1)
            mainline = sorted((t for t in known if t == base or t.startswith(base + ' ')), key=lambda t: (t != base, len(t)))
            if mainline:
                known[title], sources[title] = known[mainline[0]], 'parent:' + mainline[0]
    out_path.write_text(json.dumps(known, ensure_ascii=False, indent=0))
    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=0))
    print(f'{sum(1 for t in titles if t in known)} of {len(titles)} airlines have a logo')


if __name__ == '__main__':
    main()
