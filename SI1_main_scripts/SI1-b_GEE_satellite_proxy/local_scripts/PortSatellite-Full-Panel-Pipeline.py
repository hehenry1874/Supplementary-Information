"""
Near-port satellite proxy panel: merge GEE batch CSVs, clean, summarize, index, QC.

Does NOT merge into readiness scores or run regressions.

Usage:
  python scripts/PortSatellite-Full-Panel-Pipeline.py all \\
      --exports-dir gee/exports \\
      --n-ports 368

  python scripts/PortSatellite-Full-Panel-Pipeline.py merge --exports-dir gee/exports
  python scripts/PortSatellite-Full-Panel-Pipeline.py qc --panel gee/port_satellite_scale_panel_full.csv

Expected rows (with2026): n_ports * 5 years * 2 buffers (e.g. 3680).
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORTS = ROOT / "gee" / "exports"
PANEL_FULL = ROOT / "gee" / "port_satellite_scale_panel_full.csv"
PANEL_RAW_BACKUP = ROOT / "gee" / "port_satellite_scale_panel_full_merged_before_sentinel.csv"
PANEL_5KM = ROOT / "gee" / "port_satellite_scale_panel_full_5km.csv"
PANEL_10KM = ROOT / "gee" / "port_satellite_scale_panel_full_10km.csv"
PANEL_MAIN = ROOT / "gee" / "port_satellite_scale_panel_full_main5km_analysis.csv"
SUMMARY_1524 = ROOT / "gee" / "port_satellite_change_summary_2015_2024.csv"
SUMMARY_1824 = ROOT / "gee" / "port_satellite_change_summary_2018_2024.csv"
INDEX_PRELIM = ROOT / "gee" / "physical_expansion_index_prelim.csv"
QC_SUMMARY = ROOT / "gee" / "port_satellite_full_qc_summary.csv"
QC_ANOMALIES = ROOT / "gee" / "port_satellite_anomaly_list.csv"
QC_MANUAL = ROOT / "gee" / "port_satellite_manual_check_list.csv"

GEE_SENTINEL = -999
WINSOR_LOW = 0.01
WINSOR_HIGH = 0.99

REQUIRED_CORE_COLS = [
    "builtup_area_km2",
    "builtup_change_from_2015_km2",
    "builtup_change_from_2018_km2",
    "water_to_land_change_from_2015_km2",
    "water_to_land_change_from_2018_km2",
    "yard_like_change_from_2015_km2",
    "yard_like_change_from_2018_km2",
    "shoreline_change_from_2015_km2",
    "shoreline_change_from_2018_km2",
    "image_quality_flag",
    "wpi_location_check_flag",
]


def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    drop = [c for c in df.columns if c.startswith("system:") or c == ".geo"]
    return df.drop(columns=drop, errors="ignore")


def merge_exports(exports_dir: Path, pattern: str = "*.csv") -> pd.DataFrame:
    files = sorted(glob.glob(str(exports_dir / pattern)))
    if not files:
        raise FileNotFoundError(f"No CSV under {exports_dir} matching {pattern}")
    parts = []
    for f in files:
        dfp = norm_cols(pd.read_csv(f))
        dfp["_source_file"] = Path(f).name
        parts.append(dfp)
    return pd.concat(parts, ignore_index=True)


def replace_sentinel(df: pd.DataFrame) -> pd.DataFrame:
    """GEE_SENTINEL -> NaN on numeric columns."""
    d = df.copy()
    for c in d.select_dtypes(include=[np.number]).columns:
        d[c] = d[c].replace(GEE_SENTINEL, np.nan)
        d[c] = d[c].replace(float(GEE_SENTINEL), np.nan)
    return d


def winsor_minmax(s: pd.Series, low: float = WINSOR_LOW, high: float = WINSOR_HIGH) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    lo, hi = s.quantile([low, high])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return pd.Series(np.nan, index=s.index)
    clipped = s.clip(lo, hi)
    return (clipped - lo) / (hi - lo)


def split_buffers(df: pd.DataFrame) -> None:
    d = df.copy()
    b = pd.to_numeric(d["buffer_radius_km"], errors="coerce")
    d5 = d[b.round(0) == 5].copy()
    d10 = d[b.round(0) == 10].copy()
    d5.to_csv(PANEL_5KM, index=False, encoding="utf-8")
    d10.to_csv(PANEL_10KM, index=False, encoding="utf-8")
    d5.to_csv(PANEL_MAIN, index=False, encoding="utf-8")


def _row_year(df: pd.DataFrame, port: str, buf: float, year: int) -> pd.Series | None:
    m = (
        (df["port_id"].astype(str) == str(port))
        & (pd.to_numeric(df["buffer_radius_km"], errors="coerce").round(0) == buf)
        & (pd.to_numeric(df["calendar_year"], errors="coerce") == year)
    )
    sub = df.loc[m]
    if len(sub) != 1:
        return None
    return sub.iloc[0]


def _get_f(r: pd.Series | None, k: str):
    if r is None or k not in r.index:
        return np.nan
    return pd.to_numeric(r[k], errors="coerce")


def build_summaries(df: pd.DataFrame) -> None:
    df = df.copy()
    df["calendar_year"] = pd.to_numeric(df["calendar_year"], errors="coerce")
    rows_1524 = []
    rows_1824 = []
    for (pid, buf), _ in df.groupby(["port_id", "buffer_radius_km"]):
        buf = float(buf)
        r15 = _row_year(df, pid, buf, 2015)
        r18 = _row_year(df, pid, buf, 2018)
        r24 = _row_year(df, pid, buf, 2024)
        if r24 is None:
            continue
        name = r24.get("port_name", "")
        country = r24.get("country", "")

        built24 = _get_f(r24, "builtup_area_km2")
        built15 = _get_f(r15, "builtup_area_km2")

        shore15_15 = _get_f(r15, "shoreline_change_from_2015_km2")
        shore15_24 = _get_f(r24, "shoreline_change_from_2015_km2")
        shore18_18 = _get_f(r18, "shoreline_change_from_2018_km2")
        shore18_24 = _get_f(r24, "shoreline_change_from_2018_km2")

        d156 = built24 - built15 if pd.notna(built24) and pd.notna(built15) else np.nan
        rows_1524.append(
            {
                "port_id": pid,
                "port_name": name,
                "country": country,
                "buffer_radius_km": buf,
                "builtup_change_2015_2024_km2": d156,
                "builtup_positive_change_2015_2024_km2": max(d156, 0.0)
                if pd.notna(d156)
                else np.nan,
                "water_to_land_positive_change_2015_2024_km2": _get_f(
                    r24, "water_to_land_positive_change_from_2015_km2"
                ),
                "shoreline_change_2015_2024_km2": (
                    shore15_24 - shore15_15
                    if pd.notna(shore15_24) and pd.notna(shore15_15)
                    else np.nan
                ),
                "yard_like_positive_change_2015_2024_km2": _get_f(
                    r24, "yard_like_positive_change_from_2015_km2"
                ),
            }
        )

        rows_1824.append(
            {
                "port_id": pid,
                "port_name": name,
                "country": country,
                "buffer_radius_km": buf,
                "builtup_positive_change_2018_2024_km2": _get_f(
                    r24, "builtup_positive_change_from_2018_km2"
                ),
                "water_to_land_positive_change_2018_2024_km2": _get_f(
                    r24, "water_to_land_positive_change_from_2018_km2"
                ),
                "yard_like_positive_change_2018_2024_km2": _get_f(
                    r24, "yard_like_positive_change_from_2018_km2"
                ),
                "shoreline_change_2018_2024_km2": (
                    shore18_24 - shore18_18
                    if pd.notna(shore18_24) and pd.notna(shore18_18)
                    else np.nan
                ),
            }
        )

    pd.DataFrame(rows_1524).to_csv(SUMMARY_1524, index=False, encoding="utf-8")
    pd.DataFrame(rows_1824).to_csv(SUMMARY_1824, index=False, encoding="utf-8")


def build_prelim_index(panel: pd.DataFrame) -> None:
    p = panel.copy()
    p["calendar_year"] = pd.to_numeric(p["calendar_year"], errors="coerce")
    p["buffer_radius_km"] = pd.to_numeric(p["buffer_radius_km"], errors="coerce")
    sub = p[(p["calendar_year"] == 2024) & (p["buffer_radius_km"].round(0) == 5)]
    if sub.empty:
        return

    b = "builtup_positive_change_from_2015_km2"
    w = "water_to_land_positive_change_from_2015_km2"
    y = "yard_like_positive_change_from_2015_km2"
    b8 = "builtup_positive_change_from_2018_km2"
    w8 = "water_to_land_positive_change_from_2018_km2"
    y8 = "yard_like_positive_change_from_2018_km2"

    cols = ["port_id", "port_name", "country"] + [c for c in [b, w, y, b8, w8, y8] if c in sub.columns]
    out = sub[cols].drop_duplicates(subset=["port_id"]).copy()

    if b in out.columns:
        out[b + "_norm_2015base"] = winsor_minmax(out[b])
    if w in out.columns:
        out[w + "_norm_2015base"] = winsor_minmax(out[w])
    if y in out.columns:
        out[y + "_norm_2015base"] = winsor_minmax(out[y])

    if b8 in out.columns:
        out[b8 + "_norm_2018base"] = winsor_minmax(out[b8])
    if w8 in out.columns:
        out[w8 + "_norm_2018base"] = winsor_minmax(out[w8])
    if y8 in out.columns:
        out[y8 + "_norm_2018base"] = winsor_minmax(out[y8])

    nb = b + "_norm_2015base"
    nw = w + "_norm_2015base"
    ny = y + "_norm_2015base"
    if all(c in out.columns for c in [nb, nw, ny]):
        out["physical_expansion_index_prelim_2015base"] = out[[nb, nw, ny]].mean(axis=1)

    nb8 = b8 + "_norm_2018base"
    nw8 = w8 + "_norm_2018base"
    ny8 = y8 + "_norm_2018base"
    if all(c in out.columns for c in [nb8, nw8, ny8]):
        out["physical_expansion_index_prelim_2018base"] = out[[nb8, nw8, ny8]].mean(axis=1)

    out["index_note"] = (
        "Near-port proxies only; winsorized min-max at y2024; not true port area."
    )
    out.to_csv(INDEX_PRELIM, index=False, encoding="utf-8")


def run_qc(panel: pd.DataFrame, n_ports_expected: int, n_years: int = 5) -> None:
    df = norm_cols(panel)
    anomalies: list[dict] = []
    summary: list[dict] = []

    key = ["port_id", "calendar_year", "buffer_radius_km"]
    dup = df.duplicated(subset=key, keep=False)
    summary.append({"metric": "total_rows", "value": len(df)})
    summary.append({"metric": "unique_port_year_buffer", "value": df.groupby(key).ngroups})
    summary.append({"metric": "duplicate_key_rows", "value": int(dup.sum())})

    expected = n_ports_expected * n_years * 2
    summary.append({"metric": "expected_rows_ports_years_2buf", "value": expected})
    summary.append({"metric": "row_count_matches_expected", "value": len(df) == expected})

    for c in ["calendar_year", "buffer_radius_km"]:
        vc = df.groupby([c]).size().to_dict()
        summary.append(
            {"metric": f"count_by_{c}", "value": json.dumps({str(k): int(v) for k, v in vc.items()})}
        )

    for flag in [
        "image_quality_flag",
        "s2_empty_flag",
        "dw_empty_flag",
        "valid_2015_baseline_flag",
        "valid_2018_baseline_flag",
        "wpi_location_check_flag",
        "partial_calendar_year_flag",
    ]:
        if flag in df.columns:
            vc = df[flag].value_counts(dropna=False).to_dict()
            summary.append(
                {"metric": f"dist_{flag}", "value": json.dumps({str(k): int(v) for k, v in vc.items()})}
            )

    TOL = 0.05
    br = pd.to_numeric(df["buffer_radius_km"], errors="coerce").round(0)
    sub51 = df[br.isin([5, 10])].copy()
    sub51["_br"] = pd.to_numeric(sub51["buffer_radius_km"], errors="coerce").round(0)
    for metric in ["builtup_area_km2", "yard_like_area_km2"]:
        if metric not in sub51.columns:
            continue
        p = sub51.pivot_table(
            index=["port_id", "calendar_year"],
            columns="_br",
            values=metric,
            aggfunc="first",
        )
        if 5 not in p.columns or 10 not in p.columns:
            continue
        p["diff"] = p[10] - p[5]
        bad = p[p["diff"] < -TOL]
        for idx, r in bad.iterrows():
            anomalies.append(
                {
                    "anomaly_type": "nested_buffer_violation",
                    "metric": metric,
                    "port_id": idx[0],
                    "calendar_year": idx[1],
                    "detail": f"10km={float(r[10]):.4f} 5km={float(r[5]):.4f}",
                }
            )

    y24 = df[pd.to_numeric(df["calendar_year"], errors="coerce") == 2024]
    y24_5 = y24[pd.to_numeric(y24["buffer_radius_km"], errors="coerce").round(0) == 5]

    if not y24_5.empty and "builtup_change_from_2015_km2" in y24_5.columns:
        q = y24_5["builtup_change_from_2015_km2"].astype(float).quantile(0.99)
        big = y24_5[y24_5["builtup_change_from_2015_km2"].astype(float) > float(q)]
        for _, r in big.iterrows():
            anomalies.append(
                {
                    "anomaly_type": "large_builtup_change_2015_tail",
                    "port_id": r["port_id"],
                    "calendar_year": 2024,
                    "detail": float(r["builtup_change_from_2015_km2"]),
                }
            )

    if not y24_5.empty and "water_to_land_positive_change_from_2015_km2" in y24_5.columns:
        q2 = y24_5["water_to_land_positive_change_from_2015_km2"].astype(float).quantile(0.99)
        big2 = y24_5[
            y24_5["water_to_land_positive_change_from_2015_km2"].astype(float) > float(q2)
        ]
        for _, r in big2.iterrows():
            anomalies.append(
                {
                    "anomaly_type": "large_water_to_land_positive_2015_tail",
                    "port_id": r["port_id"],
                    "calendar_year": 2024,
                    "detail": float(r["water_to_land_positive_change_from_2015_km2"]),
                }
            )

    if not y24.empty and "builtup_change_from_2015_km2" in y24.columns:
        p24 = y24.pivot_table(
            index="port_id",
            columns=pd.to_numeric(y24["buffer_radius_km"], errors="coerce").round(0).astype(int),
            values="builtup_change_from_2015_km2",
            aggfunc="first",
        )
        if 5 in p24.columns and 10 in p24.columns:
            p24["rel_diff"] = (p24[10] - p24[5]).abs() / (p24[5].abs() + p24[10].abs() + 1e-6)
            inc = p24[(p24["rel_diff"] > 0.5) & (p24[5].notna()) & (p24[10].notna())]
            for pid, r in inc.iterrows():
                anomalies.append(
                    {
                        "anomaly_type": "buffer_5_vs_10_inconsistent_builtup",
                        "port_id": pid,
                        "calendar_year": 2024,
                        "detail": f"rel_diff={float(r['rel_diff']):.3f}",
                    }
                )

    pd.DataFrame(summary).to_csv(QC_SUMMARY, index=False, encoding="utf-8")
    pd.DataFrame(anomalies).to_csv(QC_ANOMALIES, index=False, encoding="utf-8")

    manual: set[str] = set()
    if "wpi_location_check_flag" in df.columns:
        manual.update(
            df.loc[pd.to_numeric(df["wpi_location_check_flag"], errors="coerce") != 0, "port_id"]
            .astype(str)
            .tolist()
        )

    if not y24_5.empty:
        if "builtup_change_from_2015_km2" in y24_5.columns:
            manual.update(
                y24_5.nlargest(20, "builtup_change_from_2015_km2")["port_id"].astype(str).tolist()
            )
        if "yard_like_change_from_2015_km2" in y24_5.columns:
            manual.update(
                y24_5.nlargest(20, "yard_like_change_from_2015_km2")["port_id"].astype(str).tolist()
            )
        if "water_to_land_positive_change_from_2015_km2" in y24_5.columns:
            manual.update(
                y24_5.nlargest(20, "water_to_land_positive_change_from_2015_km2")["port_id"]
                .astype(str)
                .tolist()
            )
        bad_img = y24_5[pd.to_numeric(y24_5["image_quality_flag"], errors="coerce") == 1]
        if not bad_img.empty and "builtup_change_from_2015_km2" in bad_img.columns:
            thr = bad_img["builtup_change_from_2015_km2"].astype(float).quantile(0.75)
            manual.update(
                bad_img[bad_img["builtup_change_from_2015_km2"].astype(float) >= float(thr)][
                    "port_id"
                ]
                .astype(str)
                .tolist()
            )

    for a in anomalies:
        if a.get("anomaly_type") in (
            "nested_buffer_violation",
            "buffer_5_vs_10_inconsistent_builtup",
        ):
            manual.add(str(a.get("port_id", "")))

    mlist = sorted(m for m in manual if m and m != "nan")
    pd.DataFrame({"port_id": mlist, "reason": "qc_manual_union_rules"}).to_csv(
        QC_MANUAL, index=False, encoding="utf-8"
    )


def completeness_report(df: pd.DataFrame) -> list[dict]:
    rep = []
    for c in REQUIRED_CORE_COLS:
        if c not in df.columns:
            rep.append({"column": c, "present": False, "non_null_pct": 0.0})
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        rep.append(
            {
                "column": c,
                "present": True,
                "non_null_pct": round(100.0 * s.notna().mean(), 2),
            }
        )
    return rep


def cmd_merge(args: argparse.Namespace) -> None:
    exports = Path(args.exports_dir)
    merged = merge_exports(exports, args.pattern)
    merged.to_csv(PANEL_RAW_BACKUP, index=False, encoding="utf-8")
    cleaned = replace_sentinel(merged)
    cleaned.to_csv(PANEL_FULL, index=False, encoding="utf-8")
    print(f"Wrote {PANEL_FULL} ({len(cleaned)} rows); raw backup {PANEL_RAW_BACKUP}")


def cmd_process(args: argparse.Namespace) -> None:
    panel = norm_cols(pd.read_csv(PANEL_FULL))
    pd.DataFrame(completeness_report(panel)).to_csv(
        ROOT / "gee" / "port_satellite_field_completeness.csv", index=False
    )
    split_buffers(panel)
    build_summaries(panel)
    build_prelim_index(panel)
    print(f"Split -> {PANEL_5KM}, {PANEL_10KM}, {PANEL_MAIN}")
    print(f"Summaries -> {SUMMARY_1524}, {SUMMARY_1824}; index -> {INDEX_PRELIM}")


def cmd_qc(args: argparse.Namespace) -> None:
    panel = norm_cols(pd.read_csv(Path(args.panel)))
    run_qc(panel, n_ports_expected=args.n_ports, n_years=args.n_years)
    print(f"QC -> {QC_SUMMARY}, {QC_ANOMALIES}, {QC_MANUAL}")


def cmd_all(args: argparse.Namespace) -> None:
    cmd_merge(args)
    cmd_process(args)
    cmd_qc(
        argparse.Namespace(
            panel=str(PANEL_FULL), n_ports=args.n_ports, n_years=args.n_years
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Port satellite full panel pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_exports(p):
        p.add_argument("--exports-dir", type=str, default=str(DEFAULT_EXPORTS))
        p.add_argument("--pattern", type=str, default="*.csv")

    p0 = sub.add_parser("merge", help="Merge batch CSVs; backup raw; replace -999")
    add_exports(p0)
    p0.set_defaults(func=cmd_merge)

    p1 = sub.add_parser("process", help="Split buffers, summaries, index")
    p1.set_defaults(func=cmd_process)

    p2 = sub.add_parser("qc", help="QC only")
    p2.add_argument("--panel", type=str, default=str(PANEL_FULL))
    p2.add_argument("--n-ports", type=int, default=368)
    p2.add_argument("--n-years", type=int, default=5)
    p2.set_defaults(func=cmd_qc)

    p3 = sub.add_parser("all", help="merge + process + qc")
    add_exports(p3)
    p3.add_argument("--n-ports", type=int, default=368)
    p3.add_argument("--n-years", type=int, default=5)
    p3.set_defaults(func=cmd_all)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
