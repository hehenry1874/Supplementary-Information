import os
import csv
import json
import time
import uuid
import datetime
from pathlib import Path
from llm_core_paths import WORKSPACE_ROOT
from openai import OpenAI

SYSTEM_PROMPT = """
You are an expert port sustainability analyst. Your task is to extract structured readiness evidence from the provided document.
You must output a JSON object evaluating 18 specific readiness variables across 3 dimensions.

READINESS STAGE DEFINITIONS (0 to 5):
0 = No evidence / not mentioned in this document
1 = Vision / commitment / generic statement
2 = Planning / study / announced project / roadmap / MOU / feasibility
3 = Construction / tender / procurement / secured funding / signed implementation contract
4 = Operational / commissioned / implemented / active deployment
5 = Fully operated / achieved fully alternative energy provision

DIMENSION 1: Electrification
- ops_stage: Onshore Power Supply (Shore power / Cold ironing) deployment.
- ops_scope: Extent of OPS (e.g., number of berths or vessel types covered).
- grid_upgrade_stage: Upgrades to the port's electrical grid/substations to support high power demand.
- ze_berth_target: Zero-emission berth targets or mandates.
- ops_electrified_operations_stage: Electrification of port cargo handling equipment (e.g., e-RTGs, electric trucks, electric cranes).
- electrification_breadth: How broadly electrification is adopted across different port operations.

DIMENSION 2: Green Energy
- renewable_deployment_stage: On-site renewable energy generation (e.g., solar panels, wind turbines at the port).
- ppa_greenpower_stage: Power Purchase Agreements (PPA) or procurement of green electricity from the grid.
- hydrogen_stage: Hydrogen fuel production, storage, or bunkering.
- ammonia_stage: Ammonia fuel facilities or bunkering.
- methanol_stage: Green methanol fuel facilities or bunkering.
- altfuel_diversity: Variety of alternative fuels supported.
- energy_system_integration: Integration of energy systems (e.g., microgrids, smart grids, sector coupling).

DIMENSION 3: Governance and Investment
- strategy_roadmap_stage: Presence of a formal decarbonization strategy, climate action plan, or roadmap.
- policy_support_stage: Port-level policies, tariff discounts for green ships, or environmental subsidies.
- capex_funding_stage: Capital expenditure commitments, grants, or secured funding for green infrastructure.
- official_project_registry: Structured tracking or registry of sustainability projects.
- reporting_institutionalization: Institutionalized ESG/sustainability reporting (e.g., ISO 14001, annual sustainability reports).

JSON OUTPUT FORMAT:
You must return a JSON object with exactly three keys: "electrification", "green_energy", "governance_investment".
Inside each, provide the variables as keys.
For EACH variable, provide an object with:
- "stage_code": integer (0-5)
- "snippet_original": exact excerpt from the text (leave empty if stage is 0)
- "snippet_english": English translation/summary of the excerpt (leave empty if stage is 0)
- "evidence_year": The year associated with the evidence (YYYY), or "" if not found.

Example structure:
{
  "electrification": {
    "ops_stage": {"stage_code": 4, "snippet_original": "...", "snippet_english": "...", "evidence_year": "2024"},
    "ops_scope": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "grid_upgrade_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "ze_berth_target": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "ops_electrified_operations_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "electrification_breadth": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""}
  },
  "green_energy": {
    "renewable_deployment_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "ppa_greenpower_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "hydrogen_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "ammonia_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "methanol_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "altfuel_diversity": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "energy_system_integration": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""}
  },
  "governance_investment": {
    "strategy_roadmap_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "policy_support_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "capex_funding_stage": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "official_project_registry": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""},
    "reporting_institutionalization": {"stage_code": 0, "snippet_original": "", "snippet_english": "", "evidence_year": ""}
  }
}

RULES:
1. ONLY assign stage > 0 if there is explicit evidence in the text. Do not guess.
2. If the text does not mention a variable, assign stage 0.
3. Be strictly literal. Do not inflate stages (e.g., a "plan" is 2, not 3 or 4).
"""

VARIABLES = [
    # Electrification
    "ops_stage", "ops_scope", "grid_upgrade_stage", "ze_berth_target", 
    "ops_electrified_operations_stage", "electrification_breadth",
    # Green energy
    "renewable_deployment_stage", "ppa_greenpower_stage", "hydrogen_stage", 
    "ammonia_stage", "methanol_stage", "altfuel_diversity", "energy_system_integration",
    # Governance & investment
    "strategy_roadmap_stage", "policy_support_stage", "capex_funding_stage", 
    "official_project_registry", "reporting_institutionalization"
]

def extract_evidence(api_key, model_name, port_info, source_info, text_content):
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )

    user_prompt = f"""
    Analyze the following document for Port: {port_info['port_name_standard']} ({port_info['country']})
    
    DOCUMENT INFO:
    Title: {source_info['source_title']}
    URL: {source_info['url']}
    Organization: {source_info['source_organization']}
    
    DOCUMENT TEXT:
    {text_content[:80000]}
    """

    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        content = completion.choices[0].message.content
        if not content:
            return None
        
        data = json.loads(content)
        return data
    except Exception as e:
        print(f"  [ERROR] Grok API Error for {port_info['port_id']}: {e}")
        return None

