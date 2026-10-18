import csv
import re
import time
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MASTER_CSV = 'Ski resorts - Master.csv'
OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
AUDIT_CSV = 'lift_pass_audit_report.csv'
TIMEOUT_SECONDS = 10
SEASON_INFLATION_RATE = 0.035  # Estimated 3.5% average increase for 2026/27 season

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,de;q=0.8,fr;q=0.7'
}

def clean_price(price_str):
    if not price_str: return 0.0
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)', str(price_str))
    return float(match.group(1)) if match else 0.0

def extract_price_from_text(text):
    """Extracts currency values within valid European day pass ranges (€30 - €140)."""
    patterns = [
        r'(?:€|EUR|CHF)\s*([0-9]{2,3}(?:\.[0-9]{2})?)',
        r'([0-9]{2,3}(?:\.[0-9]{2})?)\s*(?:€|EUR|CHF)'
    ]
    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            val = float(match.group(1))
            if 30 <= val <= 140:
                symbol = 'CHF' if 'CHF' in text else '€'
                return f"{symbol}{val:.2f}"
    return None

def fetch_via_ddg_snippet(resort_name):
    """Fallback Tier: Search DuckDuckGo text snippets for structured pricing data."""
    try:
        query = f"{resort_name} adult 1 day ski pass price 2026 2027"
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            snippets = soup.find_all('a', class_='result__snippet')
            for snippet in snippets:
                price = extract_price_from_text(snippet.get_text())
                if price:
                    return price, "Tier 2 (Search Snippet)"
    except Exception:
        pass
    return None, None

def scrape_resort_price(resort_name, website_url):
    """Main extraction handler."""
    if not website_url or website_url.strip().lower() in ['n/a', 'closed', '']:
        return None, "No URL"

    target_url = website_url.strip()
    if not target_url.startswith('http://') and not target_url.startswith('https://'):
        target_url = 'https://' + target_url

    # Attempt 1: Standard Direct HTML Extraction
    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=TIMEOUT_SECONDS, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            price = extract_price_from_text(soup.get_text())
            if price:
                return price, "Tier 1 (Direct HTML)"
    except Exception:
        pass

    # Attempt 2: Search Engine Snippet Parsing
    price, method = fetch_via_ddg_snippet(resort_name)
    if price:
        return price, method

    return None, "No Price Found"

def main():
    print(f"Loading master dataset from '{MASTER_CSV}'...")
    
    updated_rows = []
    audit_rows = []
    
    try:
        with open(MASTER_CSV, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            
            try:
                name_idx = header.index('Resort Name')
                website_idx = header.index('Website')
                pass_idx = header.index('Day Pass (24/25 Est)')
            except ValueError:
                # Fallback to column index search if exact header varies
                name_idx, website_idx, pass_idx = 1, 9, 5

            updated_header = list(header)
            updated_header[pass_idx] = 'Day Pass (2026/27 Est)'
            updated_rows.append(updated_header)
            audit_rows.append(['Resort Name', 'Website', 'Previous Price', 'New Price', 'Extraction Method', 'Review Status'])

            processed = 0
            scraped_success = 0
            indexed_est = 0

            for row in reader:
                if len(row) <= max(name_idx, website_idx, pass_idx):
                    updated_rows.append(row)
                    continue

                resort_name = row[name_idx]
                website = row[website_idx]
                prev_price_str = row[pass_idx]

                processed += 1
                new_price_str, method = scrape_resort_price(resort_name, website)
                
                status = "OK"
                new_row = list(row)

                if new_price_str:
                    scraped_success += 1
                    new_row[pass_idx] = new_price_str
                else:
                    # Apply +3.5% baseline inflation index if web scraping yields no result
                    prev_val = clean_price(prev_price_str)
                    if prev_val > 0:
                        est_val = prev_val * (1 + SEASON_INFLATION_RATE)
                        symbol = 'CHF' if 'CHF' in prev_price_str else '€'
                        new_price_str = f"{symbol}{est_val:.2f}"
                        method = "Tier 3 (Index +3.5%)"
                        status = "ESTIMATED"
                        indexed_est += 1
                    else:
                        new_price_str = prev_price_str
                        method = "N/A"
                        status = "MISSING"

                new_row[pass_idx] = new_price_str
                updated_rows.append(new_row)
                audit_rows.append([resort_name, website, prev_price_str, new_price_str, method, status])

                if processed % 20 == 0:
                    print(f"Processed {processed} resorts... (Scraped: {scraped_success}, Estimated: {indexed_est})")

    except FileNotFoundError:
        print(f"Error: {MASTER_CSV} not found.")
        return

    # Write output files
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(updated_rows)

    with open(AUDIT_CSV, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(audit_rows)

    print("\n" + "="*50)
    print(" LIFT PASS UPDATE PIPELINE COMPLETE")
    print("="*50)
    print(f"Total Resorts Processed : {processed}")
    print(f"Live Scraped Prices     : {scraped_success}")
    print(f"Indexed (+3.5%) Prices  : {indexed_est}")
    print(f"Output saved to         : '{OUTPUT_CSV}'")

if __name__ == '__main__':
    main()
