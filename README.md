# Flight Atlas

Every passenger air route flying today on a globe, with each line's width set by how many people
fly the route in a year.

- Click an airport to see all its nonstop routes ranked by passengers, the airlines that fly them,
  and (under *Flights away*) how many flights it takes to reach every other airport. Hover an
  airport to see the way there.
- Click a route for its passengers, how often it flies (flights a day, seats a flight, how full),
  its flight time each way, the distance flown, its airlines and sources. Where some of its airlines
  go around closed airspace (Russia, Ukraine, Iran…), the panel lists each way with its distance
  and time, and with *Around closed airspace* on (off at first) the map draws the way most of them
  fly instead of the great circle.
- With an airport open, shift-click another for the way between them: the nonstop route if there
  is one, else every way with the fewest flights (via Doha, via Istanbul…), best first, with their
  times and how often their flights go.
- Search a city with several airports (London, New York, Tokyo) for all its airports together:
  their destinations, by city where those have several airports too, and every route between two
  cities (London–New York is nine).
- Click an airline for its whole network (every other route goes away); pick an airport in it, or
  an airline at an airport, to see just that airline's routes from there.
- The overview lists every airline, airport and route, biggest first, loading more as you scroll.
- The options button beside the search bar turns the flight paths off, filters them, switches
  between a globe and a flat map, says what the numbers rest on (*About the numbers*), and
  lists the sources. The panel folds away for a full-screen map
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
the old Doha airport), so that listings naming the old one count for the new. More lists catch
Wikipedia tables that outlived the flights in them:

- closed airports: Russia's airports near Ukraine, shut since February 2022 and not reopened
  (Belgorod, Voronezh, Kursk, Rostov-on-Don, Anapa, Bryansk, Lipetsk, Simferopol), though some
  pages still list flights to them (Bovanenkovo's lists Yamal Airlines to Belgorod; Voronezh's own
  page lists eighteen routes); Port-au-Prince, without scheduled flights since November 2025;
  Sana'a; Goma and Bukavu, held by M23 since 2025; Thandwe and Bhamo in Myanmar, held by rebels
- airports reopened only in part: Khartoum, closed by the war in 2023 and reopened in 2026 for a
  few flights, keeps only the routes and airlines reported flying (Port Sudan, Cairo, Istanbul on
  Sudanese airlines), and its 2017 passenger total isn't used to scale the estimates
- suspended: airlines that stopped flying somewhere their routes are still listed, each rule with
  its source. Most follow this year's wars: foreign airlines out of Iran and Iranian ones barred
  from the UAE, Oman, Turkey, Azerbaijan and Georgia after the US sanctions of September 2026;
  Israeli airlines out of the UAE; Western airlines that dropped Tel Aviv, Amman, Beirut and some
  Gulf routes. Others are single airlines: Congo Airways (grounded), Turkish at Juba, Qatar Airways
  and Kenya Airways at Mogadishu, Conviasa's dropped routes. Only suspensions lasting past the
  next few weeks are listed, and one with a return date carries it (`until`), so it lapses by
  itself and the route comes back in the next monthly rebuild
- unlisted: the few airlines flying a route that Wikipedia doesn't list, where the listed ones
  left (Iran Air on Tehran–Istanbul, after Turkey barred Mahan Air)

## How many passengers

Each route's count comes from the best source that has it:

