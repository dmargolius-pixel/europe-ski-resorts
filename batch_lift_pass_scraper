import csv
import re
import os
import time
import ssl
import argparse
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser

# Optional imports: works out-of-the-box on Mac Terminal even if pip packages aren't installed,
# and unlocks advanced parsing when BeautifulSoup / Playwright are available.
try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# --- Configuration Constants ---
DEFAULT_MASTER_CSV = 'Ski_resorts_Master.csv'
FALLBACK_MASTER_CSV = 'Ski resorts - Master.csv'
DEFAULT_OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
DEFAULT_AUDIT_CSV = 'lift_pass_batch_audit.csv'
TIMEOUT_SECONDS = 15

# Sanity Guardrails: Extracted price must be within [0.88x, 1.28x] of previous known price
MIN_RATIO = 0.88
MAX_RATIO = 1.28
SEASON_INFLATION_RATE = 0.035

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-GB,en;q=0.9,de;q=0.8,fr;q=0.7,it;q=0.6,es;q=0.5',
    'Cache-Control': 'no-cache'
}

# Multilingual keywords used to score and discover pricing links on a resort homepage
LINK_KEYWORDS_HIGH = [
    'skipass', 'ski-pass', 'tageskarte', 'tarif', 'preise', 'prices',
    'forfait', 'prezzi', 'listino', 'skiticket', 'liftpass', 'lift-pass',
    'day-pass', 'tageskarten', 'tarifas'
]
LINK_KEYWORDS_MEDIUM = [
    'ticket', 'winter', 'bergbahn', 'remontees', 'impianti', 'rates', 'shop'
]

# Keywords that indicate a number nearby is a 1-day adult ski pass
CONTEXT_POSITIVE_KEYWORDS = [
    '1 tag', '1-tag', 'tageskarte', 'erwachsene', '1 day', 'day pass', 'adult',
    '1 jour', 'journée', 'journee', 'adulte', '1 giorno', 'giornaliero', 'adulto',
    '1 día', '1 dia', 'forfait', 'skipass', 'hauptsaison', 'high season', 'peak'
]

# Keywords that indicate a number nearby is NOT a 1-day adult pass (e.g. child, 6-day, half-day)
CONTEXT_NEGATIVE_KEYWORDS = [
    '6 tage', '6 days', '6 jours', '6 giorni', 'saison', 'season pass',
    'kinder', 'child', 'enfant', 'bambini', 'junior', 'senior', 'student',
    'halbtageskarte', 'half day', 'demi-journée', '4 stunden', '4 hours',
    '2 tage', '3 tage', '4 tage', '5 tage', '7 tage', 'familie', 'family'
]


def create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
    except Exception:
        pass
    return ctx

SSL_CTX = create_ssl_context()


class SimpleHTMLParser(HTMLParser):
    """Built-in fallback parser in case BeautifulSoup4 is not installed locally."""
    def __init__(self):
        super().__init__()
        self.links = []
        self.text_parts = []
        self._in_ignored = False
        self._current_href = None
        self._current_link_text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self._in_ignored = True
        elif tag == 'a':
            attr_dict = dict(attrs)
            self._current_href = attr_dict.get('href', '')
            self._current_link_text = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self._in_ignored = False
        elif tag == 'a' and self._current_href is not None:
            self.links.append((self._current_href, ' '.join(self._current_link_text)))
            self._current_href = None

    def handle_data(self, data):
        if not self._in_ignored:
            cleaned = data.strip()
            if cleaned:
                self.text_parts.append(cleaned)
                if self._current_href is not None:
                    self._current_link_text.append(cleaned)


def fetch_html(url, timeout=TIMEOUT_SECONDS):
    """Fetches HTML using requests if available, otherwise falls back to standard urllib."""
    if HAS_REQUESTS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text, resp.url
        except Exception:
            return None, url
    else:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as response:
                if response.status == 200:
                    raw = response.read()
                    return raw.decode('utf-8', errors='replace'), response.geturl()
        except Exception:
            return None, url
    return None, url


