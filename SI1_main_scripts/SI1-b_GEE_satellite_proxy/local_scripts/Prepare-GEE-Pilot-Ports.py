"""
Build pilot port table + GeoJSON for Google Earth Engine upload.

Joins top50_ports_master_mapping.csv to scripts/ports_wpi.csv using:
  - UN/LOCODE first two letters == WPI COUNTRY (CN, SG, HK, ...)
  - Normalized / substring port name matching (handles THANH HO CHI MINH, etc.)

Writes:
  - gee/ports_wpi_pilot30.csv
  - gee/ports_wpi_pilot30.geojson
  - gee/ports_wpi_pilot30.json   (same as GeoJSON; Earth Engine accepts .json, not .geojson)
  - gee/ports_wpi_pilot30_ee_table.csv  (point table: longitude, latitude + attributes for CSV upload)

Requires: pandas.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TOP50 = ROOT / "top50_ports_master_mapping.csv"
WPI = ROOT / "scripts" / "ports_wpi.csv"
OUT_CSV = ROOT / "gee" / "ports_wpi_pilot30.csv"
OUT_GEOJSON = ROOT / "gee" / "ports_wpi_pilot30.geojson"
OUT_JSON = ROOT / "gee" / "ports_wpi_pilot30.json"
OUT_EE_TABLE = ROOT / "gee" / "ports_wpi_pilot30_ee_table.csv"
TARGET_PLSCI = 30


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


def best_wpi_row(iso2: str, display_name: str, wpi_by_country: dict[str, pd.DataFrame]) -> tuple[pd.Series | None, str]:
    n = norm_name(display_name)
    sub = wpi_by_country.get(iso2)
    if sub is None or sub.empty:
        return None, "NO_COUNTRY_IN_WPI"

    nn = sub["PORT_NAME"].map(norm_name)

    ex = sub[nn == n]
    if not ex.empty:
        return ex.iloc[0], "OK_EXACT"

    # Long-token substring (e.g. HO CHI MINH in THANH HO CHI MINH; NEW YORK in NEW YORK CITY)
    best = None
    best_score = 0
    for i, pn in enumerate(nn):
        if len(n) >= 5 and (n in pn):
            score = len(n)
        elif len(pn) >= 5 and (pn in n):
            score = len(pn)
        else:
            ntoks = set(n.split())
            ptoks = set(pn.split())
            inter = ntoks & ptoks
            score = max((len(t) for t in inter), default=0)

        if score > best_score:
            best_score = score
            best = sub.iloc[i]

    if best is not None and best_score >= 5:
        return best, "OK_FUZZY"

    return None, "NO_WPI_ROW"


def main() -> None:
    top = pd.read_csv(TOP50).sort_values("rank")
    w = pd.read_csv(WPI)
    wpi_by_country = {k: g for k, g in w.groupby("COUNTRY")}

    rows: list[dict] = []
    matched = 0
    for _, r in top.iterrows():
        pid = str(r["ports_master_port_id"]).strip()
        if pid.startswith("CPPI_ONLY"):
            continue
        if matched >= TARGET_PLSCI:
            break

        ul = r.get("unlocode")
        iso2 = iso2_from_unlocode(ul) if pd.notna(ul) else None
        name = str(r["ports_master_display_name"])
        ctry = str(r["ports_master_country"])

        if iso2 is None:
            continue

        wr, note = best_wpi_row(iso2, name, wpi_by_country)
        if wr is None:
            continue

        rows.append(
            {
                "port_id": pid,
                "port_name": name,
                "country": ctry,
                "unlocode": ul,
                "iso2": iso2,
                "lat": float(wr["LATITUDE"]),
                "lon": float(wr["LONGITUDE"]),
                "wpi_index_no": int(wr["INDEX_NO"]),
                "wpi_port_name": str(wr["PORT_NAME"]),
                "match_note": note,
                "top50_rank": int(r["rank"]),
            }
        )
        matched += 1

    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")

    ee_tbl = df.dropna(subset=["lat", "lon"]).copy()
    ee_tbl["longitude"] = ee_tbl["lon"].astype(float)
    ee_tbl["latitude"] = ee_tbl["lat"].astype(float)
    ee_cols = [
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
    ee_cols = [c for c in ee_cols if c in ee_tbl.columns]
    ee_tbl[ee_cols].to_csv(OUT_EE_TABLE, index=False, encoding="utf-8")

    feats = []
    for _, r in df.iterrows():
        if r["lat"] is None or (isinstance(r["lat"], float) and pd.isna(r["lat"])):
            continue
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
                "properties": {
                    "port_id": r["port_id"],
                    "port_name": r["port_name"],
                    "country": r["country"],
                    "iso2": r.get("iso2"),
                    "wpi_index_no": r.get("wpi_index_no"),
                    "top50_rank": int(r["top50_rank"]),
                },
            }
        )
    fc = {"type": "FeatureCollection", "features": feats}
    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUT_CSV} ({len(df)} pilot ports with coordinates)")
    print(f"Wrote {OUT_GEOJSON} ({len(feats)} features)")
    print(f"Wrote {OUT_JSON} (duplicate for GEE upload; use if .geojson is rejected)")
    print(f"Wrote {OUT_EE_TABLE} (CSV with longitude/latitude for GEE Table Upload)")


if __name__ == "__main__":
    main()
