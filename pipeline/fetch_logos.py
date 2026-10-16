"""Airline logos, for every airline listed on a current route (build/wiki_routes.csv, by its
English Wikipedia article), from the first of:

  1. Wikidata's logo image (P154, else its small logo or icon, P8972), a file on Wikimedia Commons;
  2. the logo file the airline's English Wikipedia article shows: its infobox's `logo`, else the
     image whose name marks it as the logo (skipping old and former logos).
     Many of these are English Wikipedia's own "non-free" files, which it uses to identify the
     airline; Flight Atlas uses them the same way;
  3. for a brand without a logo of its own (American Eagle), its parent airline's logo.

Each is downloaded as a 160-pixel-wide PNG into cache/logos/. Writes build/airline_logos.json
(article title -> file) and build/airline_logo_sources.json (title -> where it came from).
Re-running only fetches what's missing; --refresh chooses every airline's logo again (downloads
stay cached). The logos remain their owners' trademarks.

    python3 fetch_logos.py
    python3 fetch_logos.py --refresh
"""

import argparse
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

from bs4 import BeautifulSoup

from fetch_wikipedia import CACHE, UA, polite_get

HERE = Path(__file__).resolve().parent
BUILD = HERE / 'build'
LOGOS = CACHE / 'logos'
SPARQL = 'https://query.wikidata.org/sparql'


def airline_titles():
    counts = Counter()
    with open(BUILD / 'wiki_routes.csv', newline='') as f:
        for row in csv.DictReader(f):
            title = row['airline_title']
            if row['status'] == 'current' and title and 'redlink=' not in title and not title.startswith('List of'):
                counts[title] += 1
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


def fetch_image(url):
    """(body, content type) or None."""
    try:
        request = urllib.request.Request(url, headers={'User-Agent': UA})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return response.read(), response.headers.get('Content-Type', '')
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504):
                    time.sleep(2 * (attempt + 1))
                    continue
                return None
    except Exception:
        return None
    return None


def download(file_name, url=None):
    """A 160px-wide PNG of the file (Commons unless a URL is given), cached; None if it can't be had.
    Wikimedia turns bots away from some thumbnail sizes, so a page's own thumbnail URL that fails is
    tried again through Special:FilePath, which serves the standard sizes."""
    name = hashlib.sha1(file_name.encode()).hexdigest()[:16] + '.png'
    path = LOGOS / name
    if path.exists():
        return name
    plain = urllib.parse.quote(re.sub(r'^File:', '', file_name).replace(' ', '_'))
    urls = [url, f'https://en.wikipedia.org/wiki/Special:FilePath/{plain}?width=160'] if url else \
        [f'https://commons.wikimedia.org/wiki/Special:FilePath/{plain}?width=160']
    for u in urls:
        got = fetch_image(u)
        if got and got[1].startswith('image/') and len(got[0]) <= 200_000:
            path.write_bytes(got[0])
            time.sleep(0.3)
            return name
    return None


def transparent(name):
    """Whether a downloaded image has see-through pixels: logos usually do, photos and maps don't."""
    from PIL import Image
    try:
        with Image.open(LOGOS / name) as image:
            return image.convert('RGBA').getchannel('A').getextrema()[0] < 255
    except Exception:
        return False


GENERIC = {'air', 'airline', 'airlines', 'airways', 'aviation', 'the', 'brand', 'airline brand', 'de', 'express', 'logo', 'svg', 'png'}
NOT_LOGOS = re.compile(r'commons-logo|wiki|star alliance|oneworld|skyteam|alliance logo|flag|question|ambox|disambig', re.I)
OLD = re.compile(r'\b(old|former|previous|historic|retro|until|pre-)\b', re.I)


def words(text):
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in GENERIC and len(w) > 1}


AIRLINE_BOX = re.compile(r'\b(IATA|ICAO|Callsign|Hubs?|Fleet size|Destinations)\b|Industry\s+(Aviation|Aerospace|Airlines?|Air transport)')


