# Measured route passengers (official statistics)

Builds `build/measured.csv` and `build/measured_sources.json` from government
statistics: passengers per airport pair, latest 12 months each source publishes.

```sh
python3 -I pipeline/build_measured.py              # all sources, reuse cache
python3 -I pipeline/build_measured.py --refresh    # re-download everything
python3 -I pipeline/build_measured.py --sources us,uk   # rebuild a subset; other
                                                                # sources' rows are kept
python3 -I pipeline/check_measured.py              # sanity checks
```

Source keys: `us` BTS T-100, `eu` Eurostat, `uk` CAA, `br` ANAC, `au` BITRE, `in` DGCA India.
Downloads are cached in `cache/<source>/`. BTS and CAA work out the latest month
on every run and fetch only months they don't have. Eurostat cache files are keyed
by the dataset's last-update date. ANAC is one 360 MB file and the BITRE CSVs are
keyed by their data.gov.au date, so use `--refresh` to pick up new ANAC months. The DGCA
CSVs are fetched again once they're a week old.

## Output columns

`source,reporter,origin_iata,dest_iata,passengers,seats,departures,months_with_traffic,first_month,last_month,period_start,period_end,carriers,directional,block_minutes,air_minutes,timed_departures`

- `directional=1`: origin→dest only. `directional=0`: both directions combined, with
  origin < dest alphabetically (`uk_caa`, `au_bitre_dom`).
- `reporter`: `US`, `GB`, `BR`, `AU`, or for Eurostat `<country>:<reporting airport>`
  (e.g. `DE:FRA`). A pair between two Eurostat reporting airports appears **once per
  reporting airport** (FRA→MAD from `DE:FRA` and from `ES:MAD`). This includes
  domestic pairs. Deduplicate downstream.
- `seats`/`departures` are empty where the source lacks them (CAA, BITRE intl). For
  `au_bitre_dom` they are two-way totals.
- `months_with_traffic`/`first_month`/`last_month` are empty for Eurostat rows filled
  from annual values (see below).
- `block_minutes`/`air_minutes`: T-100's RAMP_TO_RAMP and AIR_TIME, minutes summed over
  `timed_departures`. Only US airlines report them (foreign airlines' rows have zeros and are
  left out), so divide by `timed_departures`, not `departures`. Empty for other sources.
- `carriers`: pipe-separated, largest first. T-100 uses IATA-style codes and ANAC uses
  ICAO 3-letter codes. The other sources have no carrier data.
- Airports are IATA codes as in OurAirports (`cache/airports.csv`). Note that BTS `PBI`
  becomes `DJT`, because OurAirports renamed the airport.

## Sources and caveats

| source | what | window (as of 2026-10-16) |
|---|---|---|
| `us_bts_t100` | T-100 Segment, all carriers, CLASS F, pax>0 (TranStats form POST, per month) | 2025-08..2026-07 |
| `eu_eurostat_avia_par` | `avia_par_<cc>` SDMX-CSV, **PAS_BRD_DEP/ARR** (+ ST_PAS, CAF_PAS) | 2024-01..2024-12 (BA: 2023) |
| `uk_caa` | CAA monthly Tables 12.1 (intl) + 12.2 (domestic), names mapped via `uk_caa_airport_names.csv` | 2025-09..2026-08 |
| `br_anac` | ANAC Dados Estatísticos, flight stages, GRUPO_DE_VOO=REGULAR, stages touching Brazil | 2025-09..2026-08 |
| `au_bitre_intl` | BITRE intl city pairs (data.gov.au mirror), Passengers_In/Out | 2025-07..2026-06 |
| `au_bitre_dom` | BITRE top ~70 domestic city pairs (data.gov.au mirror) | 2025-08..2026-07 |
| `in_dgca_dom` | DGCA monthly domestic city pairs, each way (via the india-aviation-traffic CSVs) | 2025-08..2026-07 |
| `in_dgca_intl` | DGCA quarterly international city pairs (Table 4), each way, Indian and foreign airlines | 2025-07..2026-06 |

- **Eurostat measure**: PAS_CRD/PAS_BRD are both-ways totals, and the `_DEP`/`_ARR`
  variants give the split by direction. We use PAS_BRD (passengers on board per flight
  stage), which works like T-100 Segment. PAS_CRD counts by on-flight O/D, so it
  produces pairs that have no nonstop flight, e.g. CDG–Papeete via LAX. The two
  totals differ by about 0.3%. Set `MEASURE` in `measured_eurostat.py` to switch.
- Eurostat route tables come out once a year, about 10 months after the year ends, so
  2025 data is not out yet. There is no `avia_par_cz/al/xk`, and `avia_par_uk` stops in
  2019, so it is skipped.
- Eurostat confidentiality: France has 14 monthly series marked confidential. 10 of
  them are filled from annual 2024 values (e.g. ORY–NCE, ORY–TLS). **All 96 Norwegian
  domestic routes are confidential at every frequency** (OSL–TRD, OSL–BGO, …), so they
  are missing. SSB table 08511 has seats and movements between Norwegian airports, but
  no passengers.
- Eurostat unmapped: `ZZZZ` (unknown partner), `xx99` ("other airports" aggregates),
  `EDBB`, `ETLS`.
- CAA: route totals for both directions, and the international table counts UK
  airport ↔ foreign airport. "OIL RIGS" (helicopter traffic) is dropped. New airport
  names are auto-matched against OurAirports and listed under `auto_matched` in
  `measured_sources.json`. Review them and add them to `uk_caa_airport_names.csv`.
- BITRE: bitre.gov.au refuses connections from this environment, so the data comes
  from the data.gov.au copies. The data is per city, and each city is mapped to one
  airport (Tokyo→NRT except Sydney→HND, London→LHR, …). International pairs are
  "uplift/discharge within one flight number", so through flights add pairs like
  SYD–LHR (QF1 via SIN).
- DGCA India: DGCA publishes its tables as PDFs and through a JavaScript portal. The
  [india-aviation-traffic](https://github.com/Vonter/india-aviation-traffic) project
  turns them into CSVs, which is what this reads. Ports are DGCA's city names, matched
  to airports by name with the overrides in `measured_in_dgca.py`. Where a foreign city
  has several airports (London, New York), the one Wikipedia lists for that Indian city
  is used, else the one with the most routes, so `parse_wikipedia.py` has to run first
  (the source stops with an error if its routes are missing). The international table is quarterly, so each quarter is spread over its
  months. Like BITRE, it counts passengers between the cities a flight number links, so
  through flights add pairs with no nonstop flight; `build_routes.py` drops any pair
  Wikipedia doesn't list. Airports without an IATA code (Purnea, Pithoragarh, Mundra,
  about 0.3M passengers a year) are left out.
- Skipped: Canada (StatCan's domestic city-pair series stopped, and its newer tables
  are journeys from origin to destination, not flights; Canada–US routes are already in
  T-100), Japan MLIT (e-Stat Excel files with Japanese route names: more than 30
  minutes of work), Korea (airportal.go.kr resets the connection), and the OpenSky
  flight-tracking API (needs a registered account, and counts flights, not passengers).
