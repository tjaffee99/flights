"""Australia: BITRE airline activity city-pair data (republished on data.gov.au).

bitre.gov.au itself refuses connections from this environment, but BITRE's
monthly CSVs are mirrored on data.gov.au (CKAN):
  International Airlines - Traffic by city pairs   (city_pairs.csv)
      Australian port <-> foreign port, Passengers_In / Passengers_Out per month.
      Scheduled international airlines; "uplift/discharge within a single flight
      number", so a through flight (e.g. QF1 Sydney-Singapore-London) also yields
      a Sydney-London pair.
  Domestic Airlines - Top Routes and Totals        (dom_citypairs_web.csv)
      Top ~70 domestic city pairs, Passenger_Trips both directions combined,
      Aircraft_Trips, Seats.
Ports are *city names*; CITY_IATA below maps them (multi-airport cities to the
airport used by Australian services).  Two output sources: au_bitre_intl
(directional) and au_bitre_dom (non-directional).
"""
from __future__ import annotations

import pandas as pd

from measured_common import CACHE, Airports, Http, log, window_12

DIR = CACHE / "au_bitre"
CKAN = "https://data.gov.au/data/api/3/action/package_show?id={id}"
DATASETS = {
    "intl": ("international-airlines-traffic-by-city-pairs", "city_pairs.csv"),
    "dom": ("domestic-airlines-top-routes-and-totals", "dom_citypairs_web.csv"),
}