1. **Official statistics** for the latest 12 months published (`pipeline/MEASURED.md`). A route
   that started partway through is scaled up to a year.

   | Source | Covers | Period |
   | --- | --- | --- |
   | US DOT, BTS T-100 Segment | every flight touching the US, US and foreign airlines | Aug 2025 – Jul 2026 |
   | UK CAA | every route from a UK airport | Sep 2025 – Aug 2026 |
   | ANAC | every scheduled flight touching Brazil | Sep 2025 – Aug 2026 |
   | Eurostat `avia_par_*` | routes of the main airports of the EU, EFTA and candidate countries | 2024 (the latest it has published) |
   | BITRE | Australia's international routes and busiest domestic routes | mid-2025 – mid-2026 |
   | DGCA India, via [india-aviation-traffic](https://github.com/Vonter/india-aviation-traffic) | every scheduled domestic route in India, and India's international routes, Indian and foreign airlines | Aug 2025 – Jul 2026 (international: Jul 2025 – Jun 2026) |

   Eurostat stops at the end of 2024, so a route that only Eurostat counts must also be listed on
   Wikipedia today.
2. **Airports' published statistics**, from the "busiest routes" tables on many Wikipedia airport
   pages (Japan, Korea, Southeast Asia, Mexico…), for 2019 or 2023 onwards. Where official
   counts exist for the same routes, the tables are checked against them country by country. US
   and Mexican tables turn out to count one direction only, so their figures are doubled. Swiss
   tables disagree, so they are not used.

   Then Wikipedia's [List of busiest passenger air routes](https://en.wikipedia.org/wiki/List_of_busiest_passenger_air_routes),
   for the big routes no airport table covers (Beijing–Shanghai, Hanoi–Ho Chi Minh City,
   Jeddah–Riyadh). Its seat counts become passengers at the load factor its officially
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
   real count (1.9 in India), and 80% within a factor of 2.8. That's good enough to tell a 50K
   route from a 500K one, not 400K from 500K. The site shows these figures under *About the
   numbers*, and the build writes them to `data/flights.json` each time.

Counted routes cover about 62% of the 5 billion passengers a year on the map. Estimates are
marked with ≈ in the panel. They're least sure where little is published: China (almost all
estimated), Russia, the Gulf, Southeast Asia, Canada's domestic routes (Statistics Canada no
longer publishes them), and countries at war or under sanctions. Those countries' flights are
tracked like anyone's, but flight trackers count flights, not passengers, and the full
timetables (OAG, Cirium) are sold rather than published.

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

