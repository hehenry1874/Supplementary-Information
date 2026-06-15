"""
Remedial re-fetch of the ~400 web pages still classified as "weak" after corpus QC
(without discarding any URL).

================================================================================
1. Problem identification (consistent with corpus_qc_refresh.classify_file)
================================================================================
1) fetch_or_error_placeholder
   - Network timeout, TLS, DNS, 4xx/5xx, etc.; the corpus currently holds only a
     [FETCH ERROR] placeholder.
2) http_403_signal / likely_paywall_or_denied
   - Anti-scraping, geo-restriction, login-required, CDN challenge (e.g. Cloudflare),
     or only "browser-like" requests are allowed.
3) empty_or_tiny_body
   - HTTP 200 but very short body: common for SPA shells, redirect pages, human-
     verification pages, or content that needs cookies / a second request.

Limitations of the original Build-Corpus.py (which amplified the issues above):
- Minimal User-Agent + Accept only, easily detected as a script.
- No Session, no Referer, no browser-family request headers.
- No backoff/retry; gives up immediately on 403/429.
- High concurrency more easily triggers site rate limiting.

================================================================================
2. Remediation strategy (this script)
================================================================================
- Reuse Build-Corpus's HTML/PDF parsing and corpus write paths, so downstream
  Extract scripts need no format change.
- Only re-fetch the {port_id}_{rank}.txt rows still classified as "weak"
  (use --dry-run first to inspect the list and label distribution).
- Try multiple strategies per URL in order (same Session reuses cookies):
  (a) Rich Chrome-family headers + same-origin Referer.
  (b) Alternate UA + Google referral Referer (some sites only accept referer traffic).
  (c) Optional: if cloudscraper is installed, use it to bypass some Cloudflare challenges.
  (d) On further failure: retry with minimal headers (avoid Sec-* vs site-policy conflicts).
- Exponential backoff + jitter; honor Retry-After for 429/503; gentle per-site
  concurrency (default 6 workers).
- On persistent failure, write back the same [FETCH ERROR] placeholder as the original
  (so it can later be re-linked manually or routed through manual_url_supplement).

================================================================================
3. Usage (run from scripts/)
================================================================================
  # Only count weak links and labels (no disk writes)
  python Build-Corpus-Remedial.py --dry-run

  # Actual remedial fetch (recommended)
  python Build-Corpus-Remedial.py --max-workers 6

  # Only handle 403-type cases (try a subset first)
  python Build-Corpus-Remedial.py --labels http_403_signal likely_paywall_or_denied

  # Optional: skip cloudscraper (even if installed)
  python Build-Corpus-Remedial.py --no-cloudscraper

Dependencies: same as Build-Corpus (requests, bs4, pymupdf). Optionally
pip install cloudscraper to strengthen handling of CF-protected sites.
================================================================================
"""

from __future__ import annotations

import argparse
import importlib.util
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter

from llm_core_paths import WORKSPACE_ROOT, LLM_CORE_DIR

SCRIPT_DIR = WORKSPACE_ROOT
CORPUS_DIR = SCRIPT_DIR / "corpus"
INPUT_CSV = SCRIPT_DIR / "candidate_sources.csv"
MANUAL_CSV = SCRIPT_DIR / "manual_url_supplement.csv"

WEAK_LABELS_DEFAULT = frozenset(
    {
        "fetch_or_error_placeholder",
        "http_403_signal",
        "likely_paywall_or_denied",
        "empty_or_tiny_body",
    }
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _load_build_corpus():
    path = LLM_CORE_DIR / "Build-Corpus.py"
    spec = importlib.util.spec_from_file_location("build_corpus", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_classify():
    path = LLM_CORE_DIR / "corpus_qc_refresh.py"
    spec = importlib.util.spec_from_file_location("cqr", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify_file


def _merge_candidate_rows():
    paths = [INPUT_CSV]
    if MANUAL_CSV.exists():
        paths.append(MANUAL_CSV)
    by_key: dict[tuple[str, str], dict] = {}
    for p in paths:
        if not p.exists():
            continue
        import csv

        with p.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not row.get("url"):
                    continue
                by_key[(row.get("port_id", ""), str(row.get("candidate_rank", "")))] = row
    return by_key


def _build_session() -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def _headers_pack_a(url: str) -> dict[str, str]:
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}" if p.netloc else ""
    ua = random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": origin + "/" if origin else "https://www.google.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


def _headers_pack_b(url: str) -> dict[str, str]:
    ua = random.choice(USER_AGENTS)
    ref = f"https://www.google.com/search?q={quote(url[:200])}"
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": ref,
        "Upgrade-Insecure-Requests": "1",
    }


def _headers_pack_c_minimal(url: str) -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(60.0, (2**attempt) * random.uniform(0.6, 1.4)))


