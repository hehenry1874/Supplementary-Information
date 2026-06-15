"""
Step 1: Select low-confidence satellite ports for supplementary GEE rerun.

Inputs (defaults under repo root):
  - gee/port_satellite_scale_panel_full.csv
  - gee/ports_wpi_full_368_ee_table.csv  (longitude, latitude -> lon/lat)

Outputs:
  - gee/ports_wpi_low_confidence.csv
  - gee/ports_wpi_low_confidence_upload.csv  (longitude/latitude; for EE Table upload)
  - gee/ports_wpi_low_confidence.geojson

Usage:
  python scripts/SelectLowConfidenceSatellitePorts.py
  python scripts/SelectLowConfidenceSatellitePorts.py --panel path --wpi path --out-prefix gee/ports_wpi_low_confidence
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip().lower().replace(" ", "_") for c in out.columns]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--panel",
        type=str,
        default=str(ROOT / "gee" / "port_satellite_scale_panel_full.csv"),
    )
    ap.add_argument(
        "--wpi",
        type=str,
        default=str(ROOT / "gee" / "ports_wpi_full_368_ee_table.csv"),
    )
    ap.add_argument(
        "--out-prefix",
        type=str,
        default=str(ROOT / "gee" / "ports_wpi_low_confidence"),
    )
    ap.add_argument(
        "--out-batches",
        type=str,
        default=str(ROOT / "gee" / "lowconf_supp_batch_schedule.txt"),
        help="Text file listing BATCH_START values for GEE (size 30).",
    )
    args = ap.parse_args()

    panel_path = Path(args.panel)
    wpi_path = Path(args.wpi)
    out_csv = Path(args.out_prefix + ".csv")
    out_geo = Path(args.out_prefix + ".geojson")

    p = norm_cols(pd.read_csv(panel_path))
    wpi = pd.read_csv(wpi_path)
    wpi.columns = [c.strip().lower().replace(" ", "_") for c in wpi.columns]
    wpi = wpi.drop_duplicates(subset=["port_id"], keep="first")

    if "longitude" in wpi.columns:
        wpi = _rename_lonlat(wpi)
    if "port_name_standard" in wpi.columns and "port_name" not in wpi.columns:
        wpi["port_name"] = wpi["port_name_standard"]

    need_wpi = {"port_id", "port_name", "country", "lon", "lat"}
    miss = need_wpi - set(wpi.columns)
    if miss:
        raise SystemExit(f"WPI table missing columns: {miss}")

    port_rows = []
    for pid, g in p.groupby("port_id"):
        pid = str(pid)
        n = len(g)
        wpi_flag = pd.to_numeric(g["wpi_location_check_flag"], errors="coerce")
        max_wpi = float(wpi_flag.max()) if len(wpi_flag) else 0.0
        rule_wpi = bool((wpi_flag != 0).any())

        img = pd.to_numeric(g["image_quality_flag"], errors="coerce")
        bad_share = float((img == 1).sum()) / n if n else 0.0
        rule_img = bad_share >= 0.4

        v15 = pd.to_numeric(g["valid_2015_baseline_flag"], errors="coerce")
        min_v15 = float(v15.min()) if len(v15) else np.nan
        rule_v15 = bool((v15 == 0).any())

        s215 = pd.to_numeric(g["s2_2015_empty_flag"], errors="coerce")
        has_s215_empty = bool((s215 == 1).any())

        chg = pd.to_numeric(g["builtup_change_from_2015_km2"], errors="coerce")
        port_max_abs_chg = float(chg.abs().max()) if len(chg) else np.nan

        port_rows.append(
            {
                "port_id": pid,
                "max_wpi_location_check_flag": max_wpi,
                "image_quality_bad_share_template": bad_share,
                "valid_2015_baseline_min_template": min_v15,
                "rule_wpi": rule_wpi,
                "rule_img": rule_img,
                "rule_v15": rule_v15,
                "has_s215_empty": has_s215_empty,
                "port_max_abs_builtup_chg15": port_max_abs_chg,
            }
        )

    meta = pd.DataFrame(port_rows)
    # Rule 4: s2_2015_empty somewhere AND change magnitude in top tail among ports with any s2_2015 empty
    sub = meta[meta["has_s215_empty"]].copy()
    if len(sub) == 0:
        meta["rule_s215_outlier"] = False
    else:
        thr = sub["port_max_abs_builtup_chg15"].quantile(0.90)
        meta["rule_s215_outlier"] = meta["has_s215_empty"] & (
            meta["port_max_abs_builtup_chg15"] >= float(thr)
        )

    sel = meta[
        meta["rule_wpi"]
        | meta["rule_img"]
        | meta["rule_v15"]
        | meta["rule_s215_outlier"]
    ].copy()

    def reasons(row):
        r = []
        if row["rule_wpi"]:
            r.append("wpi_location_check_flag_nonzero")
        if row["rule_img"]:
            r.append("image_quality_bad_share_ge_0.4")
        if row["rule_v15"]:
            r.append("valid_2015_baseline_flag_any_zero")
        if row["rule_s215_outlier"]:
            r.append("s2_2015_empty_and_top_decile_abs_builtup_chg15")
        return ";".join(r)

    sel["low_confidence_reason"] = sel.apply(reasons, axis=1)

    w = wpi.set_index(wpi["port_id"].astype(str))[["port_name", "country", "lon", "lat"]]
    out = sel.set_index("port_id").join(w, how="inner")
    out = out.reset_index()

    v18_rows = []
    for pid, g in p.groupby("port_id"):
        v18 = pd.to_numeric(g["valid_2018_baseline_flag"], errors="coerce")
        v18_rows.append(
            {"port_id": str(pid), "valid_2018_baseline_min": float(v18.min())}
        )
    v18df = pd.DataFrame(v18_rows)
    out = out.merge(v18df, on="port_id", how="left")

    out["image_quality_bad_share"] = out["image_quality_bad_share_template"]
    out["valid_2015_baseline_min"] = out["valid_2015_baseline_min_template"]

    cols = [
        "port_id",
        "port_name",
        "country",
        "lon",
        "lat",
        "low_confidence_reason",
        "max_wpi_location_check_flag",
        "image_quality_bad_share",
        "valid_2015_baseline_min",
        "valid_2018_baseline_min",
    ]
    out = out[cols].sort_values("port_id")
    out.to_csv(out_csv, index=False, encoding="utf-8")

    # Earth Engine CSV ingest (longitude / latitude column names).
    upload_path = out_csv.parent / (out_csv.stem + "_upload.csv")
    upload_cols = [
        "longitude",
        "latitude",
        "port_id",
        "port_name",
        "country",
        "low_confidence_reason",
        "max_wpi_location_check_flag",
        "image_quality_bad_share",
        "valid_2015_baseline_min",
        "valid_2018_baseline_min",
    ]
    out.assign(longitude=out["lon"], latitude=out["lat"])[upload_cols].to_csv(
        upload_path, index=False, encoding="utf-8"
    )

    # GeoJSON (WGS84 points)
    features = []
    for _, r in out.iterrows():
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r["lon"]), float(r["lat"])],
                },
                "properties": {
                    "port_id": r["port_id"],
                    "port_name": r["port_name"],
                    "country": r["country"],
                    "low_confidence_reason": r["low_confidence_reason"],
                },
            }
        )
    fc = {"type": "FeatureCollection", "features": features}
    with open(out_geo, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)

    n = len(out)
    batch_size = 30
    n_batches = (n + batch_size - 1) // batch_size if n else 0
    lines = [
        f"low_confidence_ports={n}",
        f"batch_size={batch_size}",
        f"n_batches={n_batches}",
        "",
        "GEE: set BATCH_START / EXPORT_SUFFIX in gee/port_physical_expansion_wpi_lowconf_supp.js",
        "Drive file name pattern: port_satellite_scale_panel_<EXPORT_SUFFIX>_supp.csv",
        "",
        "BATCH_START  EXPORT_SUFFIX (example)",
    ]
    for b in range(n_batches):
        start = b * batch_size
        end = min(start + batch_size, n)
        suf = f"lowconf_batch{start:03d}_{end:03d}"
        lines.append(f"  {start}  {suf}")
    Path(args.out_batches).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Selected {n} low-confidence ports -> {out_csv}")
    print(f"GeoJSON -> {out_geo}")
    print(f"GEE upload CSV -> {upload_path}")
    print(f"Batch schedule -> {args.out_batches}")


def _rename_lonlat(wpi: pd.DataFrame) -> pd.DataFrame:
    w = wpi.copy()
    if "longitude" in w.columns:
        w["lon"] = pd.to_numeric(w["longitude"], errors="coerce")
    if "latitude" in w.columns:
        w["lat"] = pd.to_numeric(w["latitude"], errors="coerce")
    return w


if __name__ == "__main__":
    main()
