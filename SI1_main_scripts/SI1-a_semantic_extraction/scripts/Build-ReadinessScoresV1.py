import re
from pathlib import Path

import pandas as pd

from llm_core_paths import WORKSPACE_ROOT as SCRIPT_DIR
RAW_PATH = SCRIPT_DIR / "readiness_panel_raw.csv"
FUTURE_PATH = SCRIPT_DIR / "future_targets.csv"
SCORE_PATH = SCRIPT_DIR / "readiness_panel_score_v1.csv"
SUMMARY_PATH = SCRIPT_DIR / "scoring_summary.csv"
QC_MD_PATH = SCRIPT_DIR / "qc_scoring_report.md"
QC_ISSUES_PATH = SCRIPT_DIR / "qc_scoring_issues.csv"

YEAR_START = 2015
YEAR_END = 2026
YEARS = list(range(YEAR_START, YEAR_END + 1))

REQUIRED_COLUMNS = [
    "port_id",
    "port_name_standard",
    "country",
    "year",
    "electrification_stage_raw",
    "green_energy_stage_raw",
    "governance_investment_stage_raw",
]

STAGE_COLUMNS = [
    "electrification_stage_raw",
    "green_energy_stage_raw",
    "governance_investment_stage_raw",
]

DIMENSION_SCORE_COLUMNS = [
    "electrification_score_raw",
    "green_energy_score_raw",
    "governance_investment_score_raw",
]


def parse_year(value):
    if pd.isna(value):
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    if not match:
        return None
    return int(match.group(0))


def clamp01(series):
    return series.clip(lower=0, upper=1)


def load_raw_panel():
    if not RAW_PATH.exists():
        raise FileNotFoundError(f"Missing required input: {RAW_PATH}")
    df = pd.read_csv(RAW_PATH)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"readiness_panel_raw.csv is missing required columns: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for col in STAGE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def qc_panel(df):
    issues = []

    # Required year coverage: every port should have exactly one row for each year in 2015-2026.
    counts = df.groupby("port_id")["year"].agg(["count", lambda s: set(s.dropna().astype(int))])
    counts = counts.rename(columns={"<lambda_0>": "year_set"})
    for port_id, row in counts.iterrows():
        year_set = row["year_set"]
        missing_years = sorted(set(YEARS) - year_set)
        extra_years = sorted(year_set - set(YEARS))
        if row["count"] != len(YEARS) or missing_years or extra_years:
            issues.append(
                {
                    "issue_type": "unbalanced_panel",
                    "port_id": port_id,
                    "column": "year",
                    "details": f"row_count={row['count']}; missing={missing_years}; extra={extra_years}",
                }
            )

    # Stage bounds and missing checks.
    for col in STAGE_COLUMNS:
        bad = df[df[col].isna() | (df[col] < 0) | (df[col] > 5)]
        for _, r in bad.iterrows():
            issues.append(
                {
                    "issue_type": "stage_out_of_bounds_or_missing",
                    "port_id": r["port_id"],
                    "column": col,
                    "details": f"year={r['year']}; value={r[col]}",
                }
            )

    # Monotonicity by port and dimension.
    sorted_df = df.sort_values(["port_id", "year"])
    for col in STAGE_COLUMNS:
        diffs = sorted_df.groupby("port_id")[col].diff()
        bad = sorted_df[diffs < -1e-9]
        for _, r in bad.iterrows():
            issues.append(
                {
                    "issue_type": "monotonicity_violation",
                    "port_id": r["port_id"],
                    "column": col,
                    "details": f"year={r['year']}; value={r[col]}",
                }
            )

    return pd.DataFrame(issues)


def add_dimension_scores(df):
    out = df.copy()
    out["electrification_score_raw"] = clamp01(out["electrification_stage_raw"] / 5)
    out["green_energy_score_raw"] = clamp01(out["green_energy_stage_raw"] / 5)
    out["governance_investment_score_raw"] = clamp01(out["governance_investment_stage_raw"] / 5)

    out["total_readiness_score_eq"] = (
        out["electrification_score_raw"]
        + out["green_energy_score_raw"]
        + out["governance_investment_score_raw"]
    ) / 3

    out["total_readiness_score_impl"] = (
        0.40 * out["electrification_score_raw"]
        + 0.35 * out["green_energy_score_raw"]
        + 0.25 * out["governance_investment_score_raw"]
    )

    out["total_readiness_score_gov"] = (
        0.30 * out["electrification_score_raw"]
        + 0.30 * out["green_energy_score_raw"]
        + 0.40 * out["governance_investment_score_raw"]
    )
    return out


def build_future_bonus(panel):
    bonus = panel[["port_id", "year"]].copy()
    bonus["future_target_bonus_raw"] = 0.0

    if not FUTURE_PATH.exists():
        return bonus, False, 0

    ft = pd.read_csv(FUTURE_PATH)
    if ft.empty:
        return bonus, True, 0

    required = {"port_id", "document_year", "target_year_future"}
    if not required.issubset(set(ft.columns)):
        return bonus, True, 0

    rows = []
    for _, r in ft.iterrows():
        port_id = r["port_id"]
        doc_year = parse_year(r.get("document_year"))
        target_year = parse_year(r.get("target_year_future"))
        if not port_id or doc_year is None or target_year is None:
            continue

        start_year = min(max(doc_year, YEAR_START), YEAR_END)
        if target_year <= 2030:
            bonus_value = 0.05
        elif target_year <= 2040:
            bonus_value = 0.03
        else:
            bonus_value = 0.01

        for year in YEARS:
            if year >= start_year:
                rows.append(
                    {
                        "port_id": port_id,
                        "year": year,
                        "future_target_bonus_raw": bonus_value,
                    }
                )

    if not rows:
        return bonus, True, 0

    ft_bonus = pd.DataFrame(rows)
    ft_bonus = (
        ft_bonus.groupby(["port_id", "year"], as_index=False)["future_target_bonus_raw"]
        .max()
    )
    bonus = bonus.drop(columns=["future_target_bonus_raw"]).merge(
        ft_bonus, on=["port_id", "year"], how="left"
    )
    bonus["future_target_bonus_raw"] = bonus["future_target_bonus_raw"].fillna(0.0)
    return bonus, True, len(ft_bonus)


