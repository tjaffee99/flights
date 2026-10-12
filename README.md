# Flight Atlas

Every passenger air route flying today on a globe, with each line's width set by how many people
fly the route in a year. Click an airport to see all its nonstop routes ranked by passengers, and
click a route for its numbers, airlines and source.

It's a static site (`index.html`, `app.js`, `style.css`, MapLibre GL in `vendor/`) that reads one
data file, `data/flights.json`, built by the scripts in `pipeline/`. The base map streams from
OpenFreeMap.

```sh
python3 -m http.server 8000      # from the repo root, then open http://localhost:8000
```

Links can point at an airport or a route: `#MIA`, `#MIA-CCS`.

## Which routes

**Routes flying today** come from Wikipedia's airport articles. Each has an "Airlines and
destinations" table, kept current by editors, with notes such as *begins 1 November 2026*,
*resumes …*, *ends …* and *suspended*. `pipeline/parse_wikipedia.py` reads every such table:

- *begins* a future date: not flying yet, so left out
- *suspended*, or *ended* on a past date: left out
- *resumes* a future date: left out unless the route is seasonal or the official counts show it
  flying this year
- *Dubai (via Muscat)*: not a nonstop route, so left out

Routes the official statistics show flying in their latest months are kept even if Wikipedia
doesn't list them. Routes they show stopping, which Wikipedia doesn't list either, are dropped.
So Miami–Caracas, which American restarted in April 2026, is on the map.

`pipeline/overrides.json` holds the hard rules that override every source: no flights touching
Ukraine, and none between Russia or Belarus and the US, Canada, the UK, the EU, Norway, Iceland or
Switzerland.

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
3. **Estimates** for everything else (`pipeline/build_routes.py`):
   - A regression fitted on the official counts predicts a route's passengers from what Wikipedia
     says about it: how big its two airports are, how many airlines fly it and which, the distance,
     whether it's domestic, and whether it's seasonal or charter-only.
   - Each airport's estimated routes are then scaled so that, with its counted routes, they add up
     to the airport's annual passenger total from its Wikipedia infobox. Every route has two
     airports, so this is iterative proportional fitting.

   To check the estimates, the build hides each official source in turn and estimates its routes
   from scratch. The typical (median) route comes out within a factor of about 1.6–1.9 of the
   real count. That's good enough to tell a 50K route from a 500K one, not 400K from 500K.

Counted routes cover about 58% of the 4.9 billion passengers a year on the map. The estimates
show with ≈ in the panel and as dashed lines when an airport is selected.

## Rebuilding the data

Needs Python 3 with numpy, pandas, requests, beautifulsoup4, lxml and python-dateutil. Downloads
are cached in `pipeline/cache/` and intermediate files go in `pipeline/build/` (neither is
committed).

```sh
cd pipeline
python3 build_measured.py              # official statistics (see MEASURED.md for options)
python3 fetch_wikipedia.py --refresh   # ~4,100 airport articles; takes about an hour
python3 parse_wikipedia.py             # current routes, airport totals, busiest-routes tables
python3 build_routes.py                # writes ../data/flights.json and prints the checks
```

## Sources and licenses

- Routes, airport totals and busiest-routes tables: [Wikipedia](https://en.wikipedia.org/), CC BY-SA 4.0
- Airport names and locations: [OurAirports](https://ourairports.com/data/), public domain
- US: [Bureau of Transportation Statistics](https://www.transtats.bts.gov/), public domain
- UK: [Civil Aviation Authority](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/airports/uk-airport-data/)
- Brazil: [ANAC](https://www.gov.br/anac/pt-br/assuntos/dados-e-estatisticas/dados-estatisticos)
- Australia: [BITRE](https://www.bitre.gov.au/statistics/aviation), via data.gov.au, CC BY 4.0
- Europe: [Eurostat](https://ec.europa.eu/eurostat), reuse with acknowledgement
- Base map: [OpenFreeMap](https://openfreemap.org/), © OpenMapTiles, © OpenStreetMap contributors
- [MapLibre GL JS](https://maplibre.org/) 5.24.0, BSD-3-Clause
