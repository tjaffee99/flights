"""UK: Civil Aviation Authority monthly airport statistics, route tables.

Each monthly page under
  https://www.caa.co.uk/data-and-analysis/uk-aviation-market/airports/uk-airport-data/uk-airport-data-<YYYY>/<month>-<YYYY>/
links CSVs for
  Table 12.1  International air passenger traffic route analysis
              (UK reporting airport <-> foreign airport, both directions combined)
  Table 12.2  Domestic air passenger traffic route analysis
              (UK airport pair, both directions combined, each pair once)
Airports are given by *name*; uk_caa_airport_names.csv (next to this script) maps
(country, name) -> IATA.  Names missing from that file are matched automatically
against OurAirports where unambiguous, and reported.
"""
from __future__ import annotations

import csv
import html as htmlmod
import re
import unicodedata
from pathlib import Path

import pandas as pd

from measured_common import CACHE, HERE, Airports, Http, log, months_between, window_12, ym_add

SOURCE_ID = "uk_caa"
DIR = CACHE / "uk_caa"
BASE = "https://www.caa.co.uk"
INDEX = BASE + "/data-and-analysis/uk-aviation-market/airports/uk-airport-data/uk-airport-data-{y}/"
NAMES_FILE = HERE / "uk_caa_airport_names.csv"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
TABLES = {
    "12_1": re.compile(r"Table\s*12[\s._]*1\b(?!\d).*?\(CSV", re.I | re.S),
    "12_2": re.compile(r"Table\s*12[\s._]*2\b(?!\d).*?\(CSV", re.I | re.S),
}

META = {
    "id": SOURCE_ID,
    "name": "UK CAA airport data: Tables 12.1 (international) and 12.2 (domestic) air passenger route analysis",
    "publisher": "UK Civil Aviation Authority",
    "url": "https://www.caa.co.uk/data-and-analysis/uk-aviation-market/airports/uk-airport-data/",
    "license": "CAA statistics; re-use under the Open Government Licence v3.0 per CAA copyright notice "
               "(https://www.caa.co.uk/our-work/publications/copyright/) - attribute 'UK Civil Aviation Authority'.",
    "measure": "Terminal passengers (scheduled + charter) on each route, both directions combined. "
               "International: UK reporting airport <-> foreign airport. Domestic: UK airport pair counted once.",
    "directional": False,
    "coverage": "All UK reporting airports (incl. Channel Islands / Isle of Man) to every airport they serve.",
}


def _month_pages(http: Http) -> dict[str, str]:
    """{'YYYY-MM': url} for monthly pages of the current and previous two years."""
    import datetime as dt
    pages = {}
    y0 = dt.date.today().year
    for y in (y0, y0 - 1, y0 - 2):
        try:
            html = http.text(INDEX.format(y=y), DIR / f"index_{y}.html", max_age_days=1)
        except RuntimeError as e:
            log(f"  CAA index {y}: {e}")
            continue
        for href in set(re.findall(r'href="([^"]*uk-airport-data-%d/([a-z]+)-%d/?)"' % (y, y), html)):
            url, mon = href
            if mon in MONTHS:
                pages[f"{y}-{MONTHS.index(mon) + 1:02d}"] = BASE + url if url.startswith("/") else url
    return pages


def _doc_links(page_html: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r'<a[^>]*href="(/Documents/Download/[^"]+)"[^>]*>(.*?)</a>', page_html, re.S):
        title = " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())
        for key, rx in TABLES.items():
            if key not in out and rx.search(title):
                out[key] = BASE + m.group(1)
    return out


def _validate_csv(path: Path, resp):
    head = path.read_bytes()[:400].decode("utf-8-sig", "replace").lower()
    if "this_period" not in head or "total_pax_this_period" not in head:
        raise ValueError(f"not a CAA route CSV: {head[:100]!r}")


