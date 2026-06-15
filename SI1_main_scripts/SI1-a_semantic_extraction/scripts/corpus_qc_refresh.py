"""
Scan scripts/corpus/*.txt for failed or empty captures; optionally refetch from URLs.

Usage:
  python corpus_qc_refresh.py                    # print summary only
  python corpus_qc_refresh.py --refetch-bad    # re-download files classified bad/fetch_error
  python corpus_qc_refresh.py --refetch-list corpus_bad_files.txt
  python corpus_qc_refresh.py --write-bad-list corpus_bad_files.txt

Requires running from repo or with cwd=scripts (same as Build-Corpus).
"""

from __future__ import annotations

import argparse
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from llm_core_paths import WORKSPACE_ROOT, LLM_CORE_DIR

SCRIPT_DIR = WORKSPACE_ROOT
CORPUS_DIR = SCRIPT_DIR / "corpus"


def load_build_corpus():
    path = LLM_CORE_DIR / "Build-Corpus.py"
    spec = importlib.util.spec_from_file_location("build_corpus", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify_file(path: Path, min_body_bytes: int) -> tuple[str, int]:
    size = path.stat().st_size if path.exists() else 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable", size

    head = text[:12000].lower()
    if "[fetch error]" in text or "[unknown error]" in text:
        return "fetch_or_error_placeholder", size
    if "403 forbidden" in head or " 403 " in head or "http error 403" in head:
        return "http_403_signal", size
    if "access denied" in head and size < 8000:
        return "likely_paywall_or_denied", size
    body = text
    if "--- source info ---" in text.lower():
        idx = text.lower().find("-------------------")
        if idx != -1:
            body = text[idx + len("-------------------") :]
    body_strip = body.strip()
    if len(body_strip.encode("utf-8")) < min_body_bytes:
        return "empty_or_tiny_body", size
    return "ok", size


def collect_rows(build_mod, include_manual: bool):
    paths = [build_mod.INPUT_CSV]
    manual = SCRIPT_DIR / "manual_url_supplement.csv"
    if include_manual and manual.exists():
        paths.append(manual)
    by_key: dict[tuple[str, str], dict] = {}
    for p in paths:
        if not p.exists():
            continue
        import csv

        with p.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("url"):
                    continue
                key = (row.get("port_id", ""), str(row.get("candidate_rank", "")))
                by_key[key] = row
    return list(by_key.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-body-bytes", type=int, default=400)
    ap.add_argument("--refetch-bad", action="store_true", help="Re-download files not classified ok")
    ap.add_argument("--refetch-list", type=Path, help="Text file with one archived filename per line")
    ap.add_argument("--write-bad-list", type=Path, help="Write bad archived_file_path names here")
    ap.add_argument("--with-manual-urls", action="store_true", help="Merge manual_url_supplement.csv like Build-Corpus")
    args = ap.parse_args()

    build_mod = load_build_corpus()
    counts: dict[str, int] = {}
    bad_files: list[tuple[str, str, int]] = []

    for fp in sorted(CORPUS_DIR.glob("*.txt")):
        label, size = classify_file(fp, args.min_body_bytes)
        counts[label] = counts.get(label, 0) + 1
        if label != "ok":
            bad_files.append((fp.name, label, size))

    print(f"Corpus directory: {CORPUS_DIR}")
    for k in sorted(counts.keys()):
        print(f"  {k}: {counts[k]}")
    print(f"Total txt: {sum(counts.values())}")

    target_names: set[str] = set()
    if args.refetch_list and args.refetch_list.exists():
        target_names |= {
            line.strip()
            for line in args.refetch_list.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
    if args.refetch_bad:
        target_names |= {name for name, lab, _ in bad_files if lab != "ok"}

    if args.write_bad_list:
        lines = sorted({name for name, lab, _ in bad_files if lab != "ok"})
        args.write_bad_list.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        print(f"Wrote {args.write_bad_list} ({len(lines)} paths)")

    if not target_names and (args.refetch_bad or args.refetch_list):
        print("No refetch targets.")
        return

    rows = collect_rows(build_mod, include_manual=args.with_manual_urls)
    row_by_file = {
        f"{r['port_id']}_{r['candidate_rank']}.txt": r
        for r in rows
        if r.get("port_id") and r.get("candidate_rank")
    }

    refetched = 0
    missing_key = []
    to_run: list[tuple[str, dict]] = []
    for name in sorted(target_names):
        row = row_by_file.get(name)
        if not row:
            missing_key.append(name)
            continue
        to_run.append((name, row))

    def _one(item):
        name, row = item
        return name, build_mod.download_and_extract(row, force=True)

    with ThreadPoolExecutor(max_workers=15) as ex:
        futs = {ex.submit(_one, item): item[0] for item in to_run}
        for i, fut in enumerate(as_completed(futs), 1):
            name = futs[fut]
            try:
                _, _, status = fut.result()
                print(f"{name}: {status}")
            except Exception as exc:
                print(f"{name}: WORKER_ERROR {exc}")
            refetched += 1
            if refetched % 50 == 0 or refetched == len(to_run):
                print(f"[refetch progress] {refetched}/{len(to_run)}")

    if missing_key:
        print(f"[warn] No candidate row for {len(missing_key)} files (check ranks / manual supplement)")
        for n in missing_key[:20]:
            print(f"    {n}")


if __name__ == "__main__":
    main()