def parse_html_text_and_links(html_content, base_url):
    """Extracts visible text and scored candidate links from raw HTML."""
    links = []
    if HAS_BS4:
        soup = BeautifulSoup(html_content, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            text = a.get_text(separator=' ').strip()
            links.append((href, text))
        for tag in soup(['script', 'style', 'noscript', 'svg']):
            tag.decompose()
        visible_text = soup.get_text(separator=' ')
    else:
        parser = SimpleHTMLParser()
        parser.feed(html_content)
        links = parser.links
        visible_text = ' '.join(parser.text_parts)

    # Normalize whitespace
    visible_text = re.sub(r'\s+', ' ', visible_text)

    # Score links for pricing relevance
    scored_urls = {}
    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = parsed_base.netloc.lower().replace('www.', '')

    for href, anchor_text in links:
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
        full_url = urllib.parse.urljoin(base_url, href)
        parsed_link = urllib.parse.urlparse(full_url)
        link_domain = parsed_link.netloc.lower().replace('www.', '')

        # Keep same domain or known ticket subdomains
        if base_domain not in link_domain and not any(k in link_domain for k in ['ticket', 'skipass', 'shop', 'tarif']):
            continue

        # Skip media/document files except html/php/asp
        if re.search(r'\.(jpg|jpeg|png|gif|svg|pdf|zip|mp4|ics)$', parsed_link.path, re.I):
            continue

        combined = f"{parsed_link.path.lower()} {anchor_text.lower()}"
        score = 0
        for kw in LINK_KEYWORDS_HIGH:
            if kw in combined:
                score += 10
        for kw in LINK_KEYWORDS_MEDIUM:
            if kw in combined:
                score += 3

        if score > 0:
            scored_urls[full_url] = max(scored_urls.get(full_url, 0), score)

    sorted_candidates = sorted(scored_urls.keys(), key=lambda u: scored_urls[u], reverse=True)
    return visible_text, sorted_candidates[:8]


def clean_numeric_price(price_str):
    """Converts formatted strings like '€68.50' or 'CHF 75,00' to float."""
    if not price_str or str(price_str).strip().lower() in ['n/a', 'closed', 'none', '']:
        return 0.0
    normalized = str(price_str).replace(',', '.')
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)', normalized)
    return float(match.group(1)) if match else 0.0


def validate_price_bounds(val, prev_price_val):
    """Validates candidate price against previous year's baseline or realistic bounds."""
    if prev_price_val > 0:
        return (prev_price_val * MIN_RATIO) <= val <= (prev_price_val * MAX_RATIO)
    return 25.0 <= val <= 130.0


def extract_best_price_from_text(text, prev_price_val=0.0, default_currency='€'):
    """
    Finds all currency-number pairs in the text, scores each candidate by surrounding
    keywords (adult, 1-day, tageskarte vs child, 6-day), and returns the highest-confidence match.
    """
    if not text:
        return None

    # Match patterns like '€ 69,50', '69.50 EUR', 'CHF 85.-', '69,- €'
    pattern = re.compile(
        r'(?:(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*|-|\.\-)?|'
        r'([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\s*(?:\.\-|-)?\s*(€|EUR|CHF|SFr\.?))',
        re.IGNORECASE
    )

    candidates = []

    for match in pattern.finditer(text):
        curr1, val1, val2, curr2 = match.groups()
        raw_curr = (curr1 or curr2 or '').upper()
        raw_val = (val1 or val2 or '').replace(',', '.')

        try:
            val = float(raw_val)
        except ValueError:
            continue

        if not validate_price_bounds(val, prev_price_val):
            continue

        # Examine 120 characters before and after the price for context clues
        start_ctx = max(0, match.start() - 120)
        end_ctx = min(len(text), match.end() + 120)
        context = text[start_ctx:end_ctx].lower()

        score = 0
        for pos_kw in CONTEXT_POSITIVE_KEYWORDS:
            if pos_kw in context:
                score += 5
        for neg_kw in CONTEXT_NEGATIVE_KEYWORDS:
            if neg_kw in context:
                score -= 6

        # Bonus if close to previous price (+0% to +12% typical annual increase)
        if prev_price_val > 0:
            ratio = val / prev_price_val
            if 1.00 <= ratio <= 1.12:
                score += 8
            elif 0.95 <= ratio < 1.00 or 1.12 < ratio <= 1.20:
                score += 3

        symbol = 'CHF' if ('CHF' in raw_curr or 'SFR' in raw_curr or default_currency == 'CHF') else '€'
        candidates.append((score, val, f"{symbol}{val:.2f}"))

    if not candidates:
        return None

    # Sort by highest context score, then prefer values >= previous price
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_val, best_formatted = candidates[0]

    # Reject if strong negative context and no positive context
    if best_score < -3:
        return None

    return best_formatted


