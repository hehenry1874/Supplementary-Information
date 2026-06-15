import pandas as pd
import numpy as np
from pathlib import Path
import re

from llm_core_paths import REPO_ROOT, WORKSPACE_ROOT as SCRIPT_DIR


def resolve_evidence_cols(columns, v):
    """Map logical variable key v to (stage_col, snippet_col, year_col) in evidence_wide_combined."""
    cols = set(columns)
    double = f"{v}_stage_stage"
    if double in cols:
        return double, f"{v}_stage_snippet", f"{v}_stage_year"
    single = f"{v}_stage"
    if single in cols:
        return single, f"{v}_snippet", f"{v}_year"
    return None, None, None


# Keys must match Extract-Evidence-Wide.VARIABLES (wide column prefixes).
DIM_MAPPING = {
    "Electrification": [
        "ops_stage",
        "ops_scope",
        "grid_upgrade_stage",
        "ze_berth_target",
        "ops_electrified_operations_stage",
        "electrification_breadth",
    ],
    "Green energy": [
        "renewable_deployment_stage",
        "ppa_greenpower_stage",
        "hydrogen_stage",
        "ammonia_stage",
        "methanol_stage",
        "altfuel_diversity",
        "energy_system_integration",
    ],
    "Governance and investment": [
        "strategy_roadmap_stage",
        "policy_support_stage",
        "capex_funding_stage",
        "official_project_registry",
        "reporting_institutionalization",
    ],
}


def parse_year(year_val, default=None):
    if pd.isna(year_val) or year_val == "":
        return default
    year_str = str(year_val)
    m = re.search(r'(20\d{2})', year_str)
    if m:
        return int(m.group(1))
    return default

