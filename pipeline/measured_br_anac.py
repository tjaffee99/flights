"""Brazil: ANAC "Dados Estatisticos do Transporte Aereo" (flight-stage statistics).

One consolidated CSV (all years since 2000, ~360 MB, refreshed daily by ANAC):
  https://sistemas.anac.gov.br/dadosabertos/Voos e operacoes aereas/Dados Estatisticos do Transporte Aereo/Dados_Estatisticos.csv
Rows = airline x month x flight stage (origin ICAO -> destination ICAO), with
PASSAGEIROS_PAGOS + PASSAGEIROS_GRATIS, ASSENTOS (seats), DECOLAGENS (departures).
We keep GRUPO_DE_VOO == REGULAR (scheduled) and stages touching Brazil.
The CSV is cached; pass --refresh to pick up newer months.
"""
from __future__ import annotations

import pandas as pd

from measured_common import CACHE, Airports, Http, log, window_12

SOURCE_ID = "br_anac"
DIR = CACHE / "br_anac"
URL = ("https://sistemas.anac.gov.br/dadosabertos/Voos%20e%20opera%C3%A7%C3%B5es%20a%C3%A9reas/"
       "Dados%20Estat%C3%ADsticos%20do%20Transporte%20A%C3%A9reo/Dados_Estatisticos.csv")
COLS = ["EMPRESA_SIGLA", "ANO", "MES", "AEROPORTO_DE_ORIGEM_SIGLA", "AEROPORTO_DE_DESTINO_SIGLA",
        "AEROPORTO_DE_ORIGEM_PAIS", "AEROPORTO_DE_DESTINO_PAIS", "GRUPO_DE_VOO",
        "PASSAGEIROS_PAGOS", "PASSAGEIROS_GRATIS", "DECOLAGENS", "ASSENTOS"]

META = {
    "id": SOURCE_ID,
    "name": "ANAC Dados Estatisticos do Transporte Aereo (flight-stage statistics)",
    "publisher": "Agencia Nacional de Aviacao Civil (ANAC), Brazil",
    "url": "https://www.gov.br/anac/pt-br/assuntos/dados-e-estatisticas/dados-estatisticos",
    "download": URL,
    "license": "Brazilian open data (dados abertos), Open Database License (ODbL) per dados.gov.br listing; "
               "attribute ANAC.",
    "measure": "Paid + free passengers per flight stage (etapa), GRUPO_DE_VOO = REGULAR (scheduled) only; "
               "seats = ASSENTOS, departures = DECOLAGENS. Carriers are ICAO 3-letter airline codes.",
    "directional": True,
    "coverage": "All scheduled flight stages touching Brazil, Brazilian and foreign airlines.",
}


def run(http: Http, airports: Airports):
    path = http.fetch_large(URL, DIR / "Dados_Estatisticos.csv")
    parts = []
    for ch in pd.read_csv(path, sep=";", encoding="utf-8-sig", skiprows=1, usecols=COLS, dtype=str,
                          keep_default_na=False, chunksize=500_000):
        ch = ch[(ch.GRUPO_DE_VOO == "REGULAR") & (pd.to_numeric(ch.ANO, errors="coerce") >= 2015)]
        parts.append(ch)
    d = pd.concat(parts, ignore_index=True)
    for c in ("PASSAGEIROS_PAGOS", "PASSAGEIROS_GRATIS", "DECOLAGENS", "ASSENTOS"):
        d[c] = pd.to_numeric(d[c].str.replace(",", "."), errors="coerce").fillna(0)
    d["ym"] = d.ANO.str.zfill(4) + "-" + d.MES.str.zfill(2)
    d["pax"] = d.PASSAGEIROS_PAGOS + d.PASSAGEIROS_GRATIS
    end = d[d.pax > 0].ym.max()
    start, end = window_12(end)
    d = d[(d.ym >= start) & (d.ym <= end)]
    stats = {"rows_in_window": int(len(d)),
             "passengers_by_month": {k: int(v) for k, v in d.groupby("ym").pax.sum().items()}}
    br = (d.AEROPORTO_DE_ORIGEM_PAIS == "BRASIL") | (d.AEROPORTO_DE_DESTINO_PAIS == "BRASIL")
    stats["dropped_non_brazil_stage_passengers"] = float(d[~br].pax.sum())
    d = d[br]
    o = d.AEROPORTO_DE_ORIGEM_SIGLA.str.strip().str.upper()
    t = d.AEROPORTO_DE_DESTINO_SIGLA.str.strip().str.upper()
    out = pd.DataFrame({
        "source": SOURCE_ID, "reporter": "BR",
        "origin_raw": o, "dest_raw": t,
        "origin": o.map(airports.from_icao), "dest": t.map(airports.from_icao),
        "ym": d.ym, "passengers": d.pax, "seats": d.ASSENTOS, "departures": d.DECOLAGENS,
        "carrier": d.EMPRESA_SIGLA.str.strip(), "directional": 1,
        "period_start": start, "period_end": end,
    })
    out["origin"] = out.origin.where(out.origin.notna(), None)
    out["dest"] = out.dest.where(out.dest.notna(), None)
    log(f"[{SOURCE_ID}] window {start}..{end}")
    return out, dict(META, period_start=start, period_end=end), stats
