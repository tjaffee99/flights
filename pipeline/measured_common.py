"""Shared helpers for the "measured" passenger-statistics pipeline.

Each source module (measured_us_bts, measured_eurostat, measured_uk_caa, ...)
produces a *monthly* DataFrame with the columns in MONTHLY_COLUMNS, already
restricted to that source's latest 12-month window.  `aggregate()` turns that
into the per-direction rows written to build/measured.csv.

Downloaded files are untrusted data: they live only under cache/<source>/ and
are parsed as data (CSV / JSON / XLSX), never executed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
BUILD = HERE / "build"
USER_AGENT = "FlightAtlasBuilder/0.1 (https://flights.theojaffee.net/)"

MONTHLY_COLUMNS = [
    "source",       # source id, e.g. "us_bts_t100"
    "reporter",     # who reported the row, e.g. "US", "DE:FRA", "GB"
    "origin_raw",   # code/name as published
    "dest_raw",
    "origin",       # IATA code or None if unmapped
    "dest",
    "ym",           # "YYYY-MM"
    "passengers",
    "seats",        # NaN if the source lacks it
    "departures",   # NaN if the source lacks it
    "carrier",      # airline code or ""
    "directional",  # 1 = origin->dest; 0 = both directions combined
    "period_start", # "YYYY-MM" of the 12-month window
    "period_end",
]

OUTPUT_COLUMNS = [
    "source", "reporter", "origin_iata", "dest_iata", "passengers", "seats",
    "departures", "months_with_traffic", "first_month", "last_month",
    "period_start", "period_end", "carriers", "directional",
    "block_minutes", "air_minutes",   # gate to gate and wheels up to down, minutes in all (US only)
    "timed_departures",               # ... over this many departures (foreign airlines report no times)
]


def log(*args):
    print(*args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# HTTP with an on-disk cache
# --------------------------------------------------------------------------
class Http:
    """Sequential, polite HTTP client that caches responses on disk."""

    def __init__(self, delay: float = 1.0, refresh: bool = False):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self.refresh = refresh
        self._last = 0.0

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.time()

    def request(self, method: str, url: str, retries: int = 4, timeout=300, **kw):
        err = None
        for attempt in range(retries):
            self._wait()
            try:
                r = self.s.request(method, url, timeout=timeout, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                return r
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                err = e
                log(f"  ! {method} {url[:120]} failed ({e}); retry {attempt + 1}/{retries}")
                time.sleep(5 * (attempt + 1))
        raise err

    def fetch(self, url: str, dest: Path, *, max_age_days: float | None = None,
              method: str = "GET", data=None, validate=None, force: bool | None = None,
              timeout=300) -> Path:
        """Download url to dest unless dest is cached.

        max_age_days: re-download when the cached copy is older than this
        (used for index pages); None = keep forever unless --refresh.
        validate(path) may raise ValueError to reject a download.
        """
        dest = Path(dest)
        force = self.refresh if force is None else force
        if dest.exists() and not force:
            age = (time.time() - dest.stat().st_mtime) / 86400
            if max_age_days is None or age < max_age_days:
                return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        log(f"  downloading {url[:140]}")
        r = self.request(method, url, data=data, stream=True, timeout=timeout)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        if validate:
            try:
                validate(tmp, r)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
        os.replace(tmp, dest)
        return dest

    def fetch_large(self, url: str, dest: Path, *, max_age_days: float | None = None,
                    attempts: int = 12) -> Path:
        """Like fetch() for big files: resumes cut-off transfers with Range requests."""
        dest = Path(dest)
        if dest.exists() and not self.refresh:
            age = (time.time() - dest.stat().st_mtime) / 86400
            if max_age_days is None or age < max_age_days:
                return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        if self.refresh and tmp.exists():
            tmp.unlink()
        total = None
        for attempt in range(attempts):
            have = tmp.stat().st_size if tmp.exists() else 0
            if total is not None and have >= total:
                break
            headers = {"Range": f"bytes={have}-"} if have else {}
            log(f"  downloading {url[:120]} (from byte {have:,})")
            self._wait()
            try:
                with self.s.get(url, headers=headers, stream=True, timeout=300) as r:
                    if r.status_code == 416:      # already complete
                        break
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code} for {url}")
                    if r.status_code == 200:
                        have = 0
                        total = int(r.headers.get("Content-Length", 0)) or None
                    else:
                        m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
                        total = int(m.group(1)) if m else None
                    with open(tmp, "ab" if have else "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                if total is None or tmp.stat().st_size >= total:
                    break
            except (requests.ConnectionError, requests.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                log(f"  ! transfer interrupted at {tmp.stat().st_size if tmp.exists() else 0:,} bytes ({e})")
                time.sleep(3)
        else:
            raise RuntimeError(f"download of {url} did not complete")
        if total is not None and tmp.stat().st_size != total:
            raise RuntimeError(f"size mismatch for {url}: {tmp.stat().st_size} != {total}")
        os.replace(tmp, dest)
        return dest

    def text(self, url: str, dest: Path, max_age_days: float = 1.0, **kw) -> str:
        p = self.fetch(url, dest, max_age_days=max_age_days, **kw)
        return p.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# Airports (OurAirports)
# --------------------------------------------------------------------------
class Airports:
    def __init__(self, path: Path = CACHE / "airports.csv"):
        a = pd.read_csv(path, keep_default_na=False, dtype=str)
        self.df = a
        has_iata = a[a.iata_code.str.fullmatch(r"[A-Z0-9]{3}")]
        self.iata = dict(zip(has_iata.iata_code, has_iata.index))
        self.iata_country = dict(zip(has_iata.iata_code, has_iata.iso_country))
        self.iata_name = dict(zip(has_iata.iata_code, has_iata.name))
        self.iata_city = dict(zip(has_iata.iata_code, has_iata.municipality))
        self.iata_type = dict(zip(has_iata.iata_code, has_iata.type))
        self.iata_sched = dict(zip(has_iata.iata_code, has_iata.scheduled_service))
        # ICAO -> IATA: icao_code first, then ident, then gps_code
        icao = {}
        for col in ("gps_code", "ident", "icao_code"):  # later columns win
            sub = has_iata[has_iata[col].str.fullmatch(r"[A-Z0-9]{4}")]
            icao.update(dict(zip(sub[col], sub.iata_code)))
        self.icao_to_iata = icao
        # US FAA location identifiers -> IATA (BTS uses some of these)
        us = has_iata[(has_iata.iso_country.isin(["US", "PR", "VI", "GU", "AS", "MP"]))
                      & (has_iata.local_code != "")]
        self.faa_to_iata = dict(zip(us.local_code, us.iata_code))
        # Former codes listed in OurAirports keywords (e.g. PBI/KPBI for the
        # airport now coded DJT/KDJT).  Only used when a direct lookup fails,
        # and only when the alias is unambiguous.
        alias = {}
        for code, kw in zip(has_iata.iata_code, has_iata.keywords):
            for tok in set(re.findall(r"\b[A-Z0-9]{3,4}\b", kw)):
                alias.setdefault(tok, set()).add(code)
        self.alias = {k: next(iter(v)) for k, v in alias.items()
                      if len(v) == 1 and k not in self.iata and k not in self.icao_to_iata}
        self.alias_used = {}

    def is_iata(self, code) -> bool:
        return isinstance(code, str) and code in self.iata

    def from_icao(self, code):
        if not isinstance(code, str):
            return None
        code = code.strip().upper()
        hit = self.icao_to_iata.get(code)
        if hit is None and len(code) == 4 and code in self.alias:
            hit = self.alias[code]
            self.alias_used[code] = hit
        return hit

    def from_alias(self, code):
        hit = self.alias.get(code) if isinstance(code, str) else None
        if hit:
            self.alias_used[code] = hit
        return hit

    def country(self, iata):
        return self.iata_country.get(iata)


# --------------------------------------------------------------------------
# Month helpers
# --------------------------------------------------------------------------
def ym_add(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def window_12(end_ym: str) -> tuple[str, str]:
    return ym_add(end_ym, -11), end_ym


def months_between(start: str, end: str) -> list[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = ym_add(cur, 1)
    return out


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def _carrier_list(g: pd.DataFrame) -> str:
    c = g[g.carrier != ""].groupby("carrier").passengers.sum()
    if c.empty:
        return ""
    c = c.reset_index().sort_values(["passengers", "carrier"], ascending=[False, True])
    return "|".join(c.carrier)


def aggregate(monthly: pd.DataFrame):
    """Monthly rows -> (output rows, stats dict)."""
    m = monthly.copy()
    m = m[m.passengers > 0]
    stats = {"input_monthly_rows": int(len(m)), "input_passengers": float(m.passengers.sum())}

    unm = m[m.origin.isna() | m.dest.isna()]
    codes = pd.concat([
        unm[unm.origin.isna()][["origin_raw", "passengers"]].rename(columns={"origin_raw": "code"}),
        unm[unm.dest.isna()][["dest_raw", "passengers"]].rename(columns={"dest_raw": "code"}),
    ])
    top = codes.groupby("code").passengers.sum().sort_values(ascending=False)
    stats["unmapped_monthly_rows"] = int(len(unm))
    stats["unmapped_passengers"] = float(unm.passengers.sum())
    stats["top_unmapped_codes"] = [[str(k), int(v)] for k, v in top.head(25).items()]

    m = m[m.origin.notna() & m.dest.notna()]
    selfloop = m[m.origin == m.dest]
    stats["self_loop_passengers_dropped"] = float(selfloop.passengers.sum())
    m = m[m.origin != m.dest].copy()

    # undirected sources: canonical order origin < dest
    und = m.directional == 0
    a = m.loc[und, ["origin", "dest"]].min(axis=1)
    b = m.loc[und, ["origin", "dest"]].max(axis=1)
    m.loc[und, "origin"] = a
    m.loc[und, "dest"] = b

    keys = ["source", "reporter", "origin", "dest", "directional", "period_start", "period_end"]
    for c in ("block_minutes", "air_minutes", "timed_departures"):
        if c not in m:
            m[c] = float("nan")
    g = m.groupby(keys, sort=True, dropna=False)
    out = g.agg(
        passengers=("passengers", "sum"),
        seats=("seats", lambda s: s.sum(min_count=1)),
        departures=("departures", lambda s: s.sum(min_count=1)),
        block_minutes=("block_minutes", lambda s: s.sum(min_count=1)),
        air_minutes=("air_minutes", lambda s: s.sum(min_count=1)),
        timed_departures=("timed_departures", lambda s: s.sum(min_count=1)),
        months_with_traffic=("ym", "nunique"),
        first_month=("ym", "min"),
        last_month=("ym", "max"),
    ).reset_index()
    if (m.carrier != "").any():
        car = g[["carrier", "passengers"]].apply(_carrier_list).rename("carriers").reset_index()
        out = out.merge(car, on=keys, how="left")
    else:
        out["carriers"] = ""
    # rows built only from non-monthly values (e.g. Eurostat annual fill) have no month info
    out["months_with_traffic"] = out.months_with_traffic.astype("Int64").where(out.first_month.notna())
    out = out.rename(columns={"origin": "origin_iata", "dest": "dest_iata"})
    for c in ("passengers", "seats", "departures", "block_minutes", "air_minutes", "timed_departures"):
        out[c] = out[c].round().astype("Int64")
    out["carriers"] = out["carriers"].fillna("")
    out = out[OUTPUT_COLUMNS]
    stats["rows"] = int(len(out))
    stats["passengers"] = int(out.passengers.sum())
    return out, stats


def top_routes(out: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """Top n undirected airport pairs by passengers (both directions summed,
    taking the max over reporters for pairs reported by several reporters)."""
    d = out.copy()
    d["a"] = d[["origin_iata", "dest_iata"]].min(axis=1)
    d["b"] = d[["origin_iata", "dest_iata"]].max(axis=1)
    per_rep = d.groupby(["reporter", "a", "b"]).passengers.sum().reset_index()
    pair = per_rep.groupby(["a", "b"]).passengers.max().sort_values(ascending=False)
    return pair.head(n).reset_index()


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")


def today() -> str:
    return dt.date.today().isoformat()
