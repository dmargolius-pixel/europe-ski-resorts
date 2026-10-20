import csv
import re
import time
import argparse
import urllib3
import requests
from bs4 import BeautifulSoup

# Disable SSL warnings for legacy resort websites with self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration Constants ---
DEFAULT_MASTER_CSV = 'Ski resorts - Master.csv'
DEFAULT_OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
DEFAULT_AUDIT_CSV = 'lift_pass_batch_audit.csv'
TIMEOUT_SECONDS = 12

# Bounds Guardrails relative to previous baseline price (Price_new in [0.90 * prev, 1.25 * prev])
MIN_RATIO = 0.90
MAX_RATIO = 1.25
SEASON_INFLATION_RATE = 0.035  # Fallback +3.5% baseline inflation index

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,de;q=0.8,fr;q=0.7,it;q=0.6,es;q=0.5',
    'Cache-Control': 'no-cache'
}

# Multilingual terms for 1-day adult lift passes
MULTILINGUAL_REGEX_PATTERNS = [
    # German: Tageskarte, Erwachsene, Skipass
    r'(?:tageskarte|erwachsene|1\s*tag|skipass).*?(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?)',
    r'(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?).*?(?:tageskarte|erwachsene|1\s*tag)',

    # French: Forfait journée, Adulte, Pass 1 jour
    r'(?:forfait|journée|adulte|1\s*jour).*?(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?)',
    r'(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?).*?(?:forfait|journée|adulte|1\s*jour)',

    # Italian: Giornaliero, Adulto, Skipass
    r'(?:giornaliero|adulto|1\s*giorno).*?(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?)',
    r'(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?).*?(?:giornaliero|adulto|1\s*giorno)',

    # Spanish/Catalan: Forfait día, Adulto, Adult
    r'(?:forfait|día|dia|adulto|adult).*?(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?)',
    r'(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?).*?(?:forfait|día|dia|adulto|adult)',

    # General Currency Fallback (30 to 140 range)
    r'(?:€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{2})?)',
    r'([0-9]{2,3}(?:[\.,][0-9]{2})?)\s*(?:€|EUR|CHF)'
]

SUBPAGE_PATHS = [
    '',           # Homepage
    '/tarifs',    # French/German
    '/prices',    # English
    '/tickets',   # General
    '/skitickets',# German/English
    '/skipass',   # International
    '/prezzi',    # Italian
    '/tarifas'    # Spanish
]

def clean_numeric_price(price_str):
    """Converts price strings like '€ 68.50' or '68,50 CHF' to standard float values."""
    if not price_str:
        return 0.0
    # Normalize German/French commas to decimal points
    normalized = str(price_str).replace(',', '.')
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)', normalized)
    return float(match.group(1)) if match else 0.0

def validate_price_bounds(new_price_val, prev_price_val):
    """
    Checks if newly extracted price falls within the sanity threshold:
    Price_new in [0.90 * prev, 1.25 * prev].
    """
    if prev_price_val <= 0:
        # If no baseline exists, check general European day pass limits (€30 - €140)
        return 30.0 <= new_price_val <= 140.0
    
    lower_bound = prev_price_val * MIN_RATIO
    upper_bound = prev_price_val * MAX_RATIO
    return lower_bound <= new_price_val <= upper_bound