def stage1_spider_official_site(website_url, prev_price_val, default_currency):
    """
    Stage 1: Crawls the resort's homepage, discovers actual pricing/skipass links in the DOM,
    and visits the top candidate pages to extract the 1-day adult price.
    """
    if not website_url or website_url.strip().lower() in ['n/a', 'closed', '']:
        return None, None, None

    base_url = website_url.strip().rstrip('/')
    if not base_url.startswith(('http://', 'https://')):
        base_url = 'https://' + base_url

    html, final_url = fetch_html(base_url)
    if not html:
        return None, None, None

    home_text, candidate_links = parse_html_text_and_links(html, final_url)

    # Also append high-probability direct subpaths in case navigation is JS-hidden
    parsed_root = urllib.parse.urlparse(final_url)
    root_domain_url = f"{parsed_root.scheme}://{parsed_root.netloc}"
    for fallback_slug in ['/skipass', '/tarifs', '/preise', '/prices', '/tickets', '/prezzi']:
        fallback_full = root_domain_url + fallback_slug
        if fallback_full not in candidate_links:
            candidate_links.append(fallback_full)

    # First check discovered pricing subpages (more specific than homepage)
    for link_url in candidate_links[:6]:
        sub_html, sub_final_url = fetch_html(link_url, timeout=10)
        if sub_html:
            sub_text, _ = parse_html_text_and_links(sub_html, sub_final_url)
            price = extract_best_price_from_text(sub_text, prev_price_val, default_currency)
            if price:
                return price, "Stage 1 (Official Site Link Spider)", sub_final_url

    # Check homepage text if subpages didn't yield a result
    price = extract_best_price_from_text(home_text, prev_price_val, default_currency)
    if price:
        return price, "Stage 1 (Official Homepage)", final_url

    return None, None, candidate_links[0] if candidate_links else final_url


def stage2_aggregator_lookup(resort_name, country, prev_price_val, default_currency):
    """
    Stage 2: Queries standardized European ski resort aggregators (Skiresort.info & Bergfex)
    where 1-day adult pass prices are published in clean, static HTML tables.
    """
    clean_name = re.sub(r'\(.*?\)', '', resort_name).strip()
    slug = re.sub(r'[^a-z0-9]+', '-', clean_name.lower()).strip('-')

    # 2A: Try direct Skiresort.info resort page & search
    skiresort_urls = [
        f"https://www.skiresort.info/ski-resort/{slug}/ski-pass-prices/",
        f"https://www.skiresort.info/ski-resort/{slug}/"
    ]
    for sr_url in skiresort_urls:
        html, final_url = fetch_html(sr_url, timeout=10)
        if html and 'Adult' in html:
            text, _ = parse_html_text_and_links(html, final_url)
            price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 2 (Skiresort.info Table)", final_url

    # 2B: Try Bergfex direct pricing page for Alpine resorts
    bergfex_country_map = {
        'austria': 'oesterreich',
        'switzerland': 'schweiz',
        'italy': 'italien',
        'france': 'frankreich',
        'germany': 'deutschland'
    }
    bf_country = bergfex_country_map.get(country.strip().lower(), '')
    if bf_country:
        bf_url = f"https://www.bergfex.com/{slug}/preise/"
        html, final_url = fetch_html(bf_url, timeout=10)
        if html:
            text, _ = parse_html_text_and_links(html, final_url)
            price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 2 (Bergfex Pricing Table)", final_url

    return None, None, None


def stage3_headless_browser(target_url, prev_price_val, default_currency):
    """
    Stage 3: Uses Playwright (if installed) to render JavaScript-driven ticket tables.
    """
    if not HAS_PLAYWRIGHT or not target_url:
        return None, None, None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS['User-Agent'])
            page.goto(target_url, timeout=15000, wait_until="networkidle")
            rendered_html = page.content()
            browser.close()

            text, _ = parse_html_text_and_links(rendered_html, target_url)
            price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 3 (Playwright Headless JS)", target_url
    except Exception:
        pass
    return None, None, None