def fetch(http: Http) -> tuple[str, str, dict]:
    pages = _month_pages(http)
    if not pages:
        raise RuntimeError("no CAA monthly pages found")
    end = max(pages)
    start, end = window_12(end)
    files = {}
    for ym in months_between(start, end):
        if ym not in pages:
            raise RuntimeError(f"CAA monthly page for {ym} not found")
        targets = {k: DIR / f"{ym}_table_{k}.csv" for k in TABLES}
        if all(p.exists() for p in targets.values()) and not http.refresh:
            files[ym] = targets
            continue
        page = http.text(pages[ym], DIR / f"page_{ym}.html", max_age_days=7)
        links = _doc_links(page)
        for k, p in targets.items():
            if k not in links:
                raise RuntimeError(f"CAA {ym}: table {k} CSV link not found on {pages[ym]}")
            http.fetch(links[k], p, validate=_validate_csv)
        files[ym] = targets
    return start, end, files


# --------------------------------------------------------------------------
# name -> IATA
# --------------------------------------------------------------------------
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return " ".join(s.split())


COUNTRY_FIX = {  # CAA country names that do not match OurAirports' country names
    "UNITED STATES OF AMERICA": "US", "USA": "US", "IRISH REPUBLIC": "IE", "CZECH REPUBLIC": "CZ",
    "RUSSIA": "RU", "RUSSIAN FEDERATION": "RU", "TURKEY": "TR", "TURKIYE": "TR",
    "SOUTH KOREA": "KR", "KOREA REPUBLIC OF": "KR", "KOREA SOUTH": "KR",
    "UNITED ARAB EMIRATES": "AE", "MACEDONIA": "MK", "NORTH MACEDONIA": "MK",
    "CANARY ISLANDS": "ES", "MADEIRA": "PT", "AZORES": "PT", "BALEARIC ISLANDS": "ES",
    "SPAIN AND CANARY ISLANDS": "ES", "PORTUGAL INCLUDING MADEIRA": "PT",
    "GREECE": "GR", "CYPRUS": "CY", "NORTHERN CYPRUS": "CY", "CAPE VERDE": "CV", "CAPE VERDE ISLANDS": "CV",
    "IVORY COAST": "CI", "COTE D IVOIRE": "CI", "BOSNIA HERZEGOVINA": "BA", "BOSNIA AND HERZEGOVINA": "BA",
    "TANZANIA": "TZ", "VIETNAM": "VN", "LAOS": "LA", "IRAN": "IR", "SYRIA": "SY", "MOLDOVA": "MD",
    "ST LUCIA": "LC", "SAINT LUCIA": "LC", "ST KITTS AND NEVIS": "KN", "ST KITTS NEVIS": "KN",
    "ST VINCENT AND THE GRENADINES": "VC", "ANTIGUA": "AG", "ANTIGUA AND BARBUDA": "AG",
    "TRINIDAD AND TOBAGO": "TT", "TURKS AND CAICOS ISLANDS": "TC", "CAYMAN ISLANDS": "KY",
    "BRITISH VIRGIN ISLANDS": "VG", "VIRGIN ISLANDS BRITISH": "VG", "FALKLAND ISLANDS": "FK",
    "GIBRALTAR": "GI", "CHANNEL ISLANDS": "GB", "ISLE OF MAN": "IM", "JERSEY": "JE", "GUERNSEY": "GG",
    "HONG KONG": "HK", "MACAO": "MO", "MACAU": "MO", "TAIWAN": "TW", "CHINA": "CN",
    "GAMBIA": "GM", "THE GAMBIA": "GM", "BAHAMAS": "BS", "THE BAHAMAS": "BS", "SLOVAK REPUBLIC": "SK",
    "UNITED KINGDOM": "GB", "NETHERLANDS": "NL", "BRUNEI": "BN", "MYANMAR": "MM", "BURMA": "MM",
    "ST HELENA": "SH", "SAINT HELENA": "SH", "ASCENSION ISLAND": "SH", "REUNION": "RE",
    "DEMOCRATIC REPUBLIC OF CONGO": "CD", "CONGO DEMOCRATIC REPUBLIC": "CD", "KOSOVO": "XK",
    "SVALBARD": "NO", "FAROE ISLANDS": "FO", "GREENLAND": "GL", "CURACAO": "CW", "ARUBA": "AW",
    "BOSNIA-HERZEGOVINA": "BA", "ISLE OF CURACAO NETH.ANTILLES": "CW", "ISLE OF DIEGO GARCIA": "IO",
    "MALDIVE ISLANDS": "MV", "PORTUGAL(EXCLUDING MADEIRA)": "PT", "PORTUGAL(MADEIRA)": "PT",
    "REPUBLIC OF KOREA": "KR", "REPUBLIC OF MOLDOVA": "MD", "REPUBLIC OF MONTENEGRO": "ME",
    "REPUBLIC OF SERBIA": "RS", "REPUBLIC OF SOUTH AFRICA": "ZA", "SPAIN(CANARY ISLANDS)": "ES",
    "SAINT KITTS AND NEVIS": "KN", "SAINT VINCENT AND THE GRENADINES": "VC", "OIL RIGS": "--",
    "BERMUDA": "BM", "MALDIVES": "MV", "MAURITIUS": "MU", "SEYCHELLES": "SC", "PALESTINE": "PS",
}


