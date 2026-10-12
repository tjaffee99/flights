"""US: BTS T-100 Segment (All Carriers), via the TranStats download form.

The PREZIP directory has no ready-made T-100 files, so we drive the ASP.NET
form at DL_SelectFields.aspx (table "FMG" = T-100 Segment, all carriers):
GET the form for __VIEWSTATE/__EVENTVALIDATION, then POST the selected
fields with cboYear/cboPeriod; the response is a zip with one CSV.
One zip per month is cached in cache/bts_t100/.
"""
from __future__ import annotations

import re
import time
import zipfile
from pathlib import Path

import pandas as pd

from measured_common import CACHE, Airports, Http, log, months_between, window_12

SOURCE_ID = "us_bts_t100"
FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45"
POST_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4+Pn44vr45"
FIELDS = ["PASSENGERS", "SEATS", "DEPARTURES_PERFORMED", "ORIGIN", "DEST",
          "ORIGIN_COUNTRY", "DEST_COUNTRY", "UNIQUE_CARRIER", "CLASS", "YEAR", "MONTH"]
DIR = CACHE / "bts_t100"
# BTS codes that are not IATA codes, verified by hand (route/carrier pattern).
MANUAL = {
    "JRV": "NRR",  # Ceiba / Jose Aponte de la Torre PR (FAA RVR), Culebra/Vieques shuttles
}
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}

META = {
    "id": SOURCE_ID,
    "name": "BTS T-100 Segment (All Carriers)",
    "publisher": "US Department of Transportation, Bureau of Transportation Statistics",
    "url": "https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FMG",
    "download": FORM_URL,
    "license": "US federal government work; public domain (no copyright in the US).",
    "measure": "Passengers on board per nonstop flight segment (T-100 PASSENGERS), "
               "CLASS F (scheduled passenger/cargo service) only, rows with 0 passengers dropped; "
               "SEATS and DEPARTURES_PERFORMED summed over the same rows.",
    "directional": True,
    "coverage": "All flight segments touching the US and its territories (domestic and "
                "international), flown by US and foreign carriers.",
}


def _hidden(html: str, name: str) -> str:
    m = re.search(r'name="%s" id="%s" value="([^"]*)"' % (name, name), html)
    if not m:
        raise RuntimeError(f"BTS form: hidden field {name} not found")
    return m.group(1)


def _validate_zip(path: Path, resp):
    if not zipfile.is_zipfile(path):
        raise ValueError(f"BTS returned non-zip ({resp.headers.get('content-type')})")
    with zipfile.ZipFile(path) as z:
        if not any(n.lower().endswith(".csv") for n in z.namelist()):
            raise ValueError("BTS zip has no CSV")


def _get_form(http: Http, max_age_days: float) -> str:
    """The TranStats form intermittently 302s to Homepage.asp (load shedding?);
    retry with a pause until the real form comes back."""
    for attempt in range(15):
        html = http.text(FORM_URL, DIR / "form.html", max_age_days=max_age_days)
        if "__EVENTVALIDATION" in html and "Latest Available Data" in html:
            return html
        log(f"  BTS form not served (redirected to homepage?); waiting 30 s ({attempt + 1}/15)")
        time.sleep(30)
        max_age_days = 0
    raise RuntimeError("BTS form unavailable (keeps redirecting to Homepage.asp)")


def fetch(http: Http) -> tuple[str, str]:
    """Download the latest 12 published months. Returns (period_start, period_end)."""
    form = _get_form(http, max_age_days=0.5)
    m = re.search(r"Latest Available Data:\s*([A-Za-z]+)\s+(\d{4})", form)
    if not m:
        raise RuntimeError("BTS form: 'Latest Available Data' not found")
    end = f"{int(m.group(2)):04d}-{MONTHS[m.group(1)]:02d}"
    start, end = window_12(end)
    log(f"[{SOURCE_ID}] latest available month {end}; window {start}..{end}")
    for ym in months_between(start, end):
        dest = DIR / f"t100_segment_all_carrier_{ym.replace('-', '_')}.zip"
        if dest.exists() and not http.refresh:
            continue
        for attempt in range(6):
            # fresh viewstate for every POST (the form is stateful)
            form = _get_form(http, max_age_days=0)
            data = _post_data(form, ym)
            try:
                http.fetch(POST_URL, dest, method="POST", data=data, validate=_validate_zip, force=True)
                break
            except ValueError as e:
                log(f"  BTS POST for {ym} failed ({e}); waiting 30 s")
                time.sleep(30)
        else:
            raise RuntimeError(f"BTS download for {ym} kept failing")
    return start, end