def stage4_search_discovery(resort_name, country, prev_price_val, default_currency):
    """
    Stage 4: Queries DuckDuckGo Lite & HTML endpoints for targeted aggregator & official snippets,
    and follows top search result URLs if the snippet itself truncates the price.
    """
    queries = [
        f'"{resort_name}" ski pass price adult day skiresort.info',
        f'"{resort_name}" {country} skipass tageskarte forfait adult price 2026'
    ]

    for query in queries:
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            html, _ = fetch_html(url, timeout=12)
            if not html:
                continue

            text, links = parse_html_text_and_links(html, url)
            price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 4 (Search Snippet)", url

            # Follow top Skiresort.info or Bergfex link discovered in search results
            if HAS_BS4:
                soup = BeautifulSoup(html, 'html.parser')
                for a in soup.find_all('a', class_='result__a', href=True):
                    href = a['href']
                    if 'uddg=' in href:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        href = qs.get('uddg', [href])[0]
                    if any(domain in href for domain in ['skiresort.info', 'bergfex.', 'onthesnow.']):
                        sub_html, sub_url = fetch_html(href, timeout=10)
                        if sub_html:
                            sub_text, _ = parse_html_text_and_links(sub_html, sub_url)
                            found_price = extract_best_price_from_text(sub_text, prev_price_val, default_currency)
                            if found_price:
                                return found_price, "Stage 4 (Search -> Aggregator Page)", sub_url
        except Exception:
            continue

    return None, None, None


