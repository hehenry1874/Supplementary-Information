import csv
import json
import time
import re
import urllib.parse
import unicodedata
from pathlib import Path
import requests
import random

INPUT_CSV = Path("../double_batch_ports_master.csv")
OUTPUT_CSV = Path("candidate_sources.csv")

HEADERS = [
    "port_id", "port_name_standard", "country", "candidate_rank",
    "source_title", "source_organization", "source_country", "url", "language",
    "source_class", "source_tier_preliminary", "candidate_strength",
    "title_port_match", "snippet_port_match", "url_port_match",
    "thematic_keyword_match", "likely_dimension", "candidate_note",
    "low_coverage_flag", "low_coverage_reason"
]

EXCLUDE_DOMAINS = [
    'wikipedia.org', 'wikimedia.org', 'imo.org', 'unctad.org', 'sustainableworldports.org',
    'facebook.com', 'twitter.com', 'x.com', 'instagram.com', 'youtube.com', 'linkedin.com',
    'cruisemapper.com', 'vesseltracker.com', 'marinetraffic.com', 'tripadvisor.com',
    'researchgate.net', 'sciencedirect.com', 'booking.com', 'pinterest.com',
    'vesselfinder.com', 'seaports.com', 'searates.com', 'logistics-manager.com',
    'allaboutshipping.co.uk', 'cruiseease.com', 'e-tracking.net', 'cogoport.id', 'cogoport.com'
]

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
]

def search_yahoo(query, max_results=5):
    import requests, bs4
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    url = "https://search.yahoo.com/search"
    params = {'p': query}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        soup = bs4.BeautifulSoup(resp.text, 'html.parser')
        results = []
        for item in soup.select('div.algo-sr'):
            a = item.find('a', class_='d-ib') or item.find('h3').find('a') if item.find('h3') else None
            if not a or not a.get('href'):
                continue
            href = a['href']
            
            # Decode Yahoo tracking link to get actual URL
            if 'r.search.yahoo.com' in href and 'RU=' in href:
                try:
                    ru_part = href.split('RU=')[1].split('/R')[0]
                    href = urllib.parse.unquote(ru_part)
                except:
                    pass
                    
            title = a.get_text()
            compText = item.find('div', class_='compText')
            snippet = compText.get_text() if compText else ""
            results.append({"href": href, "title": title, "body": snippet})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print(f"Yahoo error: {e}")
        return []

def is_excluded(url):
    lower_url = url.lower()
    for d in EXCLUDE_DOMAINS:
        if d in lower_url:
            return True
    if '/search' in lower_url or '/tag/' in lower_url or '/category/' in lower_url:
        return True
    return False

def get_queries(port, country):
    base = f'"{port}"'
    local_port = "port"
    if country in ['Brazil', 'Portugal', 'Angola', 'Mozambique']: local_port = "porto"
    elif country in ['Chile', 'Argentina', 'Spain', 'Mexico', 'Peru', 'Colombia', 'Guatemala']: local_port = "puerto"
    elif country in ['France', 'Senegal', 'Cameroon', 'Benin', 'Ivory Coast', 'Algeria']: local_port = "port"
    elif country in ['Italy']: local_port = "porto"
    elif country in ['China']: local_port = "港口"
    elif country in ['Japan']: local_port = "港"
    
    q1 = f'{base} {local_port} (electrification OR "shore power" OR "cold ironing" OR OPS OR 岸电 OR 岸電)'
    q2 = f'{base} {local_port} ("electric crane" OR "electric truck" OR e-rtg OR "equipamento elétrico" OR "grua eléctrica")'
    q3 = f'{base} {local_port} ("green energy" OR renewable OR solar OR wind OR PPA OR "energia renovável" OR "energia renovable")'
    q4 = f'{base} {local_port} ("alternative fuel" OR methanol OR hydrogen OR ammonia OR "hidrogênio" OR "hidrógeno")'
    q5 = f'{base} {local_port} (sustainability OR "ESG" OR "climate action" OR decarbonization OR sustentabilidade OR sostenibilidad OR 脱碳)'
    q6 = f'{base} {local_port} authority (CAPEX OR investment OR tender OR procurement OR licitação OR licitación OR 投资)'
    
    return [
        (q1, "electrification"),
        (q2, "electrification"),
        (q3, "green_energy"),
        (q4, "green_energy"),
        (q5, "governance_investment"),
        (q6, "governance_investment")
    ]

