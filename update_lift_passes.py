import csv
import re
import requests
import json
from bs4 import BeautifulSoup
import urllib3

# Suppress insecure request warnings for poorly configured resort websites
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuration constants
MASTER_CSV = 'Ski resorts - Master.csv'
OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
AUDIT_CSV = 'lift_pass_audit_report.csv'
TIMEOUT_SECONDS = 12

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8,de;q=0.7'
}

def clean_price(price_str):
    """Helper to extract just the float value from a price string like '€69.00'."""
    if not price_str: return 0.0
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)', str(price_str))
    return float(match.group(1)) if match else 0.0

def extract_tier1_json_ld(soup):
    try:
        for script in soup.find_all('script', type='application/ld+json'):
            if script.string:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get('@type') in ['Product', 'Offer']:
                        offers = item.get('offers', {})
                        if isinstance(offers, dict) and 'price' in offers:
                            val = float(offers['price'])
                            if 30 <= val <= 130:
                                currency = offers.get('priceCurrency', '€').replace('EUR', '€')
                                return f"{currency}{val:.2f}"
    except Exception:
        pass
    return None

def extract_tier2_css_classes(soup):
    target_classes = ['price', 'tarif', 'ticket-price', 'skipass-price', 'valeur']
    for class_name in target_classes:
        elements = soup.find_all(class_=re.compile(class_name, re.I))
        for el in elements:
            text = el.get_text(separator=' ', strip=True)
            match = re.search(r'(?:€|CHF)\s*([0-9]{2}(?:\.[0-9]{2})?)', text)
            if match:
                val = float(match.group(1))
                if 30 <= val <= 130:
                    symbol = 'CHF' if 'CHF' in text else '€'
                    return f"{symbol}{val:.2f}"
    return None

def extract_tier3_contextual(soup):
    keywords = ['1 day', '1 jour', 'tageskarte', 'adult', 'adulte', 'erwachsene']
    for keyword in keywords:
        elements = soup.find_all(string=re.compile(keyword, re.IGNORECASE))
        for el in elements:
            parent = el.parent
            if parent:
                text = parent.get_text(strip=True)
                match = re.search(r'(?:€|CHF)\s*([0-9]{2}(?:\.[0-9]{2})?)', text)
                if match:
                    val = float(match.group(1))
                    if 30 <= val <= 130:
                        symbol = 'CHF' if 'CHF' in text else '€'
                        return f"{symbol}{val:.2f}"
    return None

def extract_tier4_global_regex(text):
    patterns = [
        r'(?:€|EUR|CHF)\s*([0-9]{2}(?:\.[0-9]{2})?)',
        r'([0-9]{2}(?:\.[0-9]{2})?)\s*(?:€|EUR)'
    ]
    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            val = float(match.group(1))
            if 30 <= val <= 130:
                return f"€{val:.2f}"
    return None

def scrape_day_pass(url):
    if not url or url == 'N/A':
        return None, "No URL"
        
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS, verify=False)
        response.raise_for_status()
        html_content = response.text
        soup = BeautifulSoup(html_content, 'html.parser')
        
        price = extract_tier1_json_ld(soup)
        if price: return price, "Tier 1 (JSON-LD)"
        
        price = extract_tier2_css_classes(soup)
        if price: return price, "Tier 2 (CSS Class)"
        
        price = extract_tier3_contextual(soup)
        if price: return price, "Tier 3 (Contextual)"
        
        price = extract_tier4_global_regex(html_content)
        if price: return price, "Tier 4 (Regex)"
                    
    except requests.exceptions.RequestException:
        return None, "Connection Error"
    except Exception:
        return None, "Parsing Error"
        
    return None, "No price found"

def main():
    print(f"Loading master dataset from '{MASTER_CSV}'...")
    
    updated_rows = []
    audit_rows = []
    
    success_count = 0
    total_checked = 0
    
    try:
        with open(MASTER_CSV, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            
            try:
                name_idx = header.index('Resort Name')
                website_idx = header.index('Website')
                pass_idx = header.index('Day Pass (24/25 Est)')
            except ValueError as e:
                print(f"[x] Column header error: {e}. Please ensure 'Day Pass (24/25 Est)' exists.")
                return

            updated_header = list(header)
            updated_header[pass_idx] = 'Day Pass (2026/27 Est)'
            updated_rows.append(updated_header)
            
            audit_rows.append(['Resort Name', 'Website', 'Old Price', 'New Price', 'Extraction Method', '% Difference', 'Review Flag'])

            for row in reader:
                if len(row) <= max(name_idx, website_idx, pass_idx):
                    updated_rows.append(row)
                    continue
                    
                resort_name = row[name_idx]
                website = row[website_idx]
                current_price_str = row[pass_idx]
                
                total_checked += 1
                print(f"[{total_checked}] {resort_name} ({website})")
                
                new_price_str, method = scrape_day_pass(website)
                
                new_row = list(row)
                review_flag = "OK"
                diff_pct = 0.0
                
                if new_price_str:
                    old_val = clean_price(current_price_str)
                    new_val = clean_price(new_price_str)
                    
                    if old_val > 0:
                        diff_pct = ((new_val - old_val) / old_val) * 100
                        
                        if diff_pct < 0:
                            review_flag = "FLAG: Price Dropped (Check if Child/Half-day pass)"
                        elif diff_pct > 15:
                            review_flag = "FLAG: High Increase (Check if Multi-day pass)"
                        elif method == "Tier 4 (Regex)":
                            review_flag = "WARNING: Found via Regex (Low Confidence)"
                    
                    print(f"  -> Found updated price: {new_price_str} via {method} (was {current_price_str}) - Flag: {review_flag}")
                    new_row[pass_idx] = new_price_str
                    success_count += 1
                else:
                    new_price_str = current_price_str
                    review_flag = f"INFO: {method} - Retained Old Price"
                    print(f"  -> {method}. Retaining existing baseline.")
                
                updated_rows.append(new_row)
                
                audit_rows.append([
                    resort_name, website, current_price_str, new_price_str, method, f"{diff_pct:.1f}%", review_flag
                ])

    except FileNotFoundError:
        print(f"[x] Error: Could not find '{MASTER_CSV}'.")
        return

    print("\n" + "="*45)
    print(" 4-TIER LIFT PASS AUTOMATION COMPLETE")
    print("="*45)
    print(f"Total resorts processed: {total_checked}")
    if total_checked > 0:
        print(f"Successfully auto-refreshed pricing: {success_count} ({success_count/total_checked*100:.1f}%)")
    
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)
        
    with open(AUDIT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerows(audit_rows)
        
    print(f"Updated master dataset saved to '{OUTPUT_CSV}'.")
    print(f"Audit report saved to '{AUDIT_CSV}'. Please review flagged items!")

if __name__ == '__main__':
    main()
