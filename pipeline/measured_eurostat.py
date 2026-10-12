"""Europe: Eurostat avia_par_<cc> route tables (one dataset per reporting country).

airp_pr = <REP_CC>_<REP_ICAO>_<PARTNER_CC>_<PARTNER_ICAO>.  Directional measures:
  PAS_CRD_DEP  passengers carried, departing the reporting airport to the partner
  PAS_CRD_ARR  passengers carried, arriving at the reporting airport from the partner
  PAS_BRD_DEP / PAS_BRD_ARR  the same on a flight-stage (passengers on board) basis
(PAS_CRD / PAS_BRD without suffix = DEP + ARR, both directions combined: not used.)
Seats: unit SEAT, ST_PAS_DEP/ARR.  Departures: unit FLIGHT, CAF_PAS_DEP/ARR
(commercial passenger flights).  PAS_BRD_DEP/ARR (passengers on board, flight-stage
basis) are downloaded; MEASURE below selects which passenger measure is used.

PAS_CRD counts passengers by *on-flight origin/destination*: on a multi-stop flight
A-B-C, passengers A->C are reported on pair A-C, which is not a nonstop segment
(e.g. CDG-Papeete via LAX shows up as LFPG_NTAA).  PAS_BRD counts passengers on
board each *flight stage*, like BTS T-100 Segment, so we use PAS_BRD_DEP/ARR by
default (totals differ by ~0.3%).

A pair between two reporting airports appears twice (once per reporting airport),
tagged by reporter = "<country>:<reporting airport IATA>"; dedup happens downstream.

Data are fetched with the SDMX 2.1 REST API as SDMX-CSV.  Large extractions are
answered asynchronously (a SOAP "queued" envelope with a key), so we poll
/1.0/async/status/<key> and fetch /1.0/async/data/<key>.
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

import pandas as pd

from measured_common import CACHE, Airports, Http, log, months_between, ym_add

SOURCE_ID = "eu_eurostat_avia_par"
DIR = CACHE / "eurostat"
TOC_URL = "https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en"
SDMX = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"
ASYNC = "https://ec.europa.eu/eurostat/api/dissemination/1.0/async"
MEASURE = "PAS_BRD"   # flight-stage basis; "PAS_CRD" = on-flight origin/destination basis
# Eurostat ICAO codes that OurAirports does not know
ICAO_FIX = {
    "LSFB": "BSL",  # EuroAirport Basel-Mulhouse, Swiss-side code (OurAirports: LFSB)
}
TRA_MEAS = ["PAS_CRD_ARR", "PAS_CRD_DEP", "PAS_BRD_ARR", "PAS_BRD_DEP",
            "ST_PAS_ARR", "ST_PAS_DEP", "CAF_PAS_ARR", "CAF_PAS_DEP"]
# The UK dataset stops in 2019 (UK left the EU); the CAA source covers the UK.
SKIP = {"uk"}
MAX_STALENESS_MONTHS = 24   # ignore a country whose data end this far behind the newest

META = {
    "id": SOURCE_ID,
    "name": "Eurostat air passenger transport routes between partner airports and main airports (avia_par_*)",
    "publisher": "Eurostat (European Commission)",
    "url": "https://ec.europa.eu/eurostat/web/transport/database",
    "download": SDMX + "/avia_par_<cc>/M.PAS+SEAT+FLIGHT.<tra_meas>./?startPeriod=...&format=SDMX-CSV",
    "license": "Eurostat copyright/licence policy: re-use permitted with acknowledgement of the source "
               "(CC BY 4.0 equivalent), https://ec.europa.eu/eurostat/about-us/policies/copyright",
    "measure": f"{MEASURE}_DEP (reporting airport -> partner) and {MEASURE}_ARR (partner -> reporting airport); "
               "PAS_BRD = passengers on board per flight stage (comparable to T-100 Segment; PAS_CRD, the "
               "on-flight origin/destination count, creates non-nonstop pairs on multi-stop flights). "
               "seats = ST_PAS_DEP/ARR, departures = CAF_PAS_DEP/ARR (commercial passenger flights).",
    "directional": True,
    "coverage": "Routes of the 'main airports' (>150k passengers/year) of each reporting country, to every "
                "partner airport worldwide. Pairs between two reporting airports are reported by both ends "
                "(reporter column = <country>:<reporting airport>).",
}


def _toc(http: Http) -> pd.DataFrame:
    p = http.fetch(TOC_URL, DIR / "toc.txt", max_age_days=1)
    toc = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False)
    toc.columns = [c.strip() for c in toc.columns]
    toc["code"] = toc["code"].str.strip()
    t = toc[toc.code.str.fullmatch(r"avia_par_[a-z]{2}") & (toc["type"].str.strip() == "dataset")].copy()
    return t.drop_duplicates("code")


def _end_month(data_end: str) -> str | None:
    """'2024-Q4' -> '2024-12', '2024-11' -> '2024-11', '2024' -> '2024-12'."""
    s = data_end.strip()
    if m := re.fullmatch(r"(\d{4})-Q([1-4])", s):
        return f"{m.group(1)}-{int(m.group(2)) * 3:02d}"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}", s):
        return f"{s}-12"
    return None


def _get_sdmx_csv(http: Http, url: str, dest: Path) -> Path:
    if dest.exists() and not http.refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"  downloading {url[:160]}")
    r = http.request("GET", url, timeout=600)
    if r.status_code == 404:
        raise FileNotFoundError(url)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")
    body = r.content
    if body.lstrip().startswith(b"<?xml") and b"queued" in body:
        key = re.search(rb"<id>([^<]+)</id>", body).group(1).decode()
        log(f"  Eurostat queued the extraction (key {key}); polling")
        for attempt in range(60):
            time.sleep(10)
            st = http.request("GET", f"{ASYNC}/status/{key}", timeout=120).text
            status = re.search(r"<ns1:status>([A-Z_]+)</ns1:status>", st)
            status = status.group(1) if status else "?"
            if status in ("AVAILABLE", "SUBMITTED", "PROCESSING") or attempt % 6 == 5:
                d = http.request("GET", f"{ASYNC}/data/{key}", timeout=600)
                if d.status_code == 200 and d.content.startswith(b"DATAFLOW"):
                    body = d.content
                    break
            if status in ("EXPIRED", "UNKNOWN_REQUEST"):
                raise RuntimeError(f"Eurostat async {key}: {status}")
        else:
            raise RuntimeError(f"Eurostat async {key} never became available")
    if not body.startswith(b"DATAFLOW"):
        raise RuntimeError(f"unexpected Eurostat response: {body[:200]!r}")
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return dest


def _read(path: Path) -> pd.DataFrame:
    """Rows with a value, plus confidential rows (CONF_STATUS 'C', no value)."""
    d = pd.read_csv(path, dtype={"TIME_PERIOD": str, "airp_pr": str, "CONF_STATUS": str},
                    keep_default_na=False, na_values=[""],
                    usecols=["freq", "unit", "tra_meas", "airp_pr", "TIME_PERIOD", "OBS_VALUE",
                             "OBS_FLAG", "CONF_STATUS"])
    d["conf"] = d.CONF_STATUS.fillna("").str.startswith("C")
    return d[d.OBS_VALUE.notna() | d.conf]


def fetch_country(http: Http, cc: str, row):
    """Latest 12 months of monthly data for one reporting country.

    Returns (monthly, annual, freq, period_start, period_end).  `annual` holds the
    calendar-year values for the window when the window is a calendar year (used
    to fill series whose monthly values are confidential); freq is "M", or "A"
    when a country has no monthly data at all.
    """
    end = _end_month(row["data end"])
    upd = re.sub(r"\D", "", row["last update of data"])  # e.g. 22102025
    ds = f"avia_par_{cc}"
    key = "+".join(TRA_MEAS)

    def get(freq, sp):
        url = f"{SDMX}/{ds}/{freq}.PAS+SEAT+FLIGHT.{key}./?startPeriod={sp}&format=SDMX-CSV"
        try:
            return _read(_get_sdmx_csv(http, url, DIR / f"{ds}_{freq}_{sp}_upd{upd}.csv"))
        except FileNotFoundError:   # 404 = no data for this selection
            return pd.DataFrame(columns=["tra_meas", "airp_pr", "TIME_PERIOD", "OBS_VALUE", "conf"])

    m = get("M", ym_add(end, -17))
    m = m[m.OBS_VALUE.notna() | m.conf]
    if m.OBS_VALUE.notna().any():
        last = m[m.OBS_VALUE.notna()].TIME_PERIOD.max()
        start, end = ym_add(last, -11), last
        m = m[(m.TIME_PERIOD >= start) & (m.TIME_PERIOD <= end)].copy()
        m["ym"] = m.TIME_PERIOD
        annual = pd.DataFrame()
        if start.endswith("-01"):
            annual = get("A", start[:4])
            annual = annual[annual.TIME_PERIOD == start[:4]].copy()
        return m, annual, "M", start, end
    a = get("A", str(int(end[:4]) - 1))
    a = a[a.OBS_VALUE.notna()]
    if a.empty:
        raise RuntimeError(f"{ds}: no monthly or annual data")
    y = a.TIME_PERIOD.max()
    return a[a.TIME_PERIOD == y].assign(ym=None), pd.DataFrame(), "A", f"{y}-01", f"{y}-12"


def run(http: Http, airports: Airports):
    toc = _toc(http)
    toc["end_month"] = toc["data end"].map(_end_month)
    newest = toc.end_month.dropna().max()
    frames, per_country, skipped = [], {}, {}
    for _, row in toc.sort_values("code").iterrows():
        cc = row["code"][-2:]
        if cc in SKIP:
            skipped[cc] = f"skipped (data end {row['data end'].strip()}; UK comes from CAA)"
            continue
        if not row.end_month or row.end_month < ym_add(newest, -MAX_STALENESS_MONTHS):
            skipped[cc] = f"stale (data end {row['data end'].strip()})"
            continue
        try:
            m, annual, freq, start, end = fetch_country(http, cc, row)
        except Exception as e:
            log(f"  ! {row['code']}: {e}")
            skipped[cc] = f"error: {e}"
            continue
        # Series (route x direction) whose passenger values are confidential in some
        # month: use the calendar-year value instead when Eurostat publishes it.
        m["dir"] = m.tra_meas.str[-3:]
        conf = m[m.conf & (m.tra_meas.str[:-4] == MEASURE)][["airp_pr", "dir"]].drop_duplicates()
        info = {"freq": freq, "period_start": start, "period_end": end,
                "last_update": row["last update of data"].strip(),
                "confidential_series": int(len(conf))}
        if len(conf) and len(annual):
            annual = annual[annual.OBS_VALUE.notna()].copy()
            annual["dir"] = annual.tra_meas.str[-3:]
            has = annual[annual.tra_meas.str[:-4] == MEASURE][["airp_pr", "dir"]].drop_duplicates()
            fill = conf.merge(has)
            key_m = pd.MultiIndex.from_frame(m[["airp_pr", "dir"]])
            key_f = pd.MultiIndex.from_frame(fill)
            m = m[~key_m.isin(key_f)]
            add = annual[pd.MultiIndex.from_frame(annual[["airp_pr", "dir"]]).isin(key_f)].assign(ym=None)
            m = pd.concat([m, add], ignore_index=True)
            info["confidential_filled_from_annual"] = int(len(fill))
            info["annual_fill_passengers"] = float(add[add.tra_meas.str[:-4] == MEASURE].OBS_VALUE.sum())
        unfilled = conf.merge(m[m.OBS_VALUE.notna() & (m.tra_meas.str[:-4] == MEASURE)][["airp_pr", "dir"]]
                              .drop_duplicates(), how="left", indicator=True)
        unfilled = sorted(unfilled[unfilled._merge == "left_only"].airp_pr.unique())
        info["confidential_routes_without_data"] = len(unfilled)
        if unfilled:
            info["confidential_routes_without_data_examples"] = unfilled[:12]
        d = m[m.OBS_VALUE.notna()].drop(columns=["dir"]).copy()
        d["cc"] = cc.upper()
        d["freq"] = freq
        d["period_start"], d["period_end"] = start, end
        frames.append(d)
        per_country[cc.upper()] = info
        log(f"  {row['code']}: freq={freq} {start}..{end}  rows={len(d)}  confidential series={len(conf)}")
    d = pd.concat(frames, ignore_index=True)

    parts = d.airp_pr.str.extract(r"^([A-Z]{2})_([A-Z0-9]{4})_([A-Z]{2})_([A-Z0-9]{4})$")
    d["rep_icao"], d["par_icao"] = parts[1], parts[3]
    bad = d.rep_icao.isna()
    if bad.any():
        log(f"  {bad.sum()} rows with unparseable airp_pr, e.g. {d[bad].airp_pr.unique()[:5]}")
    d = d[~bad]
    d["direction"] = d.tra_meas.str[-3:]          # DEP / ARR
    d["kind"] = d.tra_meas.str[:-4]               # PAS_CRD / PAS_BRD / ST_PAS / CAF_PAS
    d["ym"] = d.ym.fillna("ANNUAL")  # calendar-year values (confidential monthly series)
    wide = d.pivot_table(index=["cc", "freq", "period_start", "period_end", "rep_icao", "par_icao",
                                "direction", "ym"],
                         columns="kind", values="OBS_VALUE", aggfunc="sum").reset_index()
    wide["ym"] = wide.ym.where(wide.ym != "ANNUAL", None)
    for k in ("PAS_CRD", "PAS_BRD", "ST_PAS", "CAF_PAS"):
        if k not in wide:
            wide[k] = float("nan")

    stats = {"per_country": per_country, "skipped": skipped,
             "passengers_crd_total": float(wide.PAS_CRD.sum()),
             "passengers_brd_total": float(wide.PAS_BRD.sum()),
             "measure_used": MEASURE}
    wide = wide[wide[MEASURE].fillna(0) > 0]

    def to_iata(icao):
        return ICAO_FIX.get(icao) or airports.from_icao(icao)

    rep_iata = wide.rep_icao.map(to_iata)
    par_iata = wide.par_icao.map(to_iata)
    dep = wide.direction == "DEP"
    out = pd.DataFrame({
        "source": SOURCE_ID,
        "reporter": wide.cc + ":" + rep_iata.fillna(wide.rep_icao),
        "origin_raw": wide.rep_icao.where(dep, wide.par_icao),
        "dest_raw": wide.par_icao.where(dep, wide.rep_icao),
        "origin": rep_iata.where(dep, par_iata),
        "dest": par_iata.where(dep, rep_iata),
        "ym": wide.ym,
        "passengers": wide[MEASURE],
        "seats": wide.ST_PAS,
        "departures": wide.CAF_PAS,
        "carrier": "",
        "directional": 1,
        "period_start": wide.period_start, "period_end": wide.period_end,
    })
    # Reporting airports without an IATA code keep their ICAO code in `reporter`;
    # pandas `where` leaves NaN for unmapped codes, which aggregate() counts as unmapped.
    out["origin"] = out.origin.where(out.origin.notna(), None)
    out["dest"] = out.dest.where(out.dest.notna(), None)

    meta = dict(META, period_start=min(v["period_start"] for v in per_country.values()),
                period_end=max(v["period_end"] for v in per_country.values()),
                per_country=per_country, skipped=skipped,
                note="Each reporting country has its own 12-month window (period_start/period_end per row). "
                     "As of 2026-10 Eurostat route data end in 2024-12 for most countries (annual release "
                     "~10 months after year end).")
    return out, meta, stats