def main():
    print("Starting Step 3: Year Coding...")
    
    # 1. Load Combined Evidence
    input_path = SCRIPT_DIR / "evidence_wide_combined.csv"
    if not input_path.exists():
        print("Error: evidence_wide_combined.csv not found.")
        return
    df = pd.read_csv(input_path)
    
    # Create reverse mapping for fast lookup
    var_to_dim = {}
    for dim, vars in DIM_MAPPING.items():
        for v in vars:
            var_to_dim[v] = dim

    # 2. Reshape to Evidence Long (evidence_id x variable)
    long_records = []
    
    for _, row in df.iterrows():
        # Document year: prefer explicit document_date; else access_date from wide export.
        doc_raw = row.get("document_date", "") or row.get("access_date", "")
        doc_year = parse_year(doc_raw, default=2026) 
        
        for v, dim in var_to_dim.items():
            stage_col, snippet_col, year_col = resolve_evidence_cols(df.columns, v)
            if stage_col is None:
                continue

            if stage_col not in row or pd.isna(row[stage_col]):
                continue
                
            stage_val = pd.to_numeric(row[stage_col], errors='coerce')
            if pd.isna(stage_val) or stage_val == 0:
                continue
                
            stage_val = int(stage_val)
            snippet_val = str(row.get(snippet_col, ""))
            evidence_year_raw = row.get(year_col, "")
            extracted_year = parse_year(evidence_year_raw)
            
            # Determine effective year
            # Rule 1A: specific year found and <= 2026
            if extracted_year is not None and extracted_year <= 2026:
                effective_main = extracted_year
                future_flag = 0
                target_future = ""
            # Rule 1D: only year is future target > 2026
            elif extracted_year is not None and extracted_year > 2026:
                effective_main = doc_year
                future_flag = 1
                target_future = extracted_year
            # Rule 1B: fallback to document year
            else:
                effective_main = doc_year
                future_flag = 0
                target_future = ""
                
            # Rule 1C / 6: pre-2015 compression
            pre2015_flag = 0
            if effective_main < 2015:
                effective_main = 2015
                pre2015_flag = 1
                
            # Determine target_horizon_class
            target_class = ""
            if target_future:
                if target_future <= 2030: target_class = "near_term"
                elif target_future <= 2040: target_class = "mid_term"
                else: target_class = "long_term"
                
            # Source Rank 
            # Very basic proxy logic, can be refined based on 'source_type'
            stype = str(row.get('source_type', '')).lower()
            rank = "C"
            if 'gov' in stype or 'authority' in stype: rank = "A"
            elif 'operator' in stype or 'company' in stype: rank = "B"
            elif 'media' in stype or 'press' in stype: rank = "C"
            else: rank = "D"
            
            long_records.append({
                "evidence_id": row['evidence_id'],
                "port_id": row['port_id'],
                "port_name_standard": row['port_name_standard'],
                "country": row['country'],
                "dimension": dim,
                "variable_name": v,
                "stage_code": stage_val,
                "document_year": doc_year,
                "evidence_year_raw": evidence_year_raw,
                "effective_year_main": effective_main,
                "target_year_future": target_future,
                "pre2015_flag": pre2015_flag,
                "future_target_flag": future_flag,
                "target_horizon_class": target_class,
                "source_rank": rank,
                "snippet": snippet_val
            })
            
    df_long = pd.DataFrame(long_records)
    print(f"Generated evidence_long: {len(df_long)} variable-level records.")
    
    if len(df_long) == 0:
        print("No non-zero evidence to process!")
        return
        
    df_long.to_csv(SCRIPT_DIR / "evidence_long.csv", index=False)
    
    # 3. Future Targets Table
    df_future = df_long[df_long['future_target_flag'] == 1].copy()
    # Output specific columns per instructions
    future_cols = [
        "evidence_id", "port_id", "port_name_standard", "dimension", 
        "variable_name", "document_year", "target_year_future", 
        "stage_code", "target_horizon_class", "source_rank", "snippet"
    ]
    # Rename stage_code -> target_stage_claim for future table
    df_future = df_future.rename(columns={"stage_code": "target_stage_claim"})
    # Keep only those columns
    df_future = df_future[[c for c in future_cols if c in df_future.columns]]
    df_future.to_csv(SCRIPT_DIR / "future_targets.csv", index=False)
    print(f"Generated future_targets: {len(df_future)} future records.")
    
    # 4. Evidence-Level Staircase
    staircase_records = []
    years = list(range(2015, 2027)) # 2015 to 2026
    
    for _, row in df_long.iterrows():
        ev_id = row['evidence_id']
        pid = row['port_id']
        dim = row['dimension']
        eff_year = row['effective_year_main']
        stage = row['stage_code']
        
        for y in years:
            # Step-function mapping: 0 before effective_year, stage_code from effective_year onward
            s_curve = 0 if y < eff_year else stage
            staircase_records.append({
                "evidence_id": ev_id,
                "port_id": pid,
                "dimension": dim,
                "year": y,
                "stage_curve": s_curve
            })
            
    df_staircase = pd.DataFrame(staircase_records)
    df_staircase.to_csv(SCRIPT_DIR / "evidence_year_curve.csv", index=False)
    print(f"Generated evidence_year_curve: {len(df_staircase)} staircase data points.")
    
    # Read master ports list to ensure a balanced panel
    master_path = REPO_ROOT / "double_batch_ports_master.csv"
    if not master_path.exists():
        print(f"Error: master port list not found at {master_path}")
        return
    master_df = pd.read_csv(master_path)
    all_ports = master_df[['port_id', 'port_name_standard', 'country']].drop_duplicates()
    
    # 5. Port-Dimension-Year Aggregation
    # Group by port_id, dimension, year, taking max of stage_curve
    df_port_dim = df_staircase.groupby(['port_id', 'dimension', 'year'])['stage_curve'].max().reset_index()
    
    # Create balanced grid
    # port_id x dimension x year
    port_ids = all_ports['port_id'].unique()
    dimensions = list(DIM_MAPPING.keys())
    
    idx = pd.MultiIndex.from_product([port_ids, dimensions, years], names=['port_id', 'dimension', 'year'])
    grid = pd.DataFrame(index=idx).reset_index()
    
    # Merge with actual data
    df_port_dim = pd.merge(grid, df_port_dim, on=['port_id', 'dimension', 'year'], how='left').fillna(0)
    
    # Rule 4: Monotonic Enforcement
    df_port_dim = df_port_dim.sort_values(['port_id', 'dimension', 'year'])
    df_port_dim['stage_port_dim_year_raw'] = df_port_dim.groupby(['port_id', 'dimension'])['stage_curve'].cummax()
    
    # Export dimension panel
    out_dim = pd.merge(df_port_dim, all_ports, on='port_id', how='left')
    out_dim = out_dim[['port_id', 'port_name_standard', 'country', 'dimension', 'year', 'stage_port_dim_year_raw']]
    out_dim.to_csv(SCRIPT_DIR / "port_dimension_year_raw.csv", index=False)
    print(f"Generated port_dimension_year_raw: {len(out_dim)} balanced panel rows.")
    
    # 6. Readiness Panel Wide
    df_wide = df_port_dim.pivot(index=['port_id', 'year'], columns='dimension', values='stage_port_dim_year_raw').reset_index()
    
    rename_map = {
        'Electrification': 'electrification_stage_raw',
        'Green energy': 'green_energy_stage_raw',
        'Governance and investment': 'governance_investment_stage_raw'
    }
    df_wide = df_wide.rename(columns=rename_map)
    for expected_col in rename_map.values():
        if expected_col not in df_wide.columns:
            df_wide[expected_col] = 0
            
    df_wide = df_wide.fillna(0)
    
    # Join port names
    df_wide = pd.merge(df_wide, all_ports, on='port_id', how='left')
    
    cols = ['port_id', 'port_name_standard', 'country', 'year', 'electrification_stage_raw', 'green_energy_stage_raw', 'governance_investment_stage_raw']
    df_wide = df_wide[cols]
    
    df_wide.to_csv(SCRIPT_DIR / "readiness_panel_raw.csv", index=False)
    print(f"Generated readiness_panel_raw: {len(df_wide)} balanced port-year rows.")

if __name__ == "__main__":
    main()
