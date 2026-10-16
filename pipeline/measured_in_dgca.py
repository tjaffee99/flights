"""India: DGCA city-pair traffic, as compiled from DGCA's own reports by the india-aviation-traffic
project (github.com/Vonter/india-aviation-traffic).

  aggregated/domestic/city.csv        Monthly Statistics (Domestic Air Transport): passengers each
                                      way between pairs of Indian cities, scheduled domestic services.
  aggregated/international/city.csv   Quarterly Statistics (International Air Transport), Table 4:
                                      passengers each way between an Indian city and a foreign city,
                                      Indian and foreign airlines.

Ports are *city names* as DGCA writes them ("MUMBAI (NAVI MUMBAI)", "GOA (MOPA, NORTH GOA)",
"DON MUEANG, BANGKOK"). They're matched to airports by name, with the overrides below. Where a city
has several airports (London, New York), the one Wikipedia's route tables have flying to that Indian
city is used. A quarter's passengers are spread over its three months, so a route flown all year
counts twelve months.
"""
from __future__ import annotations

import csv
import re
from collections import Counter

import pandas as pd

from measured_common import BUILD, CACHE, Airports, Http, log, window_12

DIR = CACHE / "in_dgca"
RAW = "https://raw.githubusercontent.com/Vonter/india-aviation-traffic/main/aggregated/"
PAGE = "https://github.com/Vonter/india-aviation-traffic"

# names DGCA uses that don't match an airport's city or name in OurAirports
NAMES = {
    "DELHI": "DEL", "NEW DELHI": "DEL", "MUMBAI": "BOM", "MUMBAI (MUMBAI)": "BOM", "MUMBAI (NAVI MUMBAI)": "NMI",
    "NAVI MUMBAI": "NMI", "GOA (DABOLIM, SOUTH GOA)": "GOI", "DABOLIM": "GOI", "GOA (MOPA, NORTH GOA)": "GOX",
    "MOPA": "GOX", "HINDON AIRPORT": "HDO", "HINDON": "HDO", "KOZHIKODE": "CCJ", "CALICUT": "CCJ",
    "TRIVANDRUM": "TRV", "THIRUVANANTHAPURAM": "TRV", "MANGALORE": "IXE", "MANGALORE (MANGALURU)": "IXE",
    "VIJAYWADA": "VGA", "AYODHYA": "AYJ", "AYODHYA INTERNATIONAL AIRPORT": "AYJ", "RAJKOT": "HSR",
    "RAJKOT INTERNATIONAL AIRPORT": "HSR", "HIRASAR (RAJKOT)": "HSR", "HUBLI": "HBX", "AIZAWL": "AJL",
    "TIRUCHIRAPALLY": "TRZ", "TIRUCHIRAPPALLI": "TRZ", "TRICHY": "TRZ", "NASIK": "ISK", "NASHIK": "ISK",
    "DHARAMSALA": "DHM", "KANGRA": "DHM", "AGATTI ISLAND": "AGX", "AGATTI": "AGX", "SHIVAMOGGA": "RQY",
    "SHIVAMOGGA AIRPORT": "RQY", "MALVAN": "SDW", "SINDHUDURG": "SDW", "CUDDAPAH": "CDP", "KADAPA": "CDP",
    "VIDYANAGAR": "VDY", "SHOLAPUR": "SSE", "SOLAPUR": "SSE", "BATHINDA": "BUP", "GULBARGA": "GBI",
    "AMRAVATI AIRPORT": "AVR", "HISSAR": "HSS", "SIMLA": "SLV", "SHIMLA": "SLV", "AMBIKAPUR AIRPORT": "AHA",
    "VISHAKHAPATNAM (VISAKHAPATNAM)": "VTZ", "VISHAKHAPATNAM": "VTZ", "VISAKHAPATNAM": "VTZ",
    "BAGDOGRA": "IXB", "PORT BLAIR": "IXZ", "BENGALURU": "BLR", "BANGALORE": "BLR",
    # abroad
    "ABUDHABI": "AUH", "ABU DHABI": "AUH", "KUALALUMPUR": "KUL", "KUALA LUMPUR": "KUL", "DON MUEANG, BANGKOK": "DMK",
    "BANGKOK": "BKK", "KUWAIT": "KWI", "BAHRAIN": "BAH", "MALE": "MLE", "HONG KONG": "HKG", "SINGAPORE": "SIN",
    "DAMMAM": "DMM", "HELNSKI": "HEL", "HELSINKI": "HEL", "TANGERANG": "CGK", "JAKARTA": "CGK",
    "DAR ES SALAM": "DAR", "DAR ES SALAAM": "DAR", "NAJF": "NJF", "NAJAF": "NJF", "ST-DENIS, REUNION": "RUN",
    "PORT LOUIS,MAURITIUS": "MRU", "PORT LOUIS, MAURITIUS": "MRU", "RANGOON": "RGN", "YANGON": "RGN",
    "DUBAI": "DXB", "SHARJAH": "SHJ", "RAS AL-KHAIMAH": "RKT", "RAS AL KHAIMAH": "RKT", "DOHA": "DOH",
    "MUSCAT": "MCT", "KATHMANDU": "KTM", "COLOMBO": "CMB", "DHAKA": "DAC", "MAURITIUS": "MRU", "SEYCHELLES": "SEZ",
    "PHUKET": "HKT", "DENPASAR": "DPS", "BALI": "DPS", "HO CHI MINH CITY": "SGN", "HANOI": "HAN", "TASHKENT": "TAS",
    "ALMATY": "ALA", "TBILISI": "TBS", "BAKU": "GYD", "TEL AVIV": "TLV", "NAIROBI": "NBO", "ADDIS ABABA": "ADD",
    "JOHANNESBURG": "JNB", "TOKYO": "NRT", "TOKYO (NARITA)": "NRT", "TOKYO (HANEDA)": "HND", "SEOUL": "ICN",
    "SHANGHAI": "PVG", "BEIJING": "PEK", "GUANGZHOU": "CAN", "MELBOURNE": "MEL", "SYDNEY": "SYD", "PERTH": "PER",
    "MOSCOW": "SVO", "PARIS": "CDG", "ROME": "FCO", "MILAN": "MXP", "ISTANBUL": "IST", "TORONTO": "YYZ",
    "VANCOUVER": "YVR", "CHICAGO": "ORD", "WASHINGTON": "IAD", "SAN FRANCISCO": "SFO",
}


