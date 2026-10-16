"""Whether each airline on a current route still flies, from its English Wikipedia article.

Airport pages lag: an airline that has shut down can stay in their tables for months. For every
airline linked from a current listing, this reads its article's infobox ("Ceased operations") and
categories, and writes build/airline_status.json: title -> {"ceased": text or null, "defunct": bool}.
An airline is defunct when its infobox gives a past date for ceasing operations, or when it is in a
"Defunct airlines" category and its article speaks of it in the past tense ("Fly540 was a low-cost
airline..."). Re-running only fetches articles not in cache/airline_status.json; --refresh fetches them all again.

    python3 fetch_airline_status.py
"""

import argparse
import csv
import datetime as dt
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from fetch_wikipedia import CACHE, polite_get
from parse_wikipedia import BUILD


def facts(title):
    html = polite_get('https://en.wikipedia.org/wiki/' + urllib.parse.quote(title.replace(' ', '_'), safe="()_,'!-."))
    if not html:
        return None
    soup = BeautifulSoup(html, 'lxml')
    info = {}
    box = soup.select_one('table.infobox')
    for tr in box.find_all('tr') if box else []:
        th, td = tr.find('th'), tr.find('td')
        if th and td:
            info[th.get_text(' ', strip=True).lower()] = td.get_text(' ', strip=True)[:200]
    lead = soup.select_one('#mw-content-text p:not(.mw-empty-elt)')
    return {'ceased': next((v for k, v in info.items() if 'ceased' in k), None),
            'categories': [a.get_text() for a in soup.select('#mw-normal-catlinks a') if 'defunct' in a.get_text().lower()],
            'lead': lead.get_text(' ', strip=True)[:300] if lead else ''}


def defunct(f, today):
    if not f:
        return False
    if f['ceased']:
        years = [int(y) for y in re.findall(r'\b(?:19|20)\d\d\b', f['ceased'])]
        if years and min(years) <= today.year:
            return True
    past = re.search(r"\b(was|were) an? [^.]{0,80}\bairlines?\b", f['lead'], re.I)
    return bool(f['categories'] and past)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh', action='store_true', help='refetch articles already cached')
    args = parser.parse_args()
    today = dt.date.today()
    cache_path = CACHE / 'airline_status.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() and not args.refresh else {}
    with open(BUILD / 'wiki_routes.csv', newline='') as f:
        titles = sorted({r['airline_title'] for r in csv.DictReader(f) if r['status'] == 'current' and r['airline_title']
                         and 'redlink=' not in r['airline_title'] and not r['airline_title'].startswith('List of')})
    todo = [t for t in titles if t not in cache]
    with ThreadPoolExecutor(4) as pool:
        for title, f in zip(todo, pool.map(facts, todo)):
            cache[title] = f
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    status = {t: {'ceased': (cache.get(t) or {}).get('ceased'), 'defunct': defunct(cache.get(t), today)} for t in titles}
    (BUILD / 'airline_status.json').write_text(json.dumps(status, ensure_ascii=False, indent=0))
    gone = sorted(t for t, s in status.items() if s['defunct'])
    print(f'{len(titles)} airlines, {len(gone)} no longer flying: {", ".join(gone)}')


if __name__ == '__main__':
    main()
