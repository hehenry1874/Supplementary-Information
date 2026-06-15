"""
Pilot QC for GEE export: port_satellite_scale_panel_pilot30.csv

Usage:
  python scripts/PortSatellite-Pilot-QC.py
  set PORT_SATELLITE_CSV=path/to/file.csv for custom input

Outputs (default under gee/):
  - port_satellite_pilot_qc_summary.csv
  - port_satellite_pilot_anomaly_list.csv
  - port_satellite_pilot_manual_review_ports.csv  (10 ports for visual check)
  - port_satellite_pilot_scale_up_decision.txt

Does not build physical_expansion_index or merge readiness scores.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "gee" / "port_satellite_scale_panel_pilot30.csv"
OUT_SUMMARY = ROOT / "gee" / "port_satellite_pilot_qc_summary.csv"
OUT_ANOMALIES = ROOT / "gee" / "port_satellite_pilot_anomaly_list.csv"
OUT_MANUAL = ROOT / "gee" / "port_satellite_pilot_manual_review_ports.csv"
OUT_DECISION = ROOT / "gee" / "port_satellite_pilot_scale_up_decision.txt"

NUMERIC_COLS_BASE = [
    "builtup_area_km2",
    "builtup_share",
    "builtup_change_from_2015_km2",
    "water_to_land_area_km2",
    "shoreline_change_proxy_km2",
    "yard_like_area_km2",
    "yard_like_share",
    "yard_like_change_from_2015_km2",
    "largest_yard_like_patch_km2",
    "aoi_area_km2",
]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    drop = [c for c in df.columns if c.startswith("system:") or c.startswith(".geo")]
    if drop:
        df = df.drop(columns=drop, errors="ignore")
    return df


def check_nested_disks(
    df: pd.DataFrame, value_col: str, viol_tol_km2: float
) -> tuple[pd.DataFrame, int]:
    """10 km disk should contain 5 km disk → zonal area at 10 km >= area at 5 km (same year)."""
    rows = []
    if "buffer_radius_km" not in df.columns:
        return pd.DataFrame(), 0
    p = df.pivot_table(
        index=["port_id", "calendar_year"],
        columns="buffer_radius_km",
        values=value_col,
        aggfunc="first",
    )
    c5 = next((c for c in p.columns if abs(float(c) - 5) < 1e-3), None)
    c10 = next((c for c in p.columns if abs(float(c) - 10) < 1e-3), None)
    if c5 is None or c10 is None:
        return pd.DataFrame(), 0
    p = p.rename(columns={c5: "v5", c10: "v10"})
    p["diff"] = p["v10"] - p["v5"]
    bad = p[p["diff"] < -viol_tol_km2].reset_index()
    for _, r in bad.iterrows():
        rows.append(
            {
                "port_id": r["port_id"],
                "calendar_year": int(r["calendar_year"]),
                "buffer_radius_km": "5_vs_10",
                "anomaly_type": "nested_buffer_monotonicity",
                "metric": value_col,
                "detail": f"10km={float(r['v10']):.4f} 5km={float(r['v5']):.4f} diff={float(r['diff']):.4f}",
                "severity": "high" if float(r["diff"]) < -1.0 else "medium",
            }
        )
    return pd.DataFrame(rows), len(bad)


def main() -> int:
    csv_path = Path(os.environ.get("PORT_SATELLITE_CSV", str(DEFAULT_CSV)))
    geo_pilot = ROOT / "gee" / "ports_wpi_pilot30.geojson"
    geo_json = ROOT / "gee" / "ports_wpi_pilot30.json"
    geo_source = geo_pilot if geo_pilot.is_file() else geo_json

    if not csv_path.is_file():
        print(f"Missing input CSV: {csv_path}", file=sys.stderr)
        print(
            "Run GEE script with pilot asset, export to Drive, save as:\n"
            f"  {DEFAULT_CSV}",
            file=sys.stderr,
        )
        pd.DataFrame([{"key": "status", "value": "BLOCKED_NO_INPUT"}]).to_csv(
            OUT_SUMMARY, index=False
        )
        pd.DataFrame().to_csv(OUT_ANOMALIES, index=False)
        if geo_source.is_file():
            fc = json.loads(geo_source.read_text(encoding="utf-8"))
            feats = sorted(
                fc.get("features", []),
                key=lambda f: f.get("properties", {}).get("top50_rank", 999),
            )[:10]
            mr_pre = []
            for i, f in enumerate(feats, 1):
                p = f.get("properties", {})
                mr_pre.append(
                    {
                        "review_rank": i,
                        "port_id": p.get("port_id", ""),
                        "port_name": p.get("port_name", ""),
                        "country": p.get("country", ""),
                        "priority_score": "",
                        "reason": "placeholder_top10_by_top50_rank_pre_gee_export",
                    }
                )
            pd.DataFrame(mr_pre).to_csv(OUT_MANUAL, index=False)
        else:
            pd.DataFrame().to_csv(OUT_MANUAL, index=False)
        OUT_DECISION.write_text(
            "BLOCKED: No port_satellite_scale_panel_pilot30.csv found. "
            "Complete Earth Engine export first, then re-run this script.\n"
            "A placeholder manual-review list (first 10 pilots by top50_rank) "
            "was written if gee/ports_wpi_pilot30.geojson or .json exists.\n",
            encoding="utf-8",
        )
        return 2

    df = normalize_columns(pd.read_csv(csv_path))
    for c in [
        "calendar_year",
        "buffer_radius_km",
        "image_quality_flag",
        "cloud_quality_flag",
        "wpi_location_check_flag",
        "reclamation_proxy_flag",
        "partial_calendar_year_flag",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    anomalies: list[dict] = []
    n_built_v = 0
    n_yard_v = 0

    n_rows = len(df)
    ports = int(df["port_id"].nunique()) if "port_id" in df.columns else 0
    years_u = (
        sorted(df["calendar_year"].dropna().unique().tolist())
        if "calendar_year" in df.columns
        else []
    )
    buf_u = (
        sorted(df["buffer_radius_km"].dropna().unique().tolist())
        if "buffer_radius_km" in df.columns
        else []
    )
    expected = ports * len(years_u) * len(buf_u)
    override = os.environ.get("EXPECTED_ROWS")
    if override:
        expected = int(override)
    row_ok = n_rows == expected
    if not row_ok:
        anomalies.append(
            {
                "port_id": "_PANEL_",
                "calendar_year": "",
                "buffer_radius_km": "",
                "anomaly_type": "row_count_mismatch",
                "metric": "n_rows",
                "detail": (
                    f"n_rows={n_rows} expected_ports*years*buffers={expected} "
                    f"(ports={ports} years={len(years_u)} buffers={len(buf_u)})"
                ),
                "severity": "high",
            }
        )

    for c in ["port_id", "calendar_year", "buffer_radius_km"]:
        if c not in df.columns:
            anomalies.append(
                {
                    "port_id": "_SCHEMA_",
                    "calendar_year": "",
                    "buffer_radius_km": "",
                    "anomaly_type": "missing_column",
                    "metric": c,
                    "detail": "required column absent",
                    "severity": "high",
                }
            )

    for col in NUMERIC_COLS_BASE:
        if col not in df.columns:
            continue
        bad = df[df[col].isna()]
        for _, r in bad.iterrows():
            anomalies.append(
                {
                    "port_id": r.get("port_id", ""),
                    "calendar_year": r.get("calendar_year", ""),
                    "buffer_radius_km": r.get("buffer_radius_km", ""),
                    "anomaly_type": "missing_numeric",
                    "metric": col,
                    "detail": "NaN",
                    "severity": "medium",
                }
            )

    TOL = 0.05
    sub51 = df[df["buffer_radius_km"].isin([5, 10])] if "buffer_radius_km" in df.columns else df
    adv1, n_built_v = check_nested_disks(sub51, "builtup_area_km2", TOL)
    if not adv1.empty:
        anomalies.extend(adv1.to_dict("records"))
    adv2, n_yard_v = check_nested_disks(sub51, "yard_like_area_km2", TOL)
    if not adv2.empty:
        anomalies.extend(adv2.to_dict("records"))

    ch = df.dropna(subset=["builtup_change_from_2015_km2"]).copy()
    if not ch.empty and "buffer_radius_km" in ch.columns:
        for b in ch["buffer_radius_km"].unique():
            sub = ch[ch["buffer_radius_km"] == b]["builtup_change_from_2015_km2"].astype(float)
            if len(sub) < 5:
                continue
            p95 = sub.abs().quantile(0.95)
            thr = max(15.0, float(p95))
            spike = ch[
                (ch["buffer_radius_km"] == b)
                & (ch["builtup_change_from_2015_km2"].abs() > thr)
            ]
            for _, r in spike.iterrows():
                anomalies.append(
                    {
                        "port_id": r["port_id"],
                        "calendar_year": int(r["calendar_year"]),
                        "buffer_radius_km": r["buffer_radius_km"],
                        "anomaly_type": "builtup_change_large",
                        "metric": "builtup_change_from_2015_km2",
                        "detail": (
                            f"value={float(r['builtup_change_from_2015_km2']):.3f} thr~{thr:.3f}"
                        ),
                        "severity": "medium",
                    }
                )

    if "water_to_land_area_km2" in df.columns:
        wsum = (
            df.groupby("port_id", as_index=False)["water_to_land_area_km2"]
            .sum()
            .sort_values("water_to_land_area_km2", ascending=False)
        )
        tot = wsum["water_to_land_area_km2"].sum()
        if tot > 0:
            wsum = wsum.copy()
            wsum["share"] = wsum["water_to_land_area_km2"] / tot
            dominant = wsum.iloc[0]
            if float(dominant["share"]) > 0.45:
                anomalies.append(
                    {
                        "port_id": dominant["port_id"],
                        "calendar_year": "_ALL_",
                        "buffer_radius_km": "_ALL_",
                        "anomaly_type": "water_to_land_concentrated",
                        "metric": "sum_share_top1",
                        "detail": f"top_port_share={float(dominant['share']):.3f}",
                        "severity": "low",
                    }
                )
        pm = df.groupby("port_id")["water_to_land_area_km2"].mean()
        if len(pm) and float(pm.median()) > 8.0:
            anomalies.append(
                {
                    "port_id": "_PANEL_",
                    "calendar_year": "",
                    "buffer_radius_km": "",
                    "anomaly_type": "water_to_land_median_high",
                    "metric": "median_per_port_mean_km2",
                    "detail": f"{float(pm.median()):.3f}",
                    "severity": "medium",
                }
            )

    if "yard_like_area_km2" in df.columns:
        for b in df["buffer_radius_km"].dropna().unique():
            sub = df[df["buffer_radius_km"] == b]["yard_like_area_km2"].astype(float)
            if len(sub) < 10:
                continue
            x = np.log1p(sub.values)
            q1, q3 = np.percentile(x, [25, 75])
            iqr = q3 - q1
            hi = q3 + 3.0 * iqr
            out_idx = sub.index[(x > hi)]
            for idx in out_idx:
                r = df.loc[idx]
                anomalies.append(
                    {
                        "port_id": r["port_id"],
                        "calendar_year": int(r["calendar_year"]),
                        "buffer_radius_km": r["buffer_radius_km"],
                        "anomaly_type": "yard_like_area_outlier",
                        "metric": "yard_like_area_km2",
                        "detail": f"value={float(r['yard_like_area_km2']):.3f}",
                        "severity": "medium",
                    }
                )

    adv_df = pd.DataFrame(anomalies)
    adv_df.to_csv(OUT_ANOMALIES, index=False)

    summary_rows = [
        {"key": "input_csv", "value": str(csv_path)},
        {"key": "n_rows", "value": str(n_rows)},
        {"key": "n_ports", "value": str(ports)},
        {"key": "calendar_years_json", "value": json.dumps(years_u)},
        {"key": "buffer_values_json", "value": json.dumps(buf_u)},
        {"key": "expected_rows_ports_x_years_x_buffers", "value": str(expected)},
        {"key": "row_count_matches_expectation", "value": str(row_ok)},
        {"key": "nested_built_violations", "value": str(n_built_v)},
        {"key": "nested_yard_violations", "value": str(n_yard_v)},
    ]
    for col in ["image_quality_flag", "cloud_quality_flag", "wpi_location_check_flag"]:
        if col in df.columns:
            vc = df[col].value_counts(dropna=False).to_dict()
            summary_rows.append(
                {
                    "key": f"dist_{col}",
                    "value": json.dumps({str(k): int(v) for k, v in vc.items()}),
                }
            )

    high = len(adv_df[adv_df["severity"] == "high"]) if not adv_df.empty else 0
    medium = len(adv_df[adv_df["severity"] == "medium"]) if not adv_df.empty else 0

    scale_ok = row_ok and high == 0 and n_built_v <= 2 and n_yard_v <= 2 and ports >= 25
    scale_conditional = row_ok and high == 0 and (n_built_v + n_yard_v) <= 8 and ports >= 25

    rec = (
        "PROCEED_FULL_PANEL"
        if scale_ok
        else (
            "PROCEED_WITH_CAUTION"
            if scale_conditional
            else "HOLD_FIX_ISSUES_BEFORE_FULL_PANEL"
        )
    )

    summary_rows.extend(
        [
            {"key": "anomaly_count_high", "value": str(high)},
            {"key": "anomaly_count_medium", "value": str(medium)},
            {"key": "scale_up_recommendation", "value": rec},
        ]
    )
    pd.DataFrame(summary_rows).to_csv(OUT_SUMMARY, index=False)

    port_scores: dict[str, float] = {}
    if "port_id" in df.columns:
        for pid in df["port_id"].unique():
            port_scores[str(pid)] = 0.0
    for _, a in adv_df.iterrows():
        pid = str(a.get("port_id", ""))
        if pid in port_scores and pid not in ("_PANEL_", "_SCHEMA_"):
            w = 3.0 if a["severity"] == "high" else 1.0 if a["severity"] == "medium" else 0.3
            port_scores[pid] = port_scores.get(pid, 0) + w
    if "port_id" in df.columns:
        dmax = df.groupby("port_id").agg(
            {
                "image_quality_flag": lambda s: float(s.max()) if s.notna().any() else 0,
                "cloud_quality_flag": lambda s: float(s.max()) if s.notna().any() else 0,
                "wpi_location_check_flag": lambda s: float(s.max()) if s.notna().any() else 0,
            }
        )
        for pid, row in dmax.iterrows():
            ps = str(pid)
            port_scores[ps] = port_scores.get(ps, 0) + 2 * row["image_quality_flag"]
            port_scores[ps] += 2 * row["cloud_quality_flag"]
            port_scores[ps] += 1.5 * (1 if row["wpi_location_check_flag"] > 0 else 0)

    ranked = sorted(port_scores.items(), key=lambda x: -x[1])[:10]
    mr = []
    for rank, (pid, score) in enumerate(ranked, 1):
        subdf = df[df["port_id"] == pid]
        if subdf.empty:
            continue
        sub = subdf.iloc[0]
        mr.append(
            {
                "review_rank": rank,
                "port_id": pid,
                "port_name": sub.get("port_name", ""),
                "country": sub.get("country", ""),
                "priority_score": round(score, 3),
                "reason": "weighted_anomalies_and_flags",
            }
        )
    pd.DataFrame(mr).to_csv(OUT_MANUAL, index=False)

    decision_text = (
        f"Pilot QC recommendation: {rec}\n\n"
        f"Rows: {n_rows} (expected {expected} = {ports} ports × {len(years_u)} "
        f"years × {len(buf_u)} buffers).\n"
        f"Nested-disk violations: built {n_built_v}, yard {n_yard_v} "
        f"(tolerance {TOL} km²).\n"
        f"High-severity anomalies: {high}; medium: {medium}.\n\n"
        f"{'Full 368-port extension is supported if PROCEED_FULL_PANEL. ' if scale_ok else ''}"
        f"{'Address high-severity issues or review tolerance before scaling. ' if not scale_ok and not scale_conditional else ''}"
        f"{'Conditional scale-up: tighten thresholds after spot checks. ' if scale_conditional and not scale_ok else ''}\n"
    )
    OUT_DECISION.write_text(decision_text, encoding="utf-8")

    print(decision_text)
    print(f"Wrote: {OUT_SUMMARY}\n{OUT_ANOMALIES}\n{OUT_MANUAL}\n{OUT_DECISION}")
    return 0 if rec != "HOLD_FIX_ISSUES_BEFORE_FULL_PANEL" else 1


if __name__ == "__main__":
    sys.exit(main())
