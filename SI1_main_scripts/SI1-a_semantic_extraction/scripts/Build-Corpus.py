import csv
import json
import urllib.parse
import os
import requests
import io
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import random
import sys

from llm_core_paths import WORKSPACE_ROOT as SCRIPT_DIR
INPUT_CSV = SCRIPT_DIR / "candidate_sources.csv"
CORPUS_DIR = SCRIPT_DIR / "corpus"

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

# Setup corpus directory
CORPUS_DIR.mkdir(parents=True, exist_ok=True)

def extract_pdf_text(pdf_bytes):
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text("text") + "\n\n"
        return text.strip()
    except Exception as e:
        return f"[PDF EXTRACTION ERROR]: {e}"

def extract_html_text(html_text):
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        
        # Remove script, style, and navigation/footer elements
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()
            
        # Get text
        text = soup.get_text(separator='\n')
        
        # Break into lines and remove leading and trailing space on each
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        return text
    except Exception as e:
        return f"[HTML EXTRACTION ERROR]: {e}"

def download_and_extract(row, force=False):
    port_id = row['port_id']
    rank = row['candidate_rank']
    url = row['url']
    
    # If no URL or rank, skip
    if not url or not rank or rank == '0':
        return port_id, rank, "SKIPPED_NO_URL"
        
    filename = f"{port_id}_{rank}.txt"
    filepath = CORPUS_DIR / filename
    
    # Skip if already exists and has content
    if not force and filepath.exists() and filepath.stat().st_size > 100:
        return port_id, rank, "ALREADY_EXISTS"

    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8'
    }

    try:
        # Increase timeout slightly to handle slow port authority sites
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True, stream=True)
        resp.raise_for_status()
        
        content_type = resp.headers.get('Content-Type', '').lower()
        
        text_content = f"--- SOURCE INFO ---\nURL: {url}\nTITLE: {row.get('source_title', '')}\nNOTE: {row.get('candidate_note', '')}\n-------------------\n\n"
        
        if 'application/pdf' in content_type or url.lower().endswith('.pdf'):
            # It's a PDF
            pdf_bytes = resp.content
            extracted = extract_pdf_text(pdf_bytes)
            text_content += "[DOCUMENT TYPE: PDF]\n\n" + extracted
        else:
            # Assume HTML/Text
            # Avoid downloading huge binaries if mislabeled
            if int(resp.headers.get('Content-Length', 0)) > 10 * 1024 * 1024:
                raise ValueError("File too large (>10MB)")
                
            extracted = extract_html_text(resp.text)
            text_content += "[DOCUMENT TYPE: HTML]\n\n" + extracted
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text_content)
            
        return port_id, rank, "SUCCESS"
        
    except requests.exceptions.RequestException as e:
        err_msg = f"--- SOURCE INFO ---\nURL: {url}\nTITLE: {row.get('source_title', '')}\nNOTE: {row.get('candidate_note', '')}\n-------------------\n\n[FETCH ERROR]: {e}\n"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(err_msg)
        return port_id, rank, "FETCH_ERROR"
    except Exception as e:
        err_msg = f"--- SOURCE INFO ---\nURL: {url}\nTITLE: {row.get('source_title', '')}\nNOTE: {row.get('candidate_note', '')}\n-------------------\n\n[UNKNOWN ERROR]: {e}\n"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(err_msg)
        return port_id, rank, "UNKNOWN_ERROR"

def _merge_candidate_rows(extra_paths):
    """Later rows override earlier for the same (port_id, candidate_rank)."""
    by_key = {}
    for path in extra_paths:
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get('url'):
                    by_key[(row.get('port_id'), str(row.get('candidate_rank')))] = row
    return list(by_key.values())

def main():
    argv = sys.argv[1:]
    force_all = "--force" in argv
    with_manual = "--with-manual-urls" in argv

    paths = [INPUT_CSV]
    manual = SCRIPT_DIR / "manual_url_supplement.csv"
    if with_manual:
        paths.append(manual)
    rows = _merge_candidate_rows(paths)
                
    total = len(rows)
    print(f"Starting extraction for {total} candidate URLs...")

    def _job(row):
        return download_and_extract(row, force=force_all)
    
    # Use 15 workers to process the queue quickly but responsibly
    success_count = 0
    error_count = 0
    skip_count = 0
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(_job, r): r for r in rows}
        
        for i, future in enumerate(as_completed(futures), 1):
            try:
                pid, rank, status = future.result()
                if status == "SUCCESS":
                    success_count += 1
                elif status == "ALREADY_EXISTS" or status == "SKIPPED_NO_URL":
                    skip_count += 1
                else:
                    error_count += 1
                    
                if i % 100 == 0 or i == total:
                    print(f"Progress: {i}/{total} (Success: {success_count}, Errors: {error_count}, Skipped: {skip_count})")
            except Exception as exc:
                print(f"Worker generated an exception: {exc}")

    print("Corpus building completed!")

if __name__ == "__main__":
    main()
