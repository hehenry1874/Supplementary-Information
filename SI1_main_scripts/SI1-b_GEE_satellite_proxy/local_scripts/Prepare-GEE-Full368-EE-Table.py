"""
Build full-panel EE table (368 ports) for Google Earth Engine Table Upload.

Same column layout as gee/ports_wpi_pilot30_ee_table.csv:
  longitude, latitude, port_id, port_name, country, iso2, unlocode,
  wpi_index_no, top50_rank  (here top50_rank = sort_index from double_batch master)

Input:
  - double_batch_ports_master.csv
  - scripts/ports_wpi.csv

Output:
  - gee/ports_wpi_full_368_ee_table.csv       (rows with coordinates)
  - gee/ports_wpi_full_368_ee_table_unmatched.csv  (needs manual coords)
  - gee/ports_wpi_full_368_ee_match_report.csv     (match_note per port)

Usage:
  python scripts/Prepare-GEE-Full368-EE-Table.py
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "double_batch_ports_master.csv"
WPI = ROOT / "scripts" / "ports_wpi.csv"
OUT_EE = ROOT / "gee" / "ports_wpi_full_368_ee_table.csv"
OUT_UNMATCHED = ROOT / "gee" / "ports_wpi_full_368_ee_table_unmatched.csv"
OUT_REPORT = ROOT / "gee" / "ports_wpi_full_368_ee_match_report.csv"


def norm_name(s: str) -> str:
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    t = t.upper().strip()
    t = t.split(",")[0].strip()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def iso2_from_unlocode(u: str) -> str | None:
    u = str(u).strip().upper()
    if len(u) < 2:
        return None
    return u[:2]


def name_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def ratio_best_wpi_row(
    iso2: str,
    display_name: str,
    wpi_by_country: dict[str, pd.DataFrame],
    min_ratio: float = 0.55,
) -> tuple[pd.Series | None, float]:
    sub = wpi_by_country.get(iso2)
    if sub is None or sub.empty:
        return None, 0.0
    n = norm_name(display_name)
    best_row: pd.Series | None = None
    best_r = 0.0
    for _, row in sub.iterrows():
        pn = norm_name(str(row["PORT_NAME"]))
        r = name_ratio(n, pn)
        if r > best_r:
            best_r = r
            best_row = row
    if best_row is not None and best_r >= min_ratio:
        return best_row, best_r
    return None, best_r


# UN/LOCODE (TW) -> exact WPI PORT_NAME when token/fuzzy matching is brittle.
TW_UNLOCODE_WPI_NAME: dict[str, str] = {
    "TWKHH": "KAO-HSIUNG",
    "TWKEL": "CHI-LUNG",
    "TWTXG": "TAI-CHUNG KANG",
    "TWTPE": "TAN-SHUI",
}

# UN/LOCODE -> (WPI COUNTRY cell, exact WPI PORT_NAME). Covers naming mismatches, new
# terminals absent under our names, and ISO quirks (e.g. Mayotte YT -> WPI uses KM).
MANUAL_UNLOCODE_WPI: dict[str, tuple[str, str]] = {
    "BZBGK": ("BZ", "BELIZE CITY"),
    "BRSPB": ("BR", "GUAIBA ISLAND TERMINAL"),
    "CNDCB": ("CN", "CHIWAN"),
    "DOCAU": ("DO", "ANDRES (ANDRES LNG TERMINAL)"),
    "ECPSJ": ("EC", "PUERTO MARITIMO DE GUAYAQUIL"),
    "FRDKK": ("FR", "DUNKERQUE PORT EST"),
    "INKAT": ("IN", "CHENNAI (MADRAS)"),
    "ITVCE": ("IT", "PORTO DI LIDO-VENEZIA"),
    "KWSAA": ("KW", "MINA ASH SHUAYBAH"),
    "KWSWK": ("KW", "AL KUWAYT"),
    "LBKYE": ("LB", "TARABULUS"),
    "YTLON": ("KM", "DZAOUDZI"),
    "NGLKK": ("NG", "LAGOS"),
    "OMSLL": ("OM", "MINA RAYSUT"),
    "QAHMD": ("QA", "UMM SAID"),
    "ZAZBA": ("ZA", "PORT ELIZABETH"),
    "SYLTK": ("SY", "AL LADHIQIYAH"),
    "TRDIL": ("TR", "DERINCE BURNU"),
    "AEKHL": ("AE", "ABU ZABY"),
}


def wpi_row_exact(
    wpi_by_country: dict[str, pd.DataFrame], wpi_country: str, port_name: str
) -> pd.Series | None:
    sub = wpi_by_country.get(wpi_country)
    if sub is None or sub.empty:
        return None
    hit = sub[sub["PORT_NAME"] == port_name]
    if not hit.empty:
        return hit.iloc[0]
    return None


def best_wpi_row(
    iso2: str, display_name: str, wpi_by_country: dict[str, pd.DataFrame]
) -> tuple[pd.Series | None, str]:
    n = norm_name(display_name)
    sub = wpi_by_country.get(iso2)
    if sub is None or sub.empty:
        return None, "NO_COUNTRY_IN_WPI"

    nn = sub["PORT_NAME"].map(norm_name)

    ex = sub[nn == n]
    if not ex.empty:
        return ex.iloc[0], "OK_EXACT"

    best = None
    best_score = 0
    for i, pn in enumerate(nn):
        if len(n) >= 4 and (n in pn):
            score = len(n)
        elif len(pn) >= 4 and (pn in n):
            score = len(pn)
        else:
            ntoks = set(n.split())
            ptoks = set(pn.split())
            inter = ntoks & ptoks
            score = max((len(t) for t in inter), default=0)

        if score > best_score:
            best_score = score
            best = sub.iloc[i]

    if best is not None and best_score >= 4:
        return best, f"OK_FUZZY_{int(best_score)}"

    return None, f"LOW_SCORE_{int(best_score)}"


def main() -> None:
    master = pd.read_csv(MASTER, keep_default_na=False)
    # WPI uses literal "NA" for Namibia; pandas default would turn it into NaN.
    w = pd.read_csv(WPI, keep_default_na=False)
    for col in ("LONGITUDE", "LATITUDE", "INDEX_NO"):
        if col in w.columns:
            w[col] = pd.to_numeric(w[col], errors="coerce")
    wpi_by_country = {k: g for k, g in w.groupby("COUNTRY")}

    ee_rows: list[dict] = []
    bad_rows: list[dict] = []
    report: list[dict] = []

    for _, r in master.iterrows():
        pid = str(r["port_id"]).strip().strip('"')
        name = str(r["port_name_standard"]).strip().strip('"')
        country = str(r["country"]).strip().strip('"')
        ul = r.get("unlocode")
        sort_ix = int(r["sort_index"]) if pd.notna(r.get("sort_index")) else None

        iso2 = iso2_from_unlocode(ul) if pd.notna(ul) and str(ul).strip() else None

        if iso2 is None:
            report.append(
                {
                    "port_id": pid,
                    "port_name": name,
                    "country": country,
                    "unlocode": ul,
                    "iso2": None,
                    "match_note": "NO_UNLOCODE",
                    "wpi_index_no": None,
                    "wpi_port_name": None,
                }
            )
            bad_rows.append(
                {
                    "port_id": pid,
                    "port_name": name,
                    "country": country,
                    "unlocode": ul,
                    "longitude": None,
                    "latitude": None,
                    "iso2": None,
                    "match_note": "NO_UNLOCODE",
                    "sort_index": sort_ix,
                }
            )
            continue

        ul_s = str(ul).strip().upper()
        wr: pd.Series | None = None
        note = ""

        if ul_s in MANUAL_UNLOCODE_WPI:
            wc, wpn = MANUAL_UNLOCODE_WPI[ul_s]
            wr = wpi_row_exact(wpi_by_country, wc, wpn)
            if wr is not None:
                note = "OK_MANUAL_UNLOCODE"

        if wr is None and iso2 == "TW" and ul_s in TW_UNLOCODE_WPI_NAME:
            wpi_name = TW_UNLOCODE_WPI_NAME[ul_s]
            sub_tw = wpi_by_country.get("TW")
            if sub_tw is not None:
                hit = sub_tw[sub_tw["PORT_NAME"] == wpi_name]
                if not hit.empty:
                    wr = hit.iloc[0]
                    note = "OK_TW_UNLOCODE_MAP"

        if wr is None:
            wr, note = best_wpi_row(iso2, name, wpi_by_country)

        if wr is None:
            wr, rr = ratio_best_wpi_row(iso2, name, wpi_by_country)
            if wr is not None:
                note = f"OK_RATIO_{rr:.2f}"

        report.append(
            {
                "port_id": pid,
                "port_name": name,
                "country": country,
                "unlocode": ul,
                "iso2": iso2,
                "match_note": note,
                "wpi_index_no": int(wr["INDEX_NO"]) if wr is not None else None,
                "wpi_port_name": str(wr["PORT_NAME"]) if wr is not None else None,
            }
        )

        if wr is None:
            bad_rows.append(
                {
                    "port_id": pid,
                    "port_name": name,
                    "country": country,
                    "unlocode": ul,
                    "longitude": None,
                    "latitude": None,
                    "iso2": iso2,
                    "match_note": note,
                    "sort_index": sort_ix,
                }
            )
            continue

        ee_rows.append(
            {
                "longitude": float(wr["LONGITUDE"]),
                "latitude": float(wr["LATITUDE"]),
                "port_id": pid,
                "port_name": name,
                "country": country,
                "iso2": iso2,
                "unlocode": str(ul).strip().upper(),
                "wpi_index_no": int(wr["INDEX_NO"]),
                "top50_rank": sort_ix,
            }
        )

    ee = pd.DataFrame(ee_rows)
    ee = ee[
        [
            "longitude",
            "latitude",
            "port_id",
            "port_name",
            "country",
            "iso2",
            "unlocode",
            "wpi_index_no",
            "top50_rank",
        ]
    ]

    OUT_EE.parent.mkdir(parents=True, exist_ok=True)
    ee.to_csv(OUT_EE, index=False, encoding="utf-8")
    pd.DataFrame(bad_rows).to_csv(OUT_UNMATCHED, index=False, encoding="utf-8")
    pd.DataFrame(report).to_csv(OUT_REPORT, index=False, encoding="utf-8")

    n_ok = len(ee)
    n_bad = len(bad_rows)
    print(f"Matched with coordinates: {n_ok} / {len(master)}")
    print(f"Unmatched / missing: {n_bad} -> {OUT_UNMATCHED}")
    print(f"EE upload table: {OUT_EE}")
    print(f"Match report: {OUT_REPORT}")


if __name__ == "__main__":
    main()