def main():
    import sys
    import argparse

    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reprocess-paths",
        nargs="*",
        default=[],
        metavar="FILE",
        help="Basenames like PLSCI_000001_1.txt: remove matching rows from evidence_wide*.csv then re-run API for those corpus files.",
    )
    parser.add_argument(
        "--reprocess-from-file",
        type=Path,
        default=None,
        help="Text file with one basename per line (avoids Windows CLI length limits).",
    )
    args = parser.parse_args()

    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        print("ERROR: XAI_API_KEY environment variable not set.")
        return

    model_name = os.environ.get("XAI_MODEL", "grok-4.3")
    print(f"Using Model: {model_name}")

    SCRIPT_DIR = WORKSPACE_ROOT
    from wide_csv_utils import collect_reprocess_basenames, load_merged_candidates, strip_archived_paths

    reprocess = collect_reprocess_basenames(args.reprocess_paths, args.reprocess_from_file)
    if reprocess:
        strip_archived_paths(SCRIPT_DIR, reprocess)

    basename_filter = reprocess if reprocess else None

    out_csv = SCRIPT_DIR / "evidence_wide.csv"
    out_notes = SCRIPT_DIR / "evidence_extraction_notes_wide.md"
    corpus_dir = SCRIPT_DIR / "corpus"

    # Load candidates (includes manual_url_supplement.csv when present)
    candidates = [
        c
        for c in load_merged_candidates(SCRIPT_DIR)
        if c.get("low_coverage_flag") != "TRUE" and c.get("url")
    ]

    # Determine processed files
    processed_files = set()
    if out_csv.exists():
        with open(out_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fp = row.get("archived_file_path")
                if fp:
                    processed_files.add(fp)

    # Build headers
    headers = [
        "evidence_id", "port_id", "port_name_standard", "country", 
        "source_title", "source_organization", "url", "document_type", "access_date", "archived_file_path"
    ]
    for var in VARIABLES:
        headers.extend([f"{var}_stage", f"{var}_snippet", f"{var}_year"])

    if not out_csv.exists():
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

    today_str = datetime.date.today().isoformat()
    print(f"Found {len(candidates)} valid candidate sources.")
    
    for c in candidates:
        pid = c['port_id']
        rank = c['candidate_rank']
        file_path = f"{pid}_{rank}.txt"
        full_path = corpus_dir / file_path

        if file_path in processed_files:
            continue

        if basename_filter is not None and file_path not in basename_filter:
            continue
        
        if not full_path.exists():
            continue
            
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if "[FETCH ERROR]" in content or "[UNKNOWN ERROR]" in content:
            doc_type = "Search Snippet Only"
        elif "[DOCUMENT TYPE: PDF]" in content:
            doc_type = "PDF"
        else:
            doc_type = "HTML"
            
        print(f"Processing {pid} Rank {rank} ({c['port_name_standard']})...")
        
        port_info = {"port_id": pid, "port_name_standard": c["port_name_standard"], "country": c["country"]}
        
        data = extract_evidence(api_key, model_name, port_info, c, content)
        
        if data is None:
            time.sleep(5)
            continue
            
        # Check if there's ANY evidence > 0
        has_evidence = False
        row_data = {
            "evidence_id": str(uuid.uuid4()),
            "port_id": pid,
            "port_name_standard": port_info["port_name_standard"],
            "country": port_info["country"],
            "source_title": c.get("source_title", ""),
            "source_organization": c.get("source_organization", ""),
            "url": c.get("url", ""),
            "document_type": doc_type,
            "access_date": today_str,
            "archived_file_path": file_path
        }
        
        # Flatten the JSON
        note_items = []
        for category in ["electrification", "green_energy", "governance_investment"]:
            cat_data = data.get(category, {})
            for var, var_data in cat_data.items():
                if var not in VARIABLES:
                    continue
                if not isinstance(var_data, dict):
                    continue
                stage = var_data.get("stage_code", 0)
                snippet = var_data.get("snippet_english", var_data.get("snippet_original", ""))
                year = var_data.get("evidence_year", "")
                
                row_data[f"{var}_stage"] = stage
                row_data[f"{var}_snippet"] = snippet
                row_data[f"{var}_year"] = year
                
                if isinstance(stage, int) and stage > 0:
                    has_evidence = True
                    note_items.append(f"[{var}] Stage {stage}: {snippet[:100]}... ({year})")
        
        if not has_evidence:
            print(f"  -> No evidence found, skipping.")
            processed_files.add(file_path)
            time.sleep(1)
            continue
            
        def write_with_retry(filepath, mode, content_func, max_retries=10):
            for attempt in range(max_retries):
                try:
                    with open(filepath, mode, encoding="utf-8", newline="") as f:
                        content_func(f)
                    return True
                except PermissionError:
                    print(f"  [WARNING] Permission denied to write {filepath}. Is it open in Excel? Retrying in 5 seconds...")
                    time.sleep(5)
            print(f"  [ERROR] Failed to write to {filepath} after {max_retries} attempts.")
            return False

        # Write to CSV
        def write_csv(f):
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writerow(row_data)
            
        if not write_with_retry(out_csv, "a", write_csv):
            continue
                
        # Write to notes
        def write_notes(f):
            f.write(f"## {pid} - {port_info['port_name_standard']}\n")
            f.write(f"- **Source:** {c.get('source_title')} ({c.get('url')})\n")
            f.write(f"- **Extracted {len(note_items)} valid signals:**\n")
            for note in note_items:
                f.write(f"  - {note}\n")
            f.write("\n")
            
        write_with_retry(out_notes, "a", write_notes)
            
        processed_files.add(file_path)
        time.sleep(2)

    print("Evidence Extraction Completed!")

if __name__ == "__main__":
    main()