def write_summary(df):
    summary = (
        df.groupby("year")
        .agg(
            electrification_score_raw_mean=("electrification_score_raw", "mean"),
            green_energy_score_raw_mean=("green_energy_score_raw", "mean"),
            governance_investment_score_raw_mean=("governance_investment_score_raw", "mean"),
            total_readiness_score_eq_mean=("total_readiness_score_eq", "mean"),
            total_readiness_score_eq_median=("total_readiness_score_eq", "median"),
            ports_score_gt_0=("total_readiness_score_eq", lambda s: int((s > 0).sum())),
            ports_score_ge_0_4=("total_readiness_score_eq", lambda s: int((s >= 0.4).sum())),
            ports_score_ge_0_6=("total_readiness_score_eq", lambda s: int((s >= 0.6).sum())),
        )
        .reset_index()
    )
    summary.to_csv(SUMMARY_PATH, index=False)
    return summary


def write_qc_report(df, issues, future_exists, future_bonus_rows):
    port_count = df["port_id"].nunique()
    expected_rows = port_count * len(YEARS)
    is_balanced = len(df) == expected_rows and issues[issues["issue_type"] == "unbalanced_panel"].empty if not issues.empty else len(df) == expected_rows
    out_of_bounds = 0 if issues.empty else int((issues["issue_type"] == "stage_out_of_bounds_or_missing").sum())
    monotonic_violations = 0 if issues.empty else int((issues["issue_type"] == "monotonicity_violation").sum())

    stage_4_5_counts = {}
    for col in STAGE_COLUMNS:
        stage_4_5_counts[col] = int(df.loc[df[col] >= 4, "port_id"].nunique())

    port_max = df.groupby("port_id")[STAGE_COLUMNS].max()
    no_evidence_ports = int((port_max.max(axis=1) == 0).sum())

    lines = [
        "# QC Scoring Report",
        "",
        f"- Total rows: {len(df)}",
        f"- Unique ports: {port_count}",
        f"- Expected balanced rows (ports x 2015-2026): {expected_rows}",
        f"- Complete 2015-2026 balanced panel: {'YES' if is_balanced else 'NO'}",
        f"- Stage out-of-range or missing issues: {out_of_bounds}",
        f"- Monotonicity violations: {monotonic_violations}",
        "",
        "## Ports Reaching Stage 4 or 5",
        "",
        f"- Electrification: {stage_4_5_counts['electrification_stage_raw']}",
        f"- Green energy: {stage_4_5_counts['green_energy_stage_raw']}",
        f"- Governance and investment: {stage_4_5_counts['governance_investment_stage_raw']}",
        "",
        f"- Ports with no evidence in any dimension across all years: {no_evidence_ports}",
        "",
        "## Future Target Bonus",
        "",
        f"- future_targets.csv present: {'YES' if future_exists else 'NO'}",
        f"- Port-year bonus cells populated from future targets: {future_bonus_rows}",
        "- Bonus is carried forward from document_year to 2026 and does not alter raw stages or dimension scores.",
    ]

    if not issues.empty:
        lines.extend(
            [
                "",
                "## QC Issues",
                "",
                f"- Detailed issues written to `{QC_ISSUES_PATH.name}`.",
            ]
        )

    QC_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    df = load_raw_panel()
    issues = qc_panel(df)
    if not issues.empty:
        issues.to_csv(QC_ISSUES_PATH, index=False)
    elif QC_ISSUES_PATH.exists():
        QC_ISSUES_PATH.unlink()

    score = add_dimension_scores(df)
    future_bonus, future_exists, future_bonus_rows = build_future_bonus(score)
    score = score.merge(future_bonus, on=["port_id", "year"], how="left")
    score["future_target_bonus_raw"] = score["future_target_bonus_raw"].fillna(0.0)
    score["total_readiness_score_bonus"] = (
        score["total_readiness_score_eq"] + score["future_target_bonus_raw"]
    ).clip(upper=1)

    output_columns = [
        "port_id",
        "port_name_standard",
        "country",
        "year",
        "electrification_stage_raw",
        "green_energy_stage_raw",
        "governance_investment_stage_raw",
        "electrification_score_raw",
        "green_energy_score_raw",
        "governance_investment_score_raw",
        "total_readiness_score_eq",
        "total_readiness_score_impl",
        "total_readiness_score_gov",
        "future_target_bonus_raw",
        "total_readiness_score_bonus",
    ]
    score[output_columns].to_csv(SCORE_PATH, index=False)
    write_summary(score)
    write_qc_report(df, issues, future_exists, future_bonus_rows)

    print(f"Wrote {SCORE_PATH} ({len(score)} rows)")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {QC_MD_PATH}")
    if not issues.empty:
        print(f"Wrote {QC_ISSUES_PATH} ({len(issues)} issues)")


if __name__ == "__main__":
    main()