def fold(text):
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


class Matcher:
    """City names -> IATA, by the overrides, else an airport whose city or name matches. Ties between
    a city's airports go to the one Wikipedia lists flights to the other end from."""

    def __init__(self, airports: Airports):
        self.airports = airports
        a = airports.df[airports.df.iata_code.str.fullmatch(r"[A-Z0-9]{3}") & (airports.df.type != "closed")]
        self.rows = list(zip(a.iata_code, a.iso_country, a.type, a.scheduled_service,
                             a.municipality.map(fold), a.name.map(fold)))
        self.listed = Counter()
        path = BUILD / "wiki_routes.csv"
        if path.exists():
            with open(path, newline="") as f:
                for r in csv.DictReader(f):
                    if r["status"] == "current" and r["dest"]:
                        self.listed[frozenset((r["origin"], r["dest"]))] += 1
        self.cache = {}

    def candidates(self, raw, india):
        name = " ".join(str(raw).upper().split())
        if name in NAMES:
            code = NAMES[name]
            return [code] if (self.airports.country(code) == "IN") == india else []
        if (name, india) in self.cache:
            return self.cache[(name, india)]
        parts = re.match(r"^(.*?)\s*\((.*)\)$", name)
        tries = [parts.group(2), parts.group(1)] if parts else [name]
        tries = [t.strip() for x in tries for t in x.split(",")]   # "NEWARK, NEW YORK": Newark first
        rank = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
        found = []
        for t in tries:
            key = fold(t)
            if len(key) < 3:
                continue
            hits = [r for r in self.rows if (r[1] == "IN") == india and (r[4] == key or r[5].startswith(key))]
            hits.sort(key=lambda r: (r[3] != "yes", rank.get(r[2], 3)))
            found = [r[0] for r in hits if r[3] == "yes"] or [r[0] for r in hits[:1]]
            if found:
                break
        self.cache[(name, india)] = found
        return found

    def pair(self, indian, foreign, india_both):
        a = self.candidates(indian, True)
        b = self.candidates(foreign, india_both)
        if not a or not b:
            return (a[0] if a else None), (b[0] if b else None)
        # where either city has several airports, prefer the pair Wikipedia lists flights for
        best = max(((x, y) for x in a for y in b), key=lambda p: (self.listed[frozenset(p)], -a.index(p[0]) - b.index(p[1])))
        return best


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def run(http: Http, airports: Airports):
    match = Matcher(airports)
    meta_common = {"publisher": "Directorate General of Civil Aviation (DGCA), India; compiled by the "
                                "india-aviation-traffic project", "license": "DGCA public statistics",
                   "url": PAGE}
    results = []

    # ---------------- domestic, monthly ----------------
    path = http.fetch(RAW + "domestic/city.csv", DIR / "domestic_city.csv", max_age_days=7)
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    d["ym"] = d.Year.str.zfill(4) + "-" + d.Month.str.zfill(2)
    d["to"], d["fro"] = d.PaxToCity2.map(_num), d.PaxFromCity2.map(_num)
    start, end = window_12(d[(d.to + d.fro) > 0].ym.max())
    d = d[(d.ym >= start) & (d.ym <= end)]
    pairs = {(c1, c2): match.pair(c1, c2, True) for c1, c2 in set(zip(d.City1, d.City2))}
    rows = []
    for (c1, c2, ym, to, fro) in zip(d.City1, d.City2, d.ym, d.to, d.fro):
        a, b = pairs[(c1, c2)]
        rows.append(("in_dgca_dom", "IN", c1, c2, a, b, ym, to))
        rows.append(("in_dgca_dom", "IN", c2, c1, b, a, ym, fro))
    m = pd.DataFrame(rows, columns=["source", "reporter", "origin_raw", "dest_raw", "origin", "dest", "ym", "passengers"])
    m = m.assign(seats=float("nan"), departures=float("nan"), carrier="", directional=1, period_start=start, period_end=end)
    results.append((m, dict(meta_common, id="in_dgca_dom", name="DGCA city-pair wise domestic traffic (monthly)",
                            download=RAW + "domestic/city.csv", period_start=start, period_end=end,
                            measure="Passengers each way between pairs of Indian cities, scheduled domestic services.",
                            directional=True,
                            coverage="All scheduled domestic flights in India, by city pair; cities matched to airports."),
                    {"window_passengers": float(m.passengers.sum())}))
    log(f"[in_dgca_dom] window {start}..{end}, {m.passengers.sum() / 1e6:.1f}M passengers")

    # ---------------- international, quarterly ----------------
    path = http.fetch(RAW + "international/city.csv", DIR / "international_city.csv", max_age_days=7)
    i = pd.read_csv(path, dtype=str, keep_default_na=False)
    i["year"] = i.Year.astype(int).map(lambda y: y + 2000 if y < 100 else y)
    i["q"] = i.Quarter.astype(int)
    i["to"], i["fro"] = i.PaxToCity2.map(_num), i.PaxFromCity2.map(_num)
    last = max(zip(i[(i.to + i.fro) > 0].year, i[(i.to + i.fro) > 0].q))
    qend = f"{last[0]:04d}-{last[1] * 3:02d}"
    start, end = window_12(qend)
    rows = []
    for (c1, c2, y, q, to, fro) in zip(i.City1, i.City2, i.year, i.q, i.to, i.fro):
        months = [f"{y:04d}-{3 * (q - 1) + k:02d}" for k in (1, 2, 3)]
        if months[0] < start or months[-1] > end:
            continue
        # City1 is Indian in Table 4; check, since a few rows run the other way
        india1 = match.candidates(c1, True)
        ind, fgn, flip = (c1, c2, False) if india1 else (c2, c1, True)
        a, b = match.pair(ind, fgn, False)
        out, back = (fro, to) if flip else (to, fro)
        for ym in months:
            rows.append(("in_dgca_intl", "IN", ind, fgn, a, b, ym, out / 3))
            rows.append(("in_dgca_intl", "IN", fgn, ind, b, a, ym, back / 3))
    m = pd.DataFrame(rows, columns=["source", "reporter", "origin_raw", "dest_raw", "origin", "dest", "ym", "passengers"])
    m = m.assign(seats=float("nan"), departures=float("nan"), carrier="", directional=1, period_start=start, period_end=end)
    results.append((m, dict(meta_common, id="in_dgca_intl", name="DGCA city-pair wise international traffic (quarterly, Table 4)",
                            download=RAW + "international/city.csv", period_start=start, period_end=end,
                            measure="Passengers each way between an Indian city and a foreign city, Indian and foreign "
                                    "airlines; each quarter spread evenly over its months.",
                            directional=True,
                            coverage="All scheduled international flights to and from India, by city pair; cities "
                                     "matched to airports, multi-airport cities by Wikipedia's route tables."),
                    {"window_passengers": float(m.passengers.sum())}))
    log(f"[in_dgca_intl] window {start}..{end}, {m.passengers.sum() / 1e6:.1f}M passengers")
    return results
