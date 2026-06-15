"""Path layout for the LLM_core bundle."""

from pathlib import Path

# This folder (core scripts you can copy elsewhere).
LLM_CORE_DIR = Path(__file__).resolve().parent
# Main project data directory: sibling folder with corpus/, *.csv, candidate_sources.csv, etc.
WORKSPACE_ROOT = LLM_CORE_DIR.parent
# Repository root (parent of scripts/), used for e.g. double_batch_ports_master.csv
REPO_ROOT = WORKSPACE_ROOT.parent