def _maybe_cloudscraper_get(url: str, timeout: int):
    try:
        import cloudscraper  # type: ignore
    except ImportError:
        return None
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    return scraper.get(url, timeout=timeout, allow_redirects=True)


def remedial_get(
    session: requests.Session,
    url: str,
    timeout: int,
    use_cloudscraper: bool,
) -> requests.Response:
    last_exc: Exception | None = None
    header_builders = [_headers_pack_a, _headers_pack_b, _headers_pack_c_minimal]

    for attempt in range(3):
        for hb in header_builders:
            headers = hb(url)
            try:
                resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                if resp.status_code in (429, 503):
                    ra = resp.headers.get("Retry-After")
                    if ra and re.match(r"^\d+$", str(ra).strip()):
                        time.sleep(min(120, int(str(ra).strip())))
                    else:
                        _sleep_backoff(attempt)
                    break
                if resp.status_code in (401, 403):
                    last_exc = requests.exceptions.HTTPError(
                        f"{resp.status_code} Client Error for {url}"
                    )
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last_exc = e
                continue

        if use_cloudscraper:
            cs_resp = _maybe_cloudscraper_get(url, timeout=timeout)
            if cs_resp is not None and cs_resp.status_code < 400:
                return cs_resp

        _sleep_backoff(attempt)

    # Last resort: some government sites ship incomplete chains (local verify fails).
    try:
        resp = session.get(
            url,
            headers=_headers_pack_a(url),
            timeout=timeout,
            allow_redirects=True,
            verify=False,
        )
        if resp.status_code < 400:
            return resp
    except requests.RequestException:
        pass

    if last_exc is not None:
        raise last_exc
    raise requests.RequestException("Remedial GET failed with no exception detail")