def _country_codes() -> dict[str, str]:
    c = pd.read_csv(CACHE / "countries.csv", keep_default_na=False, dtype=str)
    m = {_norm(n): code for n, code in zip(c.name, c.code)}
    m.update({_norm(k): v for k, v in COUNTRY_FIX.items()})
    return m


def load_name_table() -> dict[tuple[str, str], str]:
    out = {}
    if NAMES_FILE.exists():
        with open(NAMES_FILE, newline="") as f:
            for r in csv.DictReader(f):
                out[(r["country"], r["caa_name"])] = r["iata"].strip()
    return out


STOP = {"INTERNATIONAL", "INTL", "AIRPORT", "AIRFIELD", "THE", "OF", "DE", "DEL", "LA", "LE", "AND"}


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t not in STOP and len(t) > 1}


class NameMatcher:
    """Automatic fallback: best token match of a CAA airport name within its
    country, preferring airports with scheduled service and larger types.
    Only used for names missing from uk_caa_airport_names.csv."""

    def __init__(self, airports: Airports):
        a = airports.df
        a = a[(a.iata_code.str.fullmatch(r"[A-Z0-9]{3}")) & a.type.isin(
            ["large_airport", "medium_airport", "small_airport"])].copy()
        a["bonus"] = a.type.map({"large_airport": 3, "medium_airport": 2, "small_airport": 0}) \
            + (a.scheduled_service == "yes") * 3
        a["toks"] = [_tokens(f"{n} {m} {k}") for n, m, k in zip(a.name, a.municipality, a.keywords)]
        self.a = a

    def match(self, cc: str | None, name: str) -> str | None:
        want = _tokens(name)
        if not want:
            return None
        a = self.a if cc is None else self.a[self.a.iso_country == cc]
        best, best_score = None, 0.0
        for code, toks, bonus in zip(a.iata_code, a.toks, a.bonus):
            hit = sum(1 for t in want if t in toks or any(x.startswith(t) for x in toks if len(t) >= 4))
            if hit == 0:
                continue
            score = hit / len(want) * 10 + bonus
            if score > best_score:
                best, best_score = code, score
        # require most of the name to match
        return best if best_score >= 8 else None