def remove_accents(s):
    if not isinstance(s, str):
        return ""
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def evaluate_candidate(port, title, snippet, url, dimension):
    url_lower = url.lower()
    title_lower = remove_accents(title.lower())
    snippet_lower = remove_accents(snippet.lower())
    port_lower = remove_accents(port.lower())

    title_match = port_lower in title_lower or 'port' in title_lower or 'porto' in title_lower or 'puerto' in title_lower or '港' in title_lower
    
    low_carbon_kws = [
        'carbon', 'green', 'sustainab', 'electri', 'shore power', 'ops', 'cold ironing', 
        'renewable', 'solar', 'wind', 'methanol', 'hydrogen', 'ammonia', 'esg', 
        'climate', 'decarbon', 'sustenta', 'sostenib', 'hidrogen', '岸电', '脱碳', '岸電',
        'emission', 'net zero', 'energy transition'
    ]
    has_lc = any(kw in snippet_lower or kw in title_lower for kw in low_carbon_kws)
    snippet_match = port_lower in snippet_lower and has_lc

    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip('/')
    url_match = len(path) > 5 and any(k in path for k in ['project', 'news', 'report', 'sustainab', 'invest', 'article', 'press', 'media', 'document', 'release', 'blog'])

    host = parsed.netloc.lower()
    domain_match = False
    good_host_words = [
        '.gov', '.gob.', 'port', 'porto', 'puerto', 'authority', 'terminal', 'maritime',
        'shipping', 'logistics', 'news', 'press', 'media', 'journal', 'post', 'times',
        'bank', 'ebrd', 'iadb', 'worldbank', 'finance', 'agency', 'group', 'infra'
    ]
    if any(w in host for w in good_host_words):
        domain_match = True

    score = sum([title_match, snippet_match, url_match, domain_match])
    keep = score >= 2

    if '.gov' in host or '.gob.' in host or 'ministry' in host or 'dept' in host:
        s_class = "government_page"
        s_tier = "Tier2"
    elif 'port' in host or 'porto' in host or 'puerto' in host or 'terminal' in host or 'authority' in host:
        s_class = "official_port_operator_page"
        s_tier = "Tier1"
    elif 'news' in host or 'media' in host or 'journal' in host or 'shipping' in host or 'press' in host:
        s_class = "trade_press_or_media"
        s_tier = "Tier2"
    else:
        s_class = "general_web_page"
        s_tier = "Tier3"

    strength = "high" if score >= 3 else ("medium" if score == 2 else "low")

    return {
        "keep": keep,
        "score": score,
        "title_port_match": "Yes" if title_match else "No",
        "snippet_port_match": "Yes" if snippet_match else "No",
        "url_port_match": "Yes" if url_match else "No",
        "thematic_keyword_match": "Yes" if has_lc else "No",
        "likely_dimension": dimension,
        "source_class": s_class,
        "source_tier_preliminary": s_tier,
        "candidate_strength": strength,
        "source_organization": host
    }


def main():
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    completed_ports = set()
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                completed_ports.add(row["port_id"])
    else:
        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        ports = list(csv.DictReader(f))

    for p in ports:
        pid = p["port_id"]
        if pid in completed_ports:
            continue
            
        pname = p["port_name_standard"]
        country = p["country"]
        print(f"Harvesting for {pname} ({country})...")
        
        candidates = []
        seen_urls = set()
        queries = get_queries(pname, country)
        
        for q, dim in queries:
            if len(candidates) >= 6:
                break
            
            results = search_yahoo(q, max_results=5)
            time.sleep(2)
            if not results:
                continue
            
            for r in results:
                url = r.get("href")
                if not url or is_excluded(url) or url in seen_urls:
                    continue
                    
                title = r.get("title", "")
                snippet = r.get("body", "")
                
                eval_res = evaluate_candidate(pname, title, snippet, url, dim)
                if eval_res["keep"]:
                    seen_urls.add(url)
                    candidates.append({
                        "port_id": pid,
                        "port_name_standard": pname,
                        "country": country,
                        "source_title": title,
                        "url": url,
                        "snippet": snippet,
                        **eval_res
                    })
                    if len(candidates) >= 8:
                        break
                
        candidates = candidates[:8]
        low_cov = "TRUE" if len(candidates) < 2 else "FALSE"
        low_cov_reason = f"Found only {len(candidates)} usable candidates across 6 queries." if low_cov == "TRUE" else ""
        
        with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            if not candidates:
                writer.writerow({
                    "port_id": pid,
                    "port_name_standard": pname,
                    "country": country,
                    "candidate_rank": 0,
                    "low_coverage_flag": "TRUE",
                    "low_coverage_reason": "Zero usable candidates found."
                })
            else:
                for idx, c in enumerate(candidates):
                    # Format output correctly
                    row = {
                        "port_id": pid,
                        "port_name_standard": pname,
                        "country": country,
                        "candidate_rank": idx + 1,
                        "source_title": c["source_title"].replace('\n', ' ').strip(),
                        "source_organization": c["source_organization"],
                        "source_country": country, 
                        "url": c["url"],
                        "language": "unknown", 
                        "source_class": c["source_class"],
                        "source_tier_preliminary": c["source_tier_preliminary"],
                        "candidate_strength": c["candidate_strength"],
                        "title_port_match": c["title_port_match"],
                        "snippet_port_match": c["snippet_port_match"],
                        "url_port_match": c["url_port_match"],
                        "thematic_keyword_match": c["thematic_keyword_match"],
                        "likely_dimension": c["likely_dimension"],
                        "candidate_note": (c["snippet"][:150] + "...").replace('\n', ' '), 
                        "low_coverage_flag": low_cov,
                        "low_coverage_reason": low_cov_reason
                    }
                    writer.writerow(row)
        completed_ports.add(pid)
        print(f"  -> Saved {len(candidates)} candidates.")
        time.sleep(1)

if __name__ == "__main__":
    main()