def page_images(title):
    """(infobox images, all images, whether the infobox is an airline's: None where there's no
    infobox) on the airline's English Wikipedia article, images as (file, thumbnail URL) in page
    order. Read from the article itself: the API turns busy callers away."""
    html = polite_get('https://en.wikipedia.org/wiki/' + urllib.parse.quote(title.replace(' ', '_'), safe="()_,'!-."))
    if not html:
        return [], [], False
    soup = BeautifulSoup(html, 'lxml')
    def images(root):
        out = []
        for img in root.select('img') if root else []:
            link = img.find_parent('a', href=True)
            if not link or '/wiki/File:' not in link['href']:
                continue
            file = 'File:' + urllib.parse.unquote(link['href'].split('/wiki/File:', 1)[1]).replace('_', ' ')
            best = img.get('src', '')
            for part in (img.get('srcset') or '').split(','):
                if part.strip().endswith('2x'):
                    best = part.strip().split()[0]
            out.append((file, 'https:' + best if best.startswith('//') else best))
        return out
    box = soup.select_one('table.infobox')
    return images(box), images(soup.select_one('#mw-content-text')), bool(AIRLINE_BOX.search(box.get_text(' '))) if box else None


def compact(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def named_for(title, file):
    """Whether a file's name says it's a logo, or names the airline."""
    plain = re.sub(r'\(.*?\)', '', title)
    stem = re.sub(r'^File:|\.\w+$', '', file)
    return 'logo' in stem.lower() or bool(words(plain) & words(stem)) or compact(plain) in compact(stem)


def guess_logo(title, images):
    """The likeliest logo among an article's images: named 'logo' and sharing words with the airline,
    or simply named after it ('Lion Air.svg')."""
    plain = re.sub(r'\(.*?\)', '', title)
    name, joined = words(plain), compact(plain)
    best = None
    for file in images:
        if NOT_LOGOS.search(file) or OLD.search(file):
            continue
        stem = re.sub(r'^File:|\.\w+$', '', file)
        if 'logo' in file.lower():
            score = len(name & words(stem)) or (2 if joined and joined in compact(stem) else 0)
        else:
            score = 1 if compact(stem) == joined and file.lower().endswith(('.svg', '.png')) else 0
        if score and (best is None or (score, -len(file)) > best[0]):
            best = ((score, -len(file)), file)
    return best and best[1]


def article_logos(titles):
    """Title -> (file, thumbnail URL, check) for the logo each English Wikipedia article shows: the
    first drawing (not a photo) in its infobox, else a guess among the article's images, else the
    infobox's first PNG, if it turns out to be see-through (check=True)."""
    found = {}
    for k, title in enumerate(titles):
        infobox, everything, airline = page_images(title)
        if airline is False:   # a link to the wrong article (an airport's table linking "Firefly", the insect)
            continue
        usable = [(f, u) for f, u in infobox if not re.search(r'\.jpe?g$', f, re.I) and not NOT_LOGOS.search(f) and not OLD.search(f)]
        drawings = [(f, u) for f, u in usable if f.lower().endswith('.svg') or named_for(title, f)]   # a PNG may be a photo or a map
        urls = dict(everything)
        guess = None if drawings else guess_logo(title, list(urls))
        if drawings:
            found[title] = (*drawings[0], False)
        elif guess:
            found[title] = (guess, urls[guess], False)
        elif usable and airline:   # "AD tagline 23 final.png" for Aero Dili
            found[title] = (*usable[0], True)
        if k % 50 == 49:
            print(f'{k + 1}/{len(titles)} articles', flush=True)
    return found


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
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true', help="choose every airline's logo again")
    args = parser.parse_args()
    LOGOS.mkdir(parents=True, exist_ok=True)
    out_path = BUILD / 'airline_logos.json'
    known = json.loads(out_path.read_text()) if out_path.exists() and not args.refresh else {}
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
    sources = json.loads(sources_path.read_text()) if sources_path.exists() and not args.refresh else {}
    for title in files:
        if title in known:
            sources.setdefault(title, 'commons')
    print(f'{len(titles)} airlines; Wikidata has logos for {len(files)} more', flush=True)

    found = article_logos([t for t in titles if t not in known])
    for title, (file_name, url, check) in found.items():
        name = download(file_name, url)
        if name and (not check or transparent(name)):
            known[title], sources[title] = name, 'wikipedia'
    print(f'{sum(1 for t in found if t in known)} more from the articles themselves', flush=True)

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
