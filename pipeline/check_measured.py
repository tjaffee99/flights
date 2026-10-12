#!/usr/bin/env python3
"""Sanity checks for build/measured.csv (run after build_measured.py).

    python3 -I pipeline/check_measured.py
"""
from __future__ import annotations

import glob
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pandas as pd  # noqa: E402

from measured_common import BUILD, CACHE, Airports, top_routes  # noqa: E402

# Rough published references (both directions, segment passengers) for plausibility only.
REFERENCE = {
    "us_bts_t100": "US T-100 all carriers ~1.0-1.1 bn segment passengers/yr (domestic ~0.85 bn + intl ~0.25 bn)",
    "eu_eurostat_avia_par": "EU+EFTA+candidates ~1.0-1.1 bn passengers/yr, but pairs between two reporting "
                            "airports are counted by both ends, so the sum is well above that",
    "uk_caa": "UK terminal passengers ~300 m/yr; intl counted once, domestic pairs once (~20 m)",
    "br_anac": "Brazil ~95-100 m domestic + ~25-30 m international segment passengers/yr",
    "au_bitre_intl": "Australia ~40-45 m international passengers/yr",
    "au_bitre_dom": "Australia ~60-65 m domestic passengers/yr (top city pairs only here)",
}


def main():
    d = pd.read_csv(BUILD / "measured.csv", keep_default_na=False, dtype={"passengers": "int64"})
    meta = {m["id"]: m for m in json.loads((BUILD / "measured_sources.json").read_text())["sources"]}
    ap = Airports()
    d["oc"] = d.origin_iata.map(ap.iata_country)
    d["dc"] = d.dest_iata.map(ap.iata_country)

    print("== Sources")
    for src, g in d.groupby("source"):
        m = meta.get(src, {})
        print(f"{src:24s} {g.period_start.min()}..{g.period_end.max()}  rows={len(g):6d}  "
              f"pax={g.passengers.sum():>14,}  directional={sorted(g.directional.unique().tolist())}  "
              f"unmapped_pax={m.get('mapping', {}).get('unmapped_passengers', 0):,.0f}")
        print(f"{'':24s} reference: {REFERENCE.get(src, '-')}")

    print("\n== MIA-CCS in T-100 (output rows)")
    us = d[d.source == "us_bts_t100"]
    print(us[((us.origin_iata == "MIA") & (us.dest_iata == "CCS")) | ((us.origin_iata == "CCS") & (us.dest_iata == "MIA"))]
          [["origin_iata", "dest_iata", "passengers", "seats", "departures", "months_with_traffic",
            "first_month", "last_month", "carriers"]].to_string(index=False))
    zips = sorted(glob.glob(str(CACHE / "bts_t100" / "t100_segment_all_carrier_*.zip")))
    if zips:
        raw = pd.concat([pd.read_csv(zipfile.ZipFile(z).open(zipfile.ZipFile(z).namelist()[0]),
                                     keep_default_na=False, na_values=[""]) for z in zips])
        x = raw[raw.ORIGIN.isin(["MIA", "CCS"]) & raw.DEST.isin(["MIA", "CCS"]) & (raw.PASSENGERS > 0)]
        print("monthly, all service classes (F = scheduled, L = charter):")
        print(x.groupby(["YEAR", "MONTH", "CLASS", "UNIQUE_CARRIER"]).PASSENGERS.sum().unstack(["CLASS", "UNIQUE_CARRIER"])
              .fillna(0).astype(int).to_string())

    print("\n== US-Russia rows in T-100 output (expect none)")
    ru = us[(us.oc == "RU") | (us.dc == "RU")]
    print(len(ru), "rows", ru[["origin_iata", "dest_iata", "passengers"]].head().to_string(index=False) if len(ru) else "")
    print("US-touching rows with no US/territory endpoint:",
          int((~us.oc.isin(["US", "PR", "VI", "GU", "AS", "MP", "UM"]) &
               ~us.dc.isin(["US", "PR", "VI", "GU", "AS", "MP", "UM"])).sum()))

    print("\n== Top 15 airport pairs per source (both directions; max over reporters)")
    for src, g in d.groupby("source"):
        t = top_routes(g, 15)
        print(f"-- {src}")
        print("   " + ", ".join(f"{a}-{b} {p / 1e6:.2f}M" for a, b, p in t.itertuples(index=False)))

    print("\n== Overlap checks (same pair in two sources; different periods!)")
    pairs = [("JFK", "LHR"), ("LHR", "LAX"), ("DUB", "LHR"), ("AMS", "LHR"), ("GRU", "MIA"), ("LIS", "GRU"),
             ("LAX", "SYD"), ("CDG", "JFK"), ("MAD", "MIA")]
    for a, b in pairs:
        x = d[((d.origin_iata == a) & (d.dest_iata == b)) | ((d.origin_iata == b) & (d.dest_iata == a))]
        s = x.groupby(["source", "reporter"]).passengers.sum()
        print(f"{a}-{b}: " + "; ".join(f"{k[0]}[{k[1]}] {v:,}" for k, v in s.items()))


if __name__ == "__main__":
    main()
