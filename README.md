# Flight Atlas

Every passenger air route flying today on a globe, with each line's width set by how many people
fly the route in a year.

- Click an airport to see all its nonstop routes ranked by passengers, the airlines that fly them,
  and (under *Flights away*) how many flights it takes to reach every other airport. Hover an
  airport to see the way there.
- Click a route for its passengers, flight time, distance, airlines and source.
- With an airport open, shift-click another for the way between them: the nonstop route if there
  is one, else the fewest flights there, stop by stop.
- Click an airline for its whole network (every other route goes away); pick an airport in it, or
  an airline at an airport, to see just that airline's routes from there.
- The overview lists every airline, airport and route, biggest first, loading more as you scroll.
- The options button beside the search bar turns the flight paths off, filters them, switches
  between a globe and a flat map, and lists the sources. The panel folds away for a full-screen map
  (on a phone it's a sheet you drag up and down), and zooming in shows the streets around each
  airport.

It's a static site (`index.html`, `app.js`, `style.css`, MapLibre GL in `vendor/`) that reads
`data/flights.json` (and `data/others.json` after the map appears), both built by the scripts in
`pipeline/`. The base map streams from
OpenFreeMap.

```sh
python3 -m http.server 8000      # from the repo root, then open http://localhost:8000
```

Links can point at an airport, a route (or the way between two airports), an airline, or an airline
at an airport: `#MIA`, `#MIA-CCS`, `#airline/Delta_Air_Lines`, `#airline/Delta_Air_Lines/ATL`.

## Which routes

**Routes flying today** come from Wikipedia's airport articles. Each has an "Airlines and
destinations" table, kept current by editors, with notes such as *begins 1 November 2026*,
*resumes …*, *ends …* and *suspended*. `pipeline/parse_wikipedia.py` reads every such table:

- *begins* a future date: not flying yet, so left out
- *suspended*, or *ended* on a past date: left out, even if the other airport's page still lists
  the airline as flying it (Frankfurt's page still had Lufthansa to Tehran; Tehran's has it
  suspended)
- *resumes* a future date: left out unless the route is seasonal or the official counts show it
  flying this year
- *Dubai (via Muscat)*: not a nonstop route, so left out
- tables of routes as they were ("International service before 1994", "Previously served
  airlines") are skipped, wherever they sit in the article
- an airline whose own Wikipedia article says it has ceased operations (Royal Air Philippines,
  flybig, Fly540…) is taken off every route, however many airport pages still list it
  (`pipeline/fetch_airline_status.py`)
- a destination is matched to its IATA code through its article. Wikidata now and then gives a
  small field another airport's code (an Indonesian airstrip with La Crosse's LSE), so a code is
  only taken when OurAirports agrees on the airport
- listed only as *Charter*: left out unless the official counts show it flying regularly. That
  drops contract, mining and military charters (Wake Island–Travis Air Force Base) and keeps holiday
  charters the statistics count

Routes the official statistics show flying in their latest months are kept even if Wikipedia
doesn't list them, as long as they look like a schedule: about weekly or more (or 5,000+ passengers a
year where a source doesn't count flights). That leaves out charters, diversions and repositioning
flights, like the three Delta flights a year between Tallahassee and Los Angeles. Routes the
statistics show stopping, which Wikipedia doesn't list either, are dropped.
So Miami–Caracas, which American restarted in April 2026, is on the map.

`pipeline/overrides.json` holds the hard rules that override every source, each with its reason:
no flights touching Ukraine; none between Russia or Belarus and the US, Canada, the UK, the EU,
Norway, Iceland or Switzerland; none between Israel and Turkey; Air France's suspended Ouagadougou
and Niamey flights; and airports replaced by new ones (Luanda's international flights, Phnom Penh,
the old Doha airport), so that listings naming the old one count for the new.

## How many passengers

Each route's count comes from the best source that has it:

1. **Official statistics** for the latest 12 months published (`pipeline/MEASURED.md`). A route
   that started partway through is scaled up to a year.

   | Source | Covers | Period |
   | --- | --- | --- |
   | US DOT, BTS T-100 Segment | every flight touching the US, US and foreign airlines | Aug 2025 – Jul 2026 |
   | UK CAA | every route from a UK airport | Aug 2025 – Jul 2026 |
   | ANAC | every scheduled flight touching Brazil | Sep 2025 – Aug 2026 |
   | Eurostat `avia_par_*` | routes of the main airports of the EU, EFTA and candidate countries | 2024 (the latest it has published) |
   | BITRE | Australia's international routes and busiest domestic routes | mid-2025 – mid-2026 |

   Eurostat stops at the end of 2024, so a route that only Eurostat counts must also be listed on
   Wikipedia today.
2. **Airports' published statistics**, from the "busiest routes" tables on many Wikipedia airport
   pages (Japan, Korea, India, Southeast Asia, Mexico…), for 2019 or 2023 onwards. Where official
   counts exist for the same routes, the tables are checked against them country by country. US
   and Mexican tables turn out to count one direction only, so their figures are doubled. Swiss
   tables disagree, so they are not used.

   Then Wikipedia's [List of busiest passenger air routes](https://en.wikipedia.org/wiki/List_of_busiest_passenger_air_routes),
   for the big routes no airport table covers (Beijing–Shanghai, Delhi–Mumbai, Hanoi–Ho Chi Minh
   City, Jeddah–Riyadh). Its seat counts become passengers at the load factor its officially
   counted routes show, or 0.8.
3. **Estimates** for everything else (`pipeline/build_routes.py`):
   - A regression fitted on the official counts predicts a route's passengers from what Wikipedia
     says about it: how big its two airports are, how many airlines fly it and which, the distance,
     whether it's domestic, and whether it's seasonal or charter-only.
   - The regression only predicts within the range it was trained on, so a route listing far
     more airlines than any it learned from (Iranian domestic routes list 20) isn't taken for a
     giant.
   - Each airport's estimated routes are then scaled so that, with its counted routes, they add up
     to the airport's annual passenger total from its Wikipedia infobox. Every route has two
     airports, so this is iterative proportional fitting. Totals from 2010–2019 or 2023 on are
     used (not the pandemic years); a part-year figure ("September to December 2025", for an
     airport that opened then) is scaled up to a year, and Indian figures written in lakhs and
     crores ("3,04,83,885") are read as such.
   - Eurostat withholds some routes as confidential (all of Norway's domestic ones). At the
     airports where it does, and the counted routes fall well short of the total, the estimated
     routes make up the rest.

   To check the estimates, the build hides each official source in turn and estimates its routes
   from scratch. The typical (median) route comes out within a factor of about 1.6–1.7 of the
   real count. That's good enough to tell a 50K route from a 500K one, not 400K from 500K.

Counted routes cover about 59% of the 5 billion passengers a year on the map. Estimates are
marked with ≈ in the panel.

**Airlines** are the ones each airport's Wikipedia table lists for a route, identified by the
airline's Wikipedia article (so *Air Algérie* and *Air Algerie* are one airline). Tables list the
brand that sells the flight (*American Eagle*, *Delta Connection*), not the regional airline that
operates it. Logos (`pipeline/fetch_logos.py`) come from Wikidata's logo property on Wikimedia
Commons; failing that, from the logo in the infobox of the airline's English Wikipedia article
(often one of Wikipedia's own non-free files, used there and here to identify the airline); failing that, for a
brand like American Eagle, from its parent airline. Airlines without one get their initials.

**Flights away** counts the fewest flights from an airport to every other. A dozen small networks no
flight joins to the rest (the Aran Islands, Ketchikan's floatplanes, Shetland's inter-island
flights) are linked to the nearest airport by a road or ferry trip that counts as free, so every
airport can be reached. Of the ways with the fewest flights, the one shown is the shortest, with
thinly flown routes counted a little longer so that it connects where people actually connect.

**Flight times** are typical block times for the distance, `45 min + 0.0703 min/km`, fitted to
schedules from short hops to the longest flights; they're not from timetables.

Airports with a code but no airline flights (general aviation fields like Boca Raton, air bases,
closed or unfinished airports) are in `data/others.json` and show as grey italic codes when you
zoom in.

## Rebuilding the data

Needs Python 3 with numpy, pandas, requests, beautifulsoup4, lxml and python-dateutil. Downloads
are cached in `pipeline/cache/` and intermediate files go in `pipeline/build/` (neither is
committed).

```sh
cd pipeline
python3 build_measured.py              # official statistics (see MEASURED.md for options)
python3 fetch_wikipedia.py --refresh   # ~4,100 airport articles; takes about an hour
python3 parse_wikipedia.py             # current routes, airport totals, busiest-routes tables
python3 fetch_airline_status.py        # which airlines have stopped flying, from their articles
python3 fetch_logos.py                 # airline logos from Wikidata / Wikimedia Commons
python3 build_routes.py                # writes ../data/flights.json and others.json, prints the checks
```

## Sources and licenses

- Routes, airport totals, busiest-routes tables, the list of the busiest routes and which airlines still fly: [Wikipedia](https://en.wikipedia.org/), CC BY-SA 4.0
- Airport names and locations: [OurAirports](https://ourairports.com/data/), public domain
- US: [Bureau of Transportation Statistics](https://www.transtats.bts.gov/), public domain
- UK: [Civil Aviation Authority](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/airports/uk-airport-data/)
- Brazil: [ANAC](https://www.gov.br/anac/pt-br/assuntos/dados-e-estatisticas/dados-estatisticos)
- Australia: [BITRE](https://www.bitre.gov.au/statistics/aviation), via data.gov.au, CC BY 4.0
- Europe: [Eurostat](https://ec.europa.eu/eurostat), reuse with acknowledgement
- Airline logos: [Wikimedia Commons](https://commons.wikimedia.org/) via [Wikidata](https://www.wikidata.org/); trademarks of their owners
- Base map: [OpenFreeMap](https://openfreemap.org/), © OpenMapTiles, © OpenStreetMap contributors
- [MapLibre GL JS](https://maplibre.org/) 5.24.0, BSD-3-Clause