def extract_price_from_html(html_content, prev_price_val=0.0):
    """
    Parses raw HTML using multilingual regex patterns and selects candidates
    passing bounds checks.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    text = soup.get_text(separator=' ')

    for pattern in MULTILINGUAL_REGEX_PATTERNS:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            raw_val = match.group(1).replace(',', '.')
            try:
                val = float(raw_val)
                if validate_price_bounds(val, prev_price_val):
                    symbol = 'CHF' if 'CHF' in text[max(0, match.start()-15):match.end()+15] else '€'
                    return f"{symbol}{val:.2f}", val
            except ValueError:
                continue

    return None, 0.0

def scrape_resort_subpages(base_url, prev_price_val=0.0):
    """
    Traverses standard lift pass subpages (/tarifs, /prices, /tickets) to locate valid prices.
    """
    if not base_url or base_url.strip().lower() in ['n/a', 'closed', '']:
        return None, "NO_URL", "No URL provided"

    clean_base = base_url.strip().rstrip('/')
    if not clean_base.startswith('http://') and not clean_base.startswith('https://'):
        clean_base = 'https://' + clean_base

    for subpath in SUBPAGE_PATHS:
        target_url = clean_base + subpath
        try:
            resp = requests.get(target_url, headers=HEADERS, timeout=TIMEOUT_SECONDS, verify=False)
            if resp.status_code == 200:
                price_str, price_val = extract_price_from_html(resp.text, prev_price_val)
                if price_str:
                    method = f"Direct DOM ({subpath if subpath else 'homepage'})"
                    return price_str, "OK", method
        except Exception:
            continue

    return None, "FAILED", "Extraction Failed / Bounds Exceeded"

def process_batch(master_csv, output_csv, audit_csv, offset, limit):
    """
    Executes batch processing for a specified slice of resorts from master CSV.
    """
    print(f"\n==================================================")
    print(f" STARTING BATCH LIFT PASS EXTRACTION")
    print(f" Processing Offset: {offset} | Batch Limit: {limit}")
    print(f"==================================================\n")

    try:
        with open(master_csv, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except FileNotFoundError:
        print(f"[x] Error: Master CSV file '{master_csv}' not found.")
        return

    try:
        name_idx = header.index('Resort Name')
        website_idx = header.index('Website')
        pass_idx = header.index('Day Pass (24/25 Est)')
    except ValueError:
        name_idx, website_idx, pass_idx = 1, 9, 5

    # Target slice for batch execution
    batch_rows = rows[offset:offset + limit] if limit > 0 else rows[offset:]
    
    updated_rows = [header]
    audit_rows = [['Resort Name', 'Website', 'Previous Price', 'New Price', 'Extraction Method', 'Review Status']]

    scraped_ok = 0
    flagged_review = 0
    estimated_count = 0

    for idx, row in enumerate(batch_rows, start=offset + 1):
        if len(row) <= max(name_idx, website_idx, pass_idx):
            continue

        resort_name = row[name_idx]
        website = row[website_idx]
        prev_price_str = row[pass_idx]
        prev_price_val = clean_numeric_price(prev_price_str)

        print(f"[{idx}] Auditing: {resort_name} ({website})...")

        new_price_str, status, method = scrape_resort_subpages(website, prev_price_val)
        new_row = list(row)

        if status == "OK":
            scraped_ok += 1
            print(f"  [+] SUCCESS: Extracted {new_price_str} via {method}")
        else:
            # Fallback to Index Estimate (+3.5%) if web scraping fails or exceeds bounds
            if prev_price_val > 0:
                est_val = prev_price_val * (1 + SEASON_INFLATION_RATE)
                symbol = 'CHF' if 'CHF' in prev_price_str else '€'
                new_price_str = f"{symbol}{est_val:.2f}"
                method = "Tier 3 (Index +3.5%)"
                status = "ESTIMATED"
                estimated_count += 1
                print(f"  [~] ESTIMATED: Indexed to {new_price_str} (+3.5%)")
            else:
                new_price_str = prev_price_str
                status = "REVIEW_REQUIRED"
                flagged_review += 1
                print(f"  [!] REVIEW REQUIRED: Flagged for human audit")

        new_row[pass_idx] = new_price_str
        updated_rows.append(new_row)
        audit_rows.append([resort_name, website, prev_price_str, new_price_str, method, status])

        time.sleep(1) # Courteous crawl delay

    # Save outputs
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(updated_rows)

    with open(audit_csv, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(audit_rows)

    print("\n" + "="*50)
    print(" BATCH EXECUTION SUMMARY")
    print("="*50)
    print(f"Resorts Processed in Batch : {len(batch_rows)}")
    print(f"Valid Scraped Prices (OK)  : {scraped_ok}")
    print(f"Indexed (+3.5%) Fallbacks  : {estimated_count}")
    print(f"Flagged for Review         : {flagged_review}")
    print(f"Batch Audit Log Saved To   : '{audit_csv}'\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Batch Lift Pass Scraper with Bounds Guardrails")
    parser.add_argument('--offset', type=int, default=0, help="Starting row index (default: 0)")
    parser.add_argument('--limit', type=int, default=15, help="Number of resorts to process in batch (default: 15)")
    parser.add_argument('--input', type=str, default=DEFAULT_MASTER_CSV, help="Master CSV filepath")
    parser.add_argument('--output', type=str, default=DEFAULT_OUTPUT_CSV, help="Output CSV filepath")
    parser.add_argument('--audit', type=str, default=DEFAULT_AUDIT_CSV, help="Audit log CSV filepath")

    args = parser.parse_args()
    process_batch(args.input, args.output, args.audit, args.offset, args.limit)