**Flights a day, seats and how full** come from the official counts that have flights and seats
(US T-100, Eurostat, ANAC, BITRE's domestic routes), from the highest-ranked of them that counts the
route, even when its passengers come from elsewhere: the UK CAA counts only passengers, so
London–Paris gets its flights from Eurostat. They're each way, averaged over the months the route
flies. Routes only Wikipedia or the estimates know about have none.

**Flight times** are the US DOT's averages, gate to gate, for every route T-100 times (US
airlines report their times; foreign ones don't). Everywhere else they're fitted to those averages
from the distance flown and how far the route runs with or against the jet streams (eastward
kilometres, weighted towards the 30–50° latitudes where the westerlies blow hardest): 46 minutes,
plus 71.6 a thousand km, less 7.2 a thousand km run east through the jet streams. On the US
routes it was fitted to, it's within 6 minutes for the typical route and 18 minutes for nine in ten.

**Closed airspace.** `pipeline/airspace.py` draws the countries airlines avoid onto a half-degree
grid, finds the shortest way around them, and straightens it into a few great-circle legs. Which
airlines avoid what is in `overrides.json` (`airspace`), as of October 2026:

- Ukraine: every airline (closed since 2022)
- Russia: airlines of the EU, the UK, the US, Canada, Switzerland and others Russia bars, and
  Japanese airlines, which have kept out since 2022
- Belarus: the same Western airlines
- Pakistan for Indian airlines, and India for Pakistani ones (monthly notices since April 2025)
- Iran: every airline but Iran's own. It's partly open on paper, but almost every foreign airline
  has kept out since the strikes of February 2026
- Iraq, Lebanon, Sudan and Yemen (over land): Western airlines, following EASA's conflict zone
  bulletins, which advise against them at every altitude. Afghanistan, Syria, Libya, Mali and
  Somalia aren't avoided: their warnings now cover only flights below FL260–FL320, and airliners
  cross them above it (Europe–Asia traffic over Afghanistan has grown since the Middle East closed)
- North Korea: every airline
- The Tibetan Plateau, Karakoram and Pamirs: every airline (with no lower ground to descend to,
  airliners keep off them)

An airline's country is the one most of its routes touch, and a route never avoids a country it
flies to or from. Partial closures (parts of Saudi Arabia, the waters of the Gulf) aren't modelled.
Of 33,000 routes, about 2,100 are flown around something by some of their airlines. The rules
need checking when the news changes: the monthly rebuild doesn't.

**Cities.** Airports are grouped by the first place their Wikipedia infobox says they serve (all six
of London's link to *London*), keeping those within 85 km of the group's busiest airport, in the
same country, with 100,000 passengers or more. `overrides.json` (`cities`) adds groups the links
miss (Tokyo's airports link to *Tokyo* and *Greater Tokyo Area*) and renames a few (*Keihanshin*
is Osaka). There are 70.

Airports with a code but no airline flights (general aviation fields like Boca Raton, air bases,
closed or unfinished airports) are in `data/others.json` and show as grey italic codes when you
zoom in.

## Rebuilding the data

Needs Python 3 with numpy, scipy, pandas, requests, beautifulsoup4, lxml, python-dateutil and Pillow. Downloads
are cached in `pipeline/cache/` and intermediate files go in `pipeline/build/` (neither is
committed).

```sh
cd pipeline
python3 fetch_reference.py             # OurAirports and Wikidata's airport codes
python3 fetch_wikipedia.py --refresh   # ~4,100 airport articles; takes 20 minutes to an hour
python3 parse_wikipedia.py             # current routes, airport totals, busiest-routes tables
python3 build_measured.py              # official statistics (see MEASURED.md); needs parse_wikipedia's routes
python3 fetch_airline_status.py        # which airlines have stopped flying, from their articles
python3 fetch_logos.py                 # airline logos from Wikidata / Wikimedia Commons (--refresh to choose again)
python3 build_routes.py                # writes ../data/flights.json and others.json (with airspace.py: ways around closed airspace)
python3 checks.py                      # compares with the published build; see below
```

`checks.py` compares the new `data/flights.json` with the last committed one and writes
`build/checks.txt`. It fails (exit status 1) if the passengers, routes or airports move by more
than a few percent, if the world total leaves 4–6 billion, if an official source's routes
shrink by more than 30% (a source that fails to download quietly hands its routes to the
estimates), or if a route of over a million passengers appears that wasn't there before (nearly
always a mapping slip: the first scheduled run took DGCA's "London" for Luton). It also lists
things worth a look: busy routes that vanished or doubled, big new routes, airports far from their
reported totals, big estimates resting on one airline, and the airlines left out as no longer
flying.

`.github/workflows/rebuild.yml` runs all of this on the 3rd of every month and commits `data/`
only if the checks pass. When they fail, nothing is published, `checks.txt` is attached to the
run, and GitHub emails the repository owner. After reading it, re-run the workflow by hand with
*force* ticked to publish anyway.

## Sources and licenses

- Routes, airport totals, busiest-routes tables, the list of the busiest routes and which airlines still fly: [Wikipedia](https://en.wikipedia.org/), CC BY-SA 4.0
- Airport names and locations: [OurAirports](https://ourairports.com/data/), public domain; airport codes and articles: [Wikidata](https://www.wikidata.org/), CC0
- US: [Bureau of Transportation Statistics](https://www.transtats.bts.gov/), public domain
- UK: [Civil Aviation Authority](https://www.caa.co.uk/data-and-analysis/uk-aviation-market/airports/uk-airport-data/)
- Brazil: [ANAC](https://www.gov.br/anac/pt-br/assuntos/dados-e-estatisticas/dados-estatisticos)
- Australia: [BITRE](https://www.bitre.gov.au/statistics/aviation), via data.gov.au, CC BY 4.0
- India: [DGCA](https://www.dgca.gov.in/), via [india-aviation-traffic](https://github.com/Vonter/india-aviation-traffic), [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). The India figures in `data/flights.json` are offered under the same license.
- Europe: [Eurostat](https://ec.europa.eu/eurostat), reuse with acknowledgement
- Airline logos: [Wikimedia Commons](https://commons.wikimedia.org/) via [Wikidata](https://www.wikidata.org/); trademarks of their owners
- Country borders for closed airspace: [Natural Earth](https://www.naturalearthdata.com/), public domain; which airspace is avoided: [EASA conflict zone bulletins](https://www.easa.europa.eu/en/domains/air-operations/czibs) and the notices named in `pipeline/overrides.json`
- Base map: [OpenFreeMap](https://openfreemap.org/), © OpenMapTiles, © OpenStreetMap contributors
- [MapLibre GL JS](https://maplibre.org/) 5.24.0, BSD-3-Clause