AU = {
    "ADELAIDE": "ADL", "ALBURY": "ABX", "ALICE SPRINGS": "ASP", "ARMIDALE": "ARM", "AYERS ROCK": "AYQ",
    "BALLINA": "BNK", "BRISBANE": "BNE", "BROOME": "BME", "BUNDABERG": "BDB", "CAIRNS": "CNS",
    "CANBERRA": "CBR", "COFFS HARBOUR": "CFS", "DARWIN": "DRW", "DEVONPORT": "DPO", "DUBBO": "DBO",
    "EMERALD": "EMD", "GERALDTON": "GET", "GLADSTONE": "GLT", "GOLD COAST": "OOL",
    "HAMILTON ISLAND": "HTI", "HOBART": "HBA", "KALGOORLIE": "KGI", "KARRATHA": "KTA",
    "LAUNCESTON": "LST", "MACKAY": "MKY", "MELBOURNE": "MEL", "MILDURA": "MQL", "MORANBAH": "MOV",
    "MOUNT ISA": "ISA", "NEWCASTLE": "NTL", "NEWMAN": "ZNE", "PERTH": "PER", "PORT HEDLAND": "PHE",
    "PORT LINCOLN": "PLO", "PORT MACQUARIE": "PQQ", "PROSERPINE": "PPP", "ROCKHAMPTON": "ROK",
    "SUNSHINE COAST": "MCY", "SYDNEY": "SYD", "TAMWORTH": "TMW", "TOWNSVILLE": "TSV",
    "WAGGA WAGGA": "WGA", "AVALON": "AVV", "CHRISTMAS ISLAND": "XCH", "NORFOLK ISLAND": "NLK",
    "COCOS ISLANDS": "CCK", "LEARMONTH": "LEA", "BALLARAT": "", "KING ISLAND": "KNS",
}
# Foreign ports (upper-cased).  Multi-airport cities -> airport used by Australian services.
FOREIGN = {
    "SINGAPORE": "SIN", "DENPASAR": "DPS", "AUCKLAND": "AKL", "HONG KONG": "HKG", "KUALA LUMPUR": "KUL",
    "DUBAI": "DXB", "TOKYO": "NRT", "DOHA": "DOH", "BANGKOK": "BKK", "GUANGZHOU": "CAN",
    "CHRISTCHURCH": "CHC", "NADI": "NAN", "LOS ANGELES": "LAX", "HO CHI MINH CITY": "SGN",
    "QUEENSTOWN": "ZQN", "SEOUL": "ICN", "MANILA": "MNL", "SHANGHAI": "PVG", "WELLINGTON": "WLG",
    "SAN FRANCISCO": "SFO", "TAIPEI": "TPE", "ABU DHABI": "AUH", "JAKARTA": "CGK", "VANCOUVER": "YVR",
    "SANTIAGO": "SCL", "LONDON": "LHR", "OSAKA": "KIX", "PHUKET": "HKT", "DALLAS": "DFW", "XIAMEN": "XMN",
    "NEW DELHI": "DEL", "DELHI": "DEL", "JOHANNESBURG": "JNB", "COLOMBO": "CMB", "HONOLULU": "HNL",
    "BEIJING": "PEK", "CHENGDU": "TFU", "PORT MORESBY": "POM", "ISTANBUL": "IST", "PORT VILA": "VLI",
    "HANOI": "HAN", "HAIKOU": "HAK", "NOUMEA": "NOU", "HAMILTON": "HLZ", "BANGALORE": "BLR",
    "BENGALURU": "BLR", "SHENZHEN": "SZX", "NANJING": "NKG", "HANGZHOU": "HGH",
    "BANDAR SERI BEGAWAN": "BWN", "RAROTONGA": "RAR", "APIA": "APW", "MAURITIUS": "MRU", "PARIS": "CDG",
    "HONIARA": "HIR", "HOUSTON": "IAH", "QINGDAO": "TAO", "DUNEDIN": "DUD", "TORONTO": "YYZ",
    "ROME": "FCO", "DILI": "DIL", "NEW YORK": "JFK", "WUHAN": "WUH", "CEBU": "CEB", "TONGATAPU": "TBU",
    "NAURU": "INU", "CHONGQING": "CKG", "SAPPORO": "CTS", "XI'AN": "XIY", "ESPIRITU SANTO": "SON",
    "PALAU": "ROR", "JINAN": "TNA", "ZHENGZHOU": "CGO", "MUNDA": "MUA", "TIANJIN": "TSN",
    "BRUSSELS": "BRU", "KUCHING": "KCH", "KOTA KINABALU": "BKI", "RIYADH": "RUH", "ANCHORAGE": "ANC",
    "OAKLAND": "OAK", "CHICAGO": "ORD", "LOUISVILLE": "SDF", "MUMBAI": "BOM", "CHENNAI": "MAA",
    "FRANKFURT": "FRA", "SEATTLE": "SEA", "TEL AVIV": "TLV", "KOLKATA": "CCU", "HYDERABAD": "HYD",
    "AMRITSAR": "ATQ", "SURABAYA": "SUB", "YOGYAKARTA": "YIA", "LOMBOK": "LOP", "MAKASSAR": "UPG",
    "NUKU'ALOFA": "TBU", "PAPEETE": "PPT", "SUVA": "SUV", "TARAWA": "TRW", "LAE": "LAE",
    "PHNOM PENH": "KTI", "SIEM REAP": "SAI", "YANGON": "RGN", "KATHMANDU": "KTM", "DHAKA": "DAC",
    "KARACHI": "KHI", "LAHORE": "LHE", "ISLAMABAD": "ISB", "MALE": "MLE", "CAIRO": "CAI",
    "NAIROBI": "NBO", "ADDIS ABABA": "ADD", "BAHRAIN": "BAH", "MUSCAT": "MCT", "KUWAIT": "KWI",
    "JEDDAH": "JED", "AMMAN": "AMM", "BEIRUT": "BEY", "ATHENS": "ATH", "MILAN": "MXP", "AMSTERDAM": "AMS",
    "ZURICH": "ZRH", "VIENNA": "VIE", "MUNICH": "MUC", "MADRID": "MAD", "BARCELONA": "BCN",
    "BUENOS AIRES": "EZE", "SAO PAULO": "GRU", "MEXICO CITY": "MEX", "MONTREAL": "YUL",
    "PAGO PAGO": "PPG", "NIUE": "IUE", "KIRITIMATI": "CXI", "GUAM": "GUM", "SAIPAN": "SPN",
    "MACAU": "MFM", "OKINAWA": "OKA", "NAGOYA": "NGO", "FUKUOKA": "FUK", "BUSAN": "PUS",
    "KAOHSIUNG": "KHH", "CLARK": "CRK", "DAVAO": "DVO", "PENANG": "PEN", "LANGKAWI": "LGK",
    "SHENYANG": "SHE", "KUNMING": "KMG", "CHANGSHA": "CSX", "FUZHOU": "FOC", "NANNING": "NNG",
    "SANYA": "SYX", "HARBIN": "HRB", "DALIAN": "DLC", "NINGBO": "NGB", "WENZHOU": "WNZ",
}

# (Australian port, foreign port) pairs where the city's other airport is the right one
PAIR_OVERRIDE = {
    ("SYDNEY", "TOKYO"): "HND",   # QF/JL/NH all fly SYD-HND; NRT is mostly Jetstar from QLD/MEL
}

META_COMMON = {
    "publisher": "Bureau of Infrastructure and Transport Research Economics (BITRE), Australia; via data.gov.au",
    "license": "Creative Commons Attribution 4.0 International (CC BY 4.0) per data.gov.au; attribute BITRE.",
}


def _fetch(http: Http, key: str):
    pkg, fname = DATASETS[key]
    meta = http.fetch(CKAN.format(id=pkg), DIR / f"{pkg}.json", max_age_days=1)
    import json
    res = json.loads(meta.read_text())["result"]["resources"]
    url = next(r["url"] for r in res if r["url"].endswith("/" + fname))
    stamp = next((r.get("last_modified") or r.get("metadata_modified") or "") for r in res
                 if r["url"].endswith("/" + fname))[:10]
    path = http.fetch(url, DIR / f"{fname[:-4]}_{stamp}.csv")
    return path, url, f"https://data.gov.au/data/dataset/{pkg}"