def load(airports: Airports, start: str, end: str, files: dict):
    intl, dom = [], []
    for ym, paths in sorted(files.items()):
        i = pd.read_csv(paths["12_1"], encoding="utf-8-sig", dtype=str, keep_default_na=False)
        d = pd.read_csv(paths["12_2"], encoding="utf-8-sig", dtype=str, keep_default_na=False)
        for df in (i, d):
            periods = set(df.this_period.str.strip())
            if periods != {ym.replace("-", "")}:
                raise RuntimeError(f"CAA {ym}: unexpected this_period values {sorted(periods)[:5]}")
            df["ym"] = ym
        intl.append(i)
        dom.append(d)
    intl = pd.concat(intl, ignore_index=True)
    dom = pd.concat(dom, ignore_index=True)
    for df in (intl, dom):
        df["pax"] = pd.to_numeric(df.total_pax_this_period.str.replace(",", ""), errors="coerce").fillna(0)

    countries = _country_codes()
    table = load_name_table()
    matcher = NameMatcher(airports)
    auto, unknown_country = {}, set()

    def resolve(country_name: str, name: str):
        key = (country_name, name)
        if key in table:
            v = table[key]
            return v if v else None
        if key in auto:
            return auto[key]
        cc = countries.get(_norm(country_name))
        if cc is None:
            unknown_country.add(country_name)
        if cc == "GB":  # Channel Islands and Isle of Man are separate ISO codes
            v = next((x for x in (matcher.match(k, name) for k in ("GB", "JE", "GG", "IM")) if x), None)
        else:
            v = matcher.match(cc, name) if cc != "--" else None
        auto[key] = v
        return v

    uk_names = sorted(set(intl.UK_airport) | set(dom.airport_1_name) | set(dom.airport_2_name))
    uk_map = {n: resolve("UNITED KINGDOM", n) for n in uk_names}
    intl["origin"] = intl.UK_airport.map(uk_map)
    intl["dest"] = [resolve(c, n) for c, n in zip(intl.foreign_country, intl.foreign_airport)]
    intl["origin_raw"] = intl.UK_airport
    intl["dest_raw"] = intl.foreign_country + "/" + intl.foreign_airport
    dom["origin"] = dom.airport_1_name.map(uk_map)
    dom["dest"] = dom.airport_2_name.map(uk_map)
    dom["origin_raw"], dom["dest_raw"] = dom.airport_1_name, dom.airport_2_name
    allr = pd.concat([intl.assign(kind="intl"), dom.assign(kind="dom")], ignore_index=True)

    # names that were matched automatically (not in the reviewed table) - review these
    pax_by_name = pd.concat([
        intl.groupby(["foreign_country", "foreign_airport"]).pax.sum(),
        pd.concat([dom.groupby("airport_1_name").pax.sum(), dom.groupby("airport_2_name").pax.sum(),
                   intl.groupby("UK_airport").pax.sum()]).groupby(level=0).sum()
        .rename(lambda n: ("UNITED KINGDOM", n)),
    ])
    stats = {
        "auto_matched": [[c, n, v, int(pax_by_name.get((c, n), 0))] for (c, n), v in sorted(auto.items())
                         if v is not None],
        "unmatched_names": sorted([[c, n, int(pax_by_name.get((c, n), 0))] for (c, n), v in auto.items()
                                   if v is None], key=lambda x: -x[2]),
        "unknown_countries": sorted(unknown_country),
        "intl_passengers_raw": float(intl.pax.sum()),
        "domestic_passengers_raw": float(dom.pax.sum()),
    }
    out = pd.DataFrame({
        "source": SOURCE_ID, "reporter": "GB",
        "origin_raw": allr.origin_raw, "dest_raw": allr.dest_raw,
        "origin": allr.origin.where(allr.origin.notna(), None),
        "dest": allr.dest.where(allr.dest.notna(), None),
        "ym": allr.ym, "passengers": allr.pax.astype(float),
        "seats": float("nan"), "departures": float("nan"), "carrier": "",
        "directional": 0, "period_start": start, "period_end": end,
    })
    return out, stats


def run(http: Http, airports: Airports):
    start, end, files = fetch(http)
    monthly, stats = load(airports, start, end, files)
    meta = dict(META, period_start=start, period_end=end)
    return monthly, meta, stats