def process_batch(master_csv, output_csv, audit_csv, offset, limit):
    # Automatically resolve filename differences ('Ski_resorts_Master.csv' vs 'Ski resorts - Master.csv')
    if not os.path.exists(master_csv) and os.path.exists(FALLBACK_MASTER_CSV):
        master_csv = FALLBACK_MASTER_CSV

    print("\n" + "=" * 68)
    print(" DEEP-DISCOVERY SKI LIFT PASS SCRAPER (4-STAGE PIPELINE)")
    print(f" Input File : {master_csv}")
    print(f" Offset     : {offset} | Batch Limit: {limit}")
    print(f" Capabilities: Requests={HAS_REQUESTS}, BS4={HAS_BS4}, Playwright={HAS_PLAYWRIGHT}")
    print("=" * 68 + "\n")

    if not os.path.exists(master_csv):
        print(f"[x] Error: Master CSV '{master_csv}' not found in current folder: {os.getcwd()}")
        return

    with open(master_csv, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    country_idx = next((i for i, h in enumerate(header) if 'Country' in h), 0)
    name_idx = next((i for i, h in enumerate(header) if 'Resort Name' in h), 1)
    website_idx = next((i for i, h in enumerate(header) if 'Website' in h), 9)
    pass_idx = next((i for i, h in enumerate(header) if 'Day Pass' in h or 'Pass' in h), 5)

    batch_rows = rows[offset:offset + limit] if limit > 0 else rows[offset:]

    if os.path.exists(output_csv) and offset > 0:
        with open(output_csv, 'r', encoding='utf-8') as f:
            all_output_rows = list(csv.reader(f))
    else:
        updated_header = list(header)
        updated_header[pass_idx] = 'Day Pass (2026/27)'
        all_output_rows = [updated_header] + [list(r) for r in rows]

    audit_header = [
        'Resort Name', 'Country', 'Website', 'Previous Price',
        'New Price', 'Variance %', 'Extraction Stage', 'Source URL', 'Status'
    ]
    audit_rows = [audit_header]

    stats = {'scraped': 0, 'estimated': 0, 'manual': 0}

    for idx, row in enumerate(batch_rows, start=offset):
        if len(row) <= max(name_idx, website_idx, pass_idx):
            continue

        country = row[country_idx] if len(row) > country_idx else ""
        resort_name = row[name_idx]
        website = row[website_idx]
        prev_price_str = row[pass_idx]
        prev_val = clean_numeric_price(prev_price_str)
        default_curr = 'CHF' if ('CHF' in prev_price_str or country.strip().lower() == 'switzerland') else '€'

        print(f"[{idx + 1}/{len(rows)}] {resort_name} ({country}) | Baseline: {prev_price_str}")

        # Stage 1: Official Website Spidering
        price, method, source_url = stage1_spider_official_site(website, prev_val, default_curr)

        # Stage 2: Standardized Aggregator Lookup (Skiresort.info / Bergfex)
        if not price:
            price, method, agg_url = stage2_aggregator_lookup(resort_name, country, prev_val, default_curr)
            if price:
                source_url = agg_url

        # Stage 3: Headless Browser JS Rendering (if Playwright installed)
        if not price and HAS_PLAYWRIGHT and source_url:
            price, method, js_url = stage3_headless_browser(source_url, prev_val, default_curr)
            if price:
                source_url = js_url

        # Stage 4: Search Engine & Aggregator Deep Discovery
        if not price:
            price, method, search_url = stage4_search_discovery(resort_name, country, prev_val, default_curr)
            if price:
                source_url = search_url

        # Final Evaluation or Fallback
        if price:
            new_val = clean_numeric_price(price)
            variance_pct = f"{((new_val - prev_val) / prev_val * 100):+.1f}%" if prev_val > 0 else "N/A"
            status = "VERIFIED_SCRAPE"
            stats['scraped'] += 1
            print(f"   [+] {status}: {price} ({variance_pct}) via {method}")
            print(f"       Source: {source_url}")
        else:
            if prev_val > 0:
                est_val = prev_val * (1 + SEASON_INFLATION_RATE)
                price = f"{default_curr}{est_val:.2f}"
                variance_pct = "+3.5%"
                method = "Fallback (Index +3.5%)"
                source_url = website
                status = "ESTIMATED"
                stats['estimated'] += 1
                print(f"   [~] ESTIMATED: {price} (Requires manual check if exact needed)")
            else:
                price = prev_price_str
                variance_pct = "0.0%"
                method = "None"
                source_url = website
                status = "MANUAL_REQUIRED"
                stats['manual'] += 1
                print(f"   [!] MANUAL REQUIRED: No baseline or live price found")

        # Update row in full output dataset
        target_row_idx = idx + 1
        updated_row = list(all_output_rows[target_row_idx]) if target_row_idx < len(all_output_rows) else list(row)
        updated_row[pass_idx] = price
        if target_row_idx < len(all_output_rows):
            all_output_rows[target_row_idx] = updated_row
        else:
            all_output_rows.append(updated_row)

        audit_rows.append([
            resort_name, country, website, prev_price_str,
            price, variance_pct, method, source_url, status
        ])

        time.sleep(1.2)

    # Save updated dataset
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(all_output_rows)

    # Save or append audit report
    audit_mode = 'a' if (os.path.exists(audit_csv) and offset > 0) else 'w'
    with open(audit_csv, audit_mode, newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if audit_mode == 'w':
            writer.writerows(audit_rows)
        else:
            writer.writerows(audit_rows[1:])

    total_batch = stats['scraped'] + stats['estimated'] + stats['manual']
    capture_rate = (stats['scraped'] / total_batch * 100) if total_batch > 0 else 0.0

    print("\n" + "=" * 68)
    print(f" BATCH COMPLETE: {total_batch} Resorts Processed")
    print(f" Live Verified Prices : {stats['scraped']} ({capture_rate:.1f}%)")
    print(f" Indexed Fallbacks    : {stats['estimated']}")
    print(f" Manual Check Needed  : {stats['manual']}")
    print(f" Output CSV Saved To  : {output_csv}")
    print(f" Audit Log Saved To   : {audit_csv}")
    print("=" * 68 + "\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="4-Stage Deep Discovery Ski Lift Pass Scraper")
    parser.add_argument('--master', type=str, default=DEFAULT_MASTER_CSV, help="Master input CSV path")
    parser.add_argument('--output', type=str, default=DEFAULT_OUTPUT_CSV, help="Updated output CSV path")
    parser.add_argument('--audit', type=str, default=DEFAULT_AUDIT_CSV, help="Audit log CSV path")
    parser.add_argument('--offset', type=int, default=0, help="Starting row offset (default: 0)")
    parser.add_argument('--limit', type=int, default=25, help="Number of resorts per batch (default: 25)")
    args = parser.parse_args()

    process_batch(args.master, args.output, args.audit, args.offset, args.limit)