def _map(name: str, table: dict, airports: Airports):
    v = table.get(str(name).strip().upper())
    return v if v and airports.is_iata(v) else None


def run(http: Http, airports: Airports):
    results = []
    # ---------------- international ----------------
    path, url, page = _fetch(http, "intl")
    i = pd.read_csv(path, dtype=str, keep_default_na=False)
    i["ym"] = i.Year.str.zfill(4) + "-" + i.Month_num.str.zfill(2)
    for c in ("Passengers_In", "Passengers_Out"):
        i[c] = pd.to_numeric(i[c], errors="coerce").fillna(0)
    start, end = window_12(i[(i.Passengers_In + i.Passengers_Out) > 0].ym.max())
    i = i[(i.ym >= start) & (i.ym <= end)]
    au = i.AustralianPort.map(lambda n: _map(n, AU, airports))
    fo = i.ForeignPort.map(lambda n: _map(n, FOREIGN, airports))
    ov = [PAIR_OVERRIDE.get((a.strip().upper(), f.strip().upper())) for a, f in zip(i.AustralianPort, i.ForeignPort)]
    fo = fo.where([v is None for v in ov], ov)
    fo_raw = i.Country + "/" + i.ForeignPort
    rows = []
    for direction, col in (("out", "Passengers_Out"), ("in", "Passengers_In")):
        out = direction == "out"
        rows.append(pd.DataFrame({
            "source": "au_bitre_intl", "reporter": "AU",
            "origin_raw": i.AustralianPort if out else fo_raw,
            "dest_raw": fo_raw if out else i.AustralianPort,
            "origin": au if out else fo, "dest": fo if out else au,
            "ym": i.ym, "passengers": i[col], "seats": float("nan"), "departures": float("nan"),
            "carrier": "", "directional": 1, "period_start": start, "period_end": end,
        }))
    m = pd.concat(rows, ignore_index=True)
    for c in ("origin", "dest"):
        m[c] = m[c].where(m[c].notna(), None)
    results.append((m, dict(META_COMMON, id="au_bitre_intl",
                            name="BITRE International Airlines - Traffic by city pairs",
                            url=page, download=url, period_start=start, period_end=end,
                            measure="Revenue passengers inbound (Passengers_In) and outbound (Passengers_Out) "
                                    "between an Australian port and a foreign port, uplift/discharge within a "
                                    "single flight number (scheduled international airlines only).",
                            directional=True,
                            coverage="All scheduled international services to/from Australia. City-level ports "
                                     "mapped to one airport each (Tokyo->NRT, London->LHR, ...). Through-flight "
                                     "pairs (e.g. SYD-LHR on QF1 via SIN) are included as published."),
                    {"window_passengers": float(m.passengers.sum())}))
    log(f"[au_bitre_intl] window {start}..{end}")

    # ---------------- domestic ----------------
    path, url, page = _fetch(http, "dom")
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    d["ym"] = d.Year.str.zfill(4) + "-" + d.Month_num.str.zfill(2)
    for c in ("Passenger_Trips", "Aircraft_Trips", "Seats"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    start, end = window_12(d[d.Passenger_Trips > 0].ym.max())
    d = d[(d.ym >= start) & (d.ym <= end)]
    m = pd.DataFrame({
        "source": "au_bitre_dom", "reporter": "AU",
        "origin_raw": d.City1, "dest_raw": d.City2,
        "origin": d.City1.map(lambda n: _map(n, AU, airports)),
        "dest": d.City2.map(lambda n: _map(n, AU, airports)),
        "ym": d.ym, "passengers": d.Passenger_Trips.fillna(0),
        # Aircraft_Trips / Seats are two-way totals for the city pair
        "seats": d.Seats, "departures": d.Aircraft_Trips,
        "carrier": "", "directional": 0, "period_start": start, "period_end": end,
    })
    for c in ("origin", "dest"):
        m[c] = m[c].where(m[c].notna(), None)
    results.append((m, dict(META_COMMON, id="au_bitre_dom",
                            name="BITRE Domestic Airlines - Top Routes and Totals (city pairs)",
                            url=page, download=url, period_start=start, period_end=end,
                            measure="Revenue passenger trips per domestic city pair, both directions combined; "
                                    "seats and aircraft trips are two-way totals too.",
                            directional=False,
                            coverage="Top ~70 Australian domestic city pairs by passengers (regular public "
                                     "transport); smaller regional routes are not included."),
                    {"window_passengers": float(m.passengers.sum())}))
    log(f"[au_bitre_dom] window {start}..{end}")
    return results