def _post_data(form: str, ym: str) -> dict:
    data = {
        "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
        "__VIEWSTATE": _hidden(form, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden(form, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(form, "__EVENTVALIDATION"),
        "cboGeography": "All", "cboYear": ym[:4], "cboPeriod": str(int(ym[5:])),
        "btnDownload": "Download",
    }
    for f in FIELDS:
        data[f] = "on"
    return data


def load(airports: Airports, start: str, end: str):
    frames = []
    for ym in months_between(start, end):
        p = DIR / f"t100_segment_all_carrier_{ym.replace('-', '_')}.zip"
        with zipfile.ZipFile(p) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            df = pd.read_csv(z.open(name), dtype={"ORIGIN": str, "DEST": str, "UNIQUE_CARRIER": str,
                                                   "CLASS": str, "ORIGIN_COUNTRY": str, "DEST_COUNTRY": str},
                             keep_default_na=False, na_values=[""])
        got = sorted({f"{y:04d}-{mo:02d}" for y, mo in zip(df.YEAR, df.MONTH)})
        if got != [ym]:
            raise RuntimeError(f"{p.name}: expected {ym}, file has {got}")
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    stats = {"raw_rows": int(len(raw)),
             "raw_passengers_by_class": {k: int(v) for k, v in raw.groupby("CLASS").PASSENGERS.sum().items()}}
    df = raw[(raw.CLASS == "F") & (raw.PASSENGERS > 0)].copy()

    # Map BTS 3-letter codes to IATA (identical for almost all airports), then
    # cross-check the country BTS gives against OurAirports and drop clashes
    # (e.g. BTS "PTL" = Port Armstrong AK, IATA PTL = Peru).
    territories = {"PR", "VI", "GU", "AS", "MP", "UM"}  # BTS reports these as "US"
    pts = pd.concat([df[["ORIGIN", "ORIGIN_COUNTRY", "PASSENGERS"]].set_axis(["raw", "cc", "pax"], axis=1),
                     df[["DEST", "DEST_COUNTRY", "PASSENGERS"]].set_axis(["raw", "cc", "pax"], axis=1)])
    cc_of = pts.groupby("raw").cc.agg(lambda s: s.mode().iat[0]).to_dict()
    cmap, how, clashes = {}, {}, []
    for c, cc in cc_of.items():
        cand = []
        if airports.is_iata(c):
            cand.append((c, "iata"))
        if c in airports.faa_to_iata:
            cand.append((airports.faa_to_iata[c], "faa_lid"))
        if c in MANUAL:
            cand.insert(0, (MANUAL[c], "manual"))
        if c in airports.alias:
            cand.append((airports.alias[c], "ourairports_keyword_alias"))
        cmap[c] = None
        for iata, kind in cand:
            oa = airports.country(iata)
            if oa == cc or (cc == "US" and oa in territories):
                cmap[c], how[c] = iata, kind
                break
            clashes.append([c, iata, cc, oa])
    df["origin"] = df.ORIGIN.map(cmap)
    df["dest"] = df.DEST.map(cmap)
    pax_by_code = pts.groupby("raw").pax.sum()
    stats["country_clashes_dropped"] = [[*x, int(pax_by_code.get(x[0], 0))] for x in clashes
                                        if cmap.get(x[0]) is None]
    stats["non_identity_mappings"] = {k: [cmap[k], v] for k, v in how.items() if v != "iata" or cmap[k] != k}

    out = pd.DataFrame({
        "source": SOURCE_ID,
        "reporter": "US",
        "origin_raw": df.ORIGIN, "dest_raw": df.DEST,
        "origin": df.origin, "dest": df.dest,
        "ym": [f"{y:04d}-{mo:02d}" for y, mo in zip(df.YEAR, df.MONTH)],
        "passengers": df.PASSENGERS.astype(float),
        "seats": df.SEATS.astype(float),
        "departures": df.DEPARTURES_PERFORMED.astype(float),
        "carrier": df.UNIQUE_CARRIER.fillna("").astype(str),
        "directional": 1,
        "period_start": start, "period_end": end,
    })
    return out, stats


def run(http: Http, airports: Airports):
    start, end = fetch(http)
    monthly, stats = load(airports, start, end)
    meta = dict(META, period_start=start, period_end=end)
    return monthly, meta, stats