def remedial_download_and_extract(
    row: dict,
    build_mod,
    session: requests.Session,
    *,
    timeout: int,
    use_cloudscraper: bool,
) -> tuple[str, str, str]:
    port_id = row["port_id"]
    rank = row["candidate_rank"]
    url = row["url"]
    if not url or not rank or rank == "0":
        return port_id, rank, "SKIPPED_NO_URL"

    filename = f"{port_id}_{rank}.txt"
    filepath = CORPUS_DIR / filename

    try:
        resp = remedial_get(session, url, timeout=timeout, use_cloudscraper=use_cloudscraper)
        content_type = (resp.headers.get("Content-Type") or "").lower()

        text_content = (
            f"--- SOURCE INFO ---\nURL: {url}\nTITLE: {row.get('source_title', '')}\n"
            f"NOTE: {row.get('candidate_note', '')}\n"
            f"REMEDIAL_FETCH: true\n-------------------\n\n"
        )

        if "application/pdf" in content_type or url.lower().rstrip("/").endswith(".pdf"):
            pdf_bytes = resp.content
            extracted = build_mod.extract_pdf_text(pdf_bytes)
            text_content += "[DOCUMENT TYPE: PDF]\n\n" + extracted
        else:
            cl = int(resp.headers.get("Content-Length") or 0)
            if cl > 10 * 1024 * 1024:
                raise ValueError("File too large (>10MB)")
            raw = resp.content
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError("Body too large (>10MB)")
            enc = getattr(resp, "apparent_encoding", None) or resp.encoding or "utf-8"
            html = raw.decode(enc, errors="replace")
            extracted = build_mod.extract_html_text(html)
            text_content += "[DOCUMENT TYPE: HTML]\n\n" + extracted

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text_content)
        return port_id, rank, "SUCCESS_REMEDIAL"

    except requests.exceptions.RequestException as e:
        err_msg = (
            f"--- SOURCE INFO ---\nURL: {url}\nTITLE: {row.get('source_title', '')}\n"
            f"NOTE: {row.get('candidate_note', '')}\nREMEDIAL_FETCH: true\n-------------------\n\n"
            f"[FETCH ERROR]: {e}\n"
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(err_msg)
        return port_id, rank, "FETCH_ERROR"
    except Exception as e:
        err_msg = (
            f"--- SOURCE INFO ---\nURL: {url}\nTITLE: {row.get('source_title', '')}\n"
            f"NOTE: {row.get('candidate_note', '')}\nREMEDIAL_FETCH: true\n-------------------\n\n"
            f"[UNKNOWN ERROR]: {e}\n"
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(err_msg)
        return port_id, rank, "UNKNOWN_ERROR"


def scan_weak_basenames(classify_file, min_body_bytes: int, allowed_labels: frozenset) -> list[tuple[str, str, int]]:
    """Returns sorted list of (basename, label, size)."""
    out: list[tuple[str, str, int]] = []
    for fp in sorted(CORPUS_DIR.glob("*.txt")):
        label, size = classify_file(fp, min_body_bytes)
        if label in allowed_labels:
            out.append((fp.name, label, size))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Remedial corpus fetch for weak QC rows.")
    ap.add_argument("--min-body-bytes", type=int, default=400)
    ap.add_argument(
        "--labels",
        nargs="*",
        default=sorted(WEAK_LABELS_DEFAULT),
        help="QC labels to remediate (default: all weak kinds)",
    )
    ap.add_argument("--max-workers", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=28)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cloudscraper", action="store_true")
    ap.add_argument(
        "--only-basenames-file",
        type=str,
        default=None,
        help="If set, only remediate corpus basenames listed in this file (one *.txt name per line).",
    )
    args = ap.parse_args()

    classify_file = _load_classify()
    build_mod = _load_build_corpus()
    allowed = frozenset(args.labels)

    weak = scan_weak_basenames(classify_file, args.min_body_bytes, allowed)
    by_file = _merge_candidate_rows()

    if args.only_basenames_file:
        path = Path(args.only_basenames_file)
        if not path.is_absolute():
            path = SCRIPT_DIR / path
        allow_bn = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
        weak = [(bn, lab, sz) for bn, lab, sz in weak if bn in allow_bn]
        print(f"After --only-basenames-file ({path.name}): {len(weak)} basenames to remediate")

    print(f"Weak corpus files matching {sorted(allowed)}: {len(weak)}")
    if not weak:
        return

    counts: dict[str, int] = {}
    for _bn, lab, _sz in weak:
        counts[lab] = counts.get(lab, 0) + 1
    for k in sorted(counts.keys()):
        print(f"  {k}: {counts[k]}")

    missing_row: list[str] = []
    jobs: list[dict] = []
    for basename, _lab, _sz in weak:
        stem = basename[:-4] if basename.lower().endswith(".txt") else basename
        port_id, rank = stem.rsplit("_", 1)
        row = by_file.get((port_id, str(rank)))
        if not row:
            missing_row.append(basename)
            continue
        jobs.append(row)

    if missing_row:
        print(f"[warn] No candidate_sources row for {len(missing_row)} files (showing up to 25):")
        for n in missing_row[:25]:
            print(f"    {n}")

    if args.dry_run:
        print(f"[dry-run] Would refetch {len(jobs)} URLs (parallel workers={args.max_workers}).")
        return

    use_cs = not args.no_cloudscraper

    def _one(row: dict) -> tuple[str, str, str]:
        sess = _build_session()
        return remedial_download_and_extract(
            row, build_mod, sess, timeout=args.timeout, use_cloudscraper=use_cs
        )

    ok = err = 0
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as pool:
        futs = {pool.submit(_one, r): r for r in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                _p, _r, st = fut.result()
                if st == "SUCCESS_REMEDIAL":
                    ok += 1
                else:
                    err += 1
            except Exception as exc:
                err += 1
                print(f"[worker] {exc}")
            if i % 50 == 0 or i == len(jobs):
                print(f"Progress: {i}/{len(jobs)} (SUCCESS_REMEDIAL: {ok}, other: {err})")

    print(f"Remedial fetch done. SUCCESS_REMEDIAL={ok}, errors/other={err}")


if __name__ == "__main__":
    main()
