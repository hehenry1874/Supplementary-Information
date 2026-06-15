"""
Compare per-dimension max stages implied by evidence_wide_combined.csv
against readiness_panel_raw.csv (year=2026) after year-coding.

Flags ports where the wide table shows stage>0 but the panel is still 0 for that dimension.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pandas as pd

from llm_core_paths import WORKSPACE_ROOT as SCRIPT_DIR, LLM_CORE_DIR

PANEL_DIM_COL = {
    "Electrification": "electrification_stage_raw",
    "Green energy": "green_energy_stage_raw",
    "Governance and investment": "governance_investment_stage_raw",
}


def _load_year_coding():
    spec = importlib.util.spec_from_file_location(
        "execute_year_coding", LLM_CORE_DIR / "Execute-YearCoding.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wide_max_stage_by_port_dim(df: pd.DataFrame, yc) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        pid = row.get("port_id")
        if pd.isna(pid):
            continue
        for dim, var_keys in yc.DIM_MAPPING.items():
            m = 0
            for v in var_keys:
                sc, _, _ = yc.resolve_evidence_cols(df.columns, v)
                if sc is None:
                    continue
                val = pd.to_numeric(row.get(sc), errors="coerce")
                if pd.isna(val) or val <= 0:
                    continue
                m = max(m, int(val))
            if m > 0:
                rows.append({"port_id": pid, "dimension": dim, "wide_max_stage": m})
    if not rows:
        return pd.DataFrame(columns=["port_id", "dimension", "wide_max_stage"])
    out = pd.DataFrame(rows)
    return out.groupby(["port_id", "dimension"], as_index=False)["wide_max_stage"].max()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--out", type=Path, help="Write mismatch table CSV")
    args = ap.parse_args()

    yc = _load_year_coding()
    wide_path = SCRIPT_DIR / "evidence_wide_combined.csv"
    raw_path = SCRIPT_DIR / "readiness_panel_raw.csv"
    if not wide_path.exists():
        print(f"Missing {wide_path}")
        return
    if not raw_path.exists():
        print(f"Missing {raw_path}")
        return

    df_wide = pd.read_csv(wide_path)
    wide_agg = wide_max_stage_by_port_dim(df_wide, yc)

    panel = pd.read_csv(raw_path)
    panel_y = panel[panel["year"] == args.year].copy()

    records = []
    for _, wrow in wide_agg.iterrows():
        pid = wrow["port_id"]
        dim = wrow["dimension"]
        wst = int(wrow["wide_max_stage"])
        col = PANEL_DIM_COL.get(dim)
        if not col:
            continue
        match = panel_y[panel_y["port_id"] == pid]
        if match.empty:
            continue
        pst = int(pd.to_numeric(match.iloc[0].get(col), errors="coerce") or 0)
        if wst > 0 and pst == 0:
            records.append(
                {
                    "port_id": pid,
                    "port_name_standard": match.iloc[0].get("port_name_standard", ""),
                    "dimension": dim,
                    "wide_max_stage": wst,
                    f"panel_{args.year}": pst,
                }
            )

    mism = pd.DataFrame(records)
    print(f"Wide rows: {len(df_wide)}; port-dim signals in wide: {len(wide_agg)}")
    print(f"Mismatches (wide>0, panel {args.year}==0): {len(mism)}")
    if not mism.empty:
        print(mism.head(30).to_string(index=False))
    if args.out and not mism.empty:
        mism.to_csv(args.out, index=False)
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
