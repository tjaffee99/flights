#!/usr/bin/env python3
"""Build flights/pipeline/build/measured.csv from official passenger statistics.

Usage (from anywhere; -I keeps the cache dir off sys.path):
    python3 -I pipeline/build_measured.py                 # all sources, use cache
    python3 -I pipeline/build_measured.py --refresh       # re-download everything
    python3 -I pipeline/build_measured.py --sources us,uk # subset (others kept
                                                                  # from the previous build)
Downloads go to flights/pipeline/cache/<source>/, outputs to flights/pipeline/build/.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # the pipeline dir only (never the cache)

import pandas as pd  # noqa: E402

from measured_common import (BUILD, OUTPUT_COLUMNS, Airports, Http, aggregate,  # noqa: E402
                             log, today, top_routes, write_json)

SOURCES = {
    "us": "measured_us_bts",
    "eu": "measured_eurostat",
    "uk": "measured_uk_caa",
    "br": "measured_br_anac",
    "au": "measured_au_bitre",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=",".join(SOURCES), help="comma list of: " + ",".join(SOURCES))
    ap.add_argument("--refresh", action="store_true", help="re-download cached files")
    args = ap.parse_args()
    wanted = [s.strip() for s in args.sources.split(",") if s.strip()]

    http = Http(delay=1.0, refresh=args.refresh)
    airports = Airports()
    csv_path = BUILD / "measured.csv"
    json_path = BUILD / "measured_sources.json"

    # keep previous results for sources not rebuilt this run
    prev_rows = pd.read_csv(csv_path, dtype=str, keep_default_na=False) if csv_path.exists() else None
    prev_meta = json.loads(json_path.read_text()) if json_path.exists() else {"sources": []}

    outs, metas, failed = [], [], {}
    for key in wanted:
        modname = SOURCES[key]
        log(f"== {key}: {modname}")
        try:
            mod = __import__(modname)
            res = mod.run(http, airports)
            for monthly, meta, src_stats in (res if isinstance(res, list) else [res]):
                out, agg_stats = aggregate(monthly)
                meta = dict(meta)
                meta.update(rows=agg_stats["rows"], total_passengers=agg_stats["passengers"],
                            source_stats=src_stats, mapping=agg_stats, built=today())
                outs.append(out)
                metas.append(meta)
                log(f"   {meta['id']}: {meta['rows']} rows, {meta['total_passengers']:,} passengers, "
                    f"unmapped {agg_stats['unmapped_passengers']:,.0f} pax")
        except Exception as e:  # keep going with other sources; keep their previous rows
            traceback.print_exc()
            failed[key] = f"{type(e).__name__}: {e}"

    built_ids = {m["id"] for m in metas}
    final = pd.concat([o.astype(str).replace({"<NA>": "", "nan": ""}) for o in outs], ignore_index=True) \
        if outs else pd.DataFrame(columns=OUTPUT_COLUMNS)
    meta_list = list(metas)
    if prev_rows is not None:
        keep = prev_rows[~prev_rows.source.isin(built_ids)]
        final = pd.concat([keep, final], ignore_index=True)
        meta_list = [m for m in prev_meta.get("sources", []) if m["id"] not in built_ids] + meta_list
    final = final.sort_values(["source", "reporter", "origin_iata", "dest_iata"], kind="stable")
    BUILD.mkdir(parents=True, exist_ok=True)
    final[OUTPUT_COLUMNS].to_csv(csv_path, index=False)
    write_json(json_path, {
        "description": "Official passengers-per-airport-pair statistics, latest 12 published months per source. "
                       "See MEASURED.md.",
        "built": today(),
        "columns": OUTPUT_COLUMNS,
        "sources": sorted(meta_list, key=lambda m: m["id"]),
        "failed_this_run": failed,
    })
    log(f"wrote {csv_path} ({len(final)} rows) and {json_path}")

    # quick report
    for o, m in zip(outs, metas):
        print(f"\n### {m['id']}  {m.get('period_start')}..{m.get('period_end')}  rows={m['rows']}  "
              f"pax={m['total_passengers']:,}")
        print(top_routes(o, 15).to_string(index=False))
        print("top unmapped:", m["mapping"]["top_unmapped_codes"][:10])
    if failed:
        print("\nFAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
