"""Helpers for maintaining evidence_wide*.csv partials before merge."""

import csv
from pathlib import Path

WIDE_PARTIAL_NAMES = [
    "evidence_wide.csv",
    "evidence_wide_pass2.csv",
    "evidence_wide_supplement.csv",
    "evidence_wide_major_sparse_supplement.csv",
    "evidence_wide_targeted_supplement.csv",
]


def strip_archived_paths(script_dir: Path, archived_paths: set[str]) -> None:
    """Remove rows whose archived_file_path is in archived_paths (in place)."""
    if not archived_paths:
        return
    for name in WIDE_PARTIAL_NAMES:
        path = script_dir / name
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            if not fieldnames or "archived_file_path" not in fieldnames:
                continue
            rows = [
                r
                for r in reader
                if (r.get("archived_file_path") or "") not in archived_paths
            ]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def collect_reprocess_basenames(inline: list[str], from_file: Path | None) -> set[str]:
    """Merge CLI path list with one basename per line from file (optional)."""
    names = {p.strip() for p in inline if p and p.strip()}
    if from_file and from_file.exists():
        text = from_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line)
    return names


def load_merged_candidates(script_dir: Path) -> list[dict]:
    """Merge candidate_sources.csv with manual_url_supplement.csv (later wins on same port_id+rank)."""
    paths = [script_dir / "candidate_sources.csv"]
    manual = script_dir / "manual_url_supplement.csv"
    if manual.exists():
        paths.append(manual)
    by_key: dict[tuple[str, str], dict] = {}
    for p in paths:
        if not p.exists():
            continue
        with p.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if not row.get("url"):
                    continue
                by_key[(row.get("port_id", ""), str(row.get("candidate_rank", "")))] = row
    return list(by_key.values())
