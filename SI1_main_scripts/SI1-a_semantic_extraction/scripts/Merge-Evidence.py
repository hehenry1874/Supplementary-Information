import pandas as pd

from llm_core_paths import WORKSPACE_ROOT as SCRIPT_DIR


def main():
    input_paths = [
        ("Pass 1", SCRIPT_DIR / "evidence_wide.csv"),
        ("Pass 2", SCRIPT_DIR / "evidence_wide_pass2.csv"),
        ("Supplement", SCRIPT_DIR / "evidence_wide_supplement.csv"),
        ("Major sparse supplement", SCRIPT_DIR / "evidence_wide_major_sparse_supplement.csv"),
        ("Targeted supplement", SCRIPT_DIR / "evidence_wide_targeted_supplement.csv"),
    ]
    out_path = SCRIPT_DIR / "evidence_wide_combined.csv"

    frames = []
    for label, path in input_paths:
        if path.exists():
            df = pd.read_csv(path)
            frames.append(df)
            print(f"Loaded {len(df)} rows from {label}: {path}")
        elif label == "Pass 1":
            print("Pass 1 file not found.")
            return
        else:
            print(f"{label} file not found; skipping.")

    combined = pd.concat(frames, ignore_index=True)
        
    # Drop exact duplicates if any
    combined = combined.drop_duplicates(subset=["evidence_id"], keep="first")
    
    # Optional: fill NaNs with empty string
    combined = combined.fillna("")
    
    combined.to_csv(out_path, index=False)
    print(f"Combined evidence saved to {out_path} with {len(combined)} rows.")

if __name__ == "__main__":
    main()
