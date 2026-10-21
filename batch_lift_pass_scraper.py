import argparse
import csv
import datetime
import io
import os
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Optional Playwright & PDF support (gracefully degrades if not installed locally)
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

MASTER_CSV = 'Ski resorts - Master.csv'
OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
AUDIT_CSV = 'lift_pass_batch_audit.csv'
LEGACY_AUDIT_CSV = 'lift_pass_audit_report.csv'
TIMEOUT_SECONDS = 12
SEASON_INFLATION_RATE = 0.035  # +3.5% fallback index

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'de-AT,de;q=0.9,en-GB;q=0.8,en;q=0.7,fr;q=0.6,it;q=0.5'
}

POSITIVE_KEYWORDS = [
    'erwachsene', 'erwachsener', 'adult', 'adults', 'adulte', 'adultes', 'adulti', 'adulto',
    '1 tag', '1-tag', '1 day', '1-day', '1 jour', '1 journée', '1 giorno', 'giornaliero',
    'tageskarte', 'tagesskipass', 'tages-skipass', 'day pass', 'day ticket', 'forfait 1 jour',
    'hauptsaison', 'hochsaison', 'peak', 'high season', 'haute saison', 'alta stagione',
    'kassa', 'kassenpreis', 'regulär', 'standard'
]

NEGATIVE_KEYWORDS = [
    'kind', 'kinder', 'child', 'children', 'enfant', 'enfants', 'bambini', 'bambino',
    'jugend', 'youth', 'junior', 'senior', 'senioren', 'student', 'kleinkind', 'mini', 'bambini',
    'halbtag', 'half day', 'half-day', 'demi-journée', 'mattino', 'pomeriggio', 'vormittag', 'nachmittag',
    'stunden', 'hours', 'heures', 'ore', 'ab 11', 'ab 12', 'ab 13', 'ab 14',
    'verleih', 'rental', 'location', 'noleggio', 'skischule', 'ski school', 'lesson',
    'parking', 'parkplatz', 'depot', 'rodel', 'toboggan', 'sommer', 'summer', 'bike',
    'wandern', 'hiking', 'all-in-card', 'saisonkarte', 'jahreskarte', 'season pass',
    '2 tage', '3 tage', '4 tage', '5 tage', '6 tage', '2 days', '3 days', '6 days', '2 jours', '6 jours'
]

# Dynamic teaser prefixes that indicate low-season "from €XX" prices rather than peak prices
TEASER_PREFIX_KEYWORDS = [
    'ab €', 'ab eur', 'ab chf', 'from €', 'from eur', 'from chf',
    'à partir de', 'da €', 'nebensaison', 'vorsaison', 'low season', 'early bird', 'online-frühbucher'
]

HIGH_PRIORITY_LINK_KEYWORDS = [
    'skipass', 'tageskarte', 'tagesskipass', 'winterpreise', 'skitarife',
    'preise', 'tarife', 'tarifs', 'forfait', 'prezzi', 'ticket', 'prices', 'billetterie', 'cennik'
]

# Forensic Fix #1 & #2: Strict blacklist for summer pages, season cards, and non-lift URLs
BLACKLIST_LINK_KEYWORDS = [
    'sommer', 'summer', 'ete', 'estate', 'bike', 'wandern', 'hiking', 'klettern',
    'paragleiten', 'tandem', 'rodel', 'toboggan', 'camping', 'hotel', 'unterkunft',
    'accommodation', 'webcam', 'wetter', 'weather', 'skischule', 'school', 'verleih',
    'rental', 'jobs', 'karriere', 'impressum', 'datenschutz', 'privacy', 'contact',
    'kontakt', 'all-in-card', 'saisonkarte', 'jahreskarte', 'season-pass', 'super-ski-card',
    'freizeitticket', 'snow-card', 'magic-pass', 'gutschein', 'voucher', 'loipe', 'langlauf'
]

# Forensic Fix #4: Known Bergfex & aggregator slug overrides for rebrands, compound names, and regions
BERGFEX_ALIAS_OVERRIDES = {
    'alpbach': ['alpbachtal', 'skijuwel'],
    'auffach': ['alpbachtal', 'skijuwel'],
    'niederau': ['alpbachtal', 'skijuwel'],
    'ankogel-mallnitz': ['ankogel'],
    'achensee - christlum': ['christlum'],
    'bad gastein': ['bad-gastein', 'schlossalm-angertal-stubnerkogel', 'gastein'],
    'bad hofgastein': ['bad-hofgastein', 'schlossalm-angertal-stubnerkogel'],
    'dorfgastein': ['dorfgastein-grossarltal'],
    'brixen im thale': ['brixen-im-thale', 'soell', 'skiwelt'],
    'ellmau': ['ellmau', 'soell', 'skiwelt'],
    'ellmau - going': ['ellmau', 'soell', 'skiwelt'],
    'söll': ['soell', 'skiwelt'],
    'hopfgarten': ['hopfgarten', 'soell', 'skiwelt'],
    'westendorf': ['westendorf', 'soell', 'skiwelt'],
    'fageralm': ['fageralm', 'reiteralm'],
    'fieberbrunn': ['fieberbrunn', 'saalbach-hinterglemm-leogang'],
    'hinterglemm': ['saalbach-hinterglemm-leogang'],
    'saalbach': ['saalbach-hinterglemm-leogang'],
    'leogang': ['saalbach-hinterglemm-leogang'],
    'glun gezer': ['glungezer'],
    'grossglockner resort kals-matrei': ['kals-matrei', 'gg-resort'],
    'hintertux glacier': ['hintertux'],
    'hochkössen': ['koessen'],
    'hochkrimml': ['hochkrimml-gerlosplatte', 'gerlos'],
    'königsleiten': ['koenigsleiten', 'gerlos'],
    'zell am ziller': ['zell-am-ziller', 'gerlos'],
    'hochzillertal-hochfügen': ['hochzillertal', 'ski-gletscherwelt-zillertal3000-tux'],
    'vallnord': ['pal-arinsal', 'vallnord-pal-arinsal'],
    'ordino arcalis': ['ordino-arcalis'],
    'grandvalira': ['grandvalira']
}

# Known multi-resort syndicates that share an identical 1-day adult peak price
SHARED_SYNDICATE_KEYS = [
    'skiwelt', 'ski juwel', 'zillertal arena', 'skicircus', 'snow space salzburg',
    '3 vallées', 'portes du soleil', 'paradiski', '4 vallées', 'via lattea', 'grand massif'
]


def clean_numeric_price(price_str):
    """Extracts float value from a price string."""
    if not price_str:
        return 0.0
    match = re.search(r'([0-9]+(?:[\.,][0-9]+)?)', str(price_str))
    if not match:
        return 0.0
    return float(match.group(1).replace(',', '.'))


def detect_currency(text, fallback_str=''):
    """Determines whether CHF or EUR applies."""
    if 'CHF' in str(text) or 'SFr' in str(text) or 'CHF' in str(fallback_str):
        return 'CHF'
    return '€'


def transliterate_slug(text):
    """
    Forensic Fix #4: Properly transliterates German/European characters
    (ä->ae, ö->oe, ü->ue, ß->ss) instead of stripping them, so Bergfex URLs
    like /damuels-mellau/, /galtuer/, /hochkoenig/, and /grosseck-speiereck/ resolve.
    """
    s = str(text).lower().strip()
    replacements = {
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'á': 'a', 'à': 'a', 'â': 'a', 'å': 'aa',
        'ó': 'o', 'ò': 'o', 'ô': 'o', 'ø': 'oe',
        'ú': 'u', 'ù': 'u', 'û': 'u', 'ñ': 'n', 'ç': 'c'
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    # Strip any remaining accents
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s


def generate_bergfex_slugs(resort_name):
    """Generates prioritized candidate Bergfex slugs for a resort name."""
    raw_lower = resort_name.lower().strip()
    candidates = []

    if raw_lower in BERGFEX_ALIAS_OVERRIDES:
        candidates.extend(BERGFEX_ALIAS_OVERRIDES[raw_lower])

    # Primary transliterated slug (e.g. "Damüls-Mellau" -> "damuels-mellau")
    primary = transliterate_slug(resort_name)
    if primary and primary not in candidates:
        candidates.append(primary)

    # Strip noise words like "glacier", "gletscher", "resort", "ski area"
    cleaned_words = re.sub(
        r'\b(glacier|gletscher|resort|ski|arena|area|bergbahnen|skigebiet)\b',
        '', raw_lower
    ).strip()
    cleaned_slug = transliterate_slug(cleaned_words)
    if cleaned_slug and cleaned_slug not in candidates:
        candidates.append(cleaned_slug)

    # If hyphenated or spaced ("Achensee - Christlum", "Fügen - Spieljoch", "Ankogel-Mallnitz"),
    # also try individual parts and concatenated parts
    if ' - ' in raw_lower or '-' in raw_lower:
        parts = [transliterate_slug(p) for p in re.split(r'\s*-\s*', raw_lower) if len(p.strip()) >= 4]
        for p in parts:
            if p and p not in candidates:
                candidates.append(p)

    # Concatenated without spaces (e.g. "Glun Gezer" -> "glungezer")
    no_space = transliterate_slug(raw_lower.replace(' ', ''))
    if no_space and no_space not in candidates:
        candidates.append(no_space)

    return candidates[:5]


def score_price_candidates(text, prev_price=0.0, strict_non_negative=True):
    """
    Scans text for valid ski pass prices (22 to 140) and scores each candidate
    using surrounding positive/negative multilingual context and baseline proximity.
    Forensic Fix #3: Strongly prefers non-negative seasonal increases (0% to +18%)
    and penalizes dynamic "ab €XX" teaser prices or child/half-day prices.
    """
    if not text:
        return None

    normalized_text = re.sub(r'\s+', ' ', text)
    patterns = [
        r'(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\b',
        r'\b([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\s*(€|EUR|CHF|SFr\.?)'
    ]

    candidates = []

    for pat_idx, pattern in enumerate(patterns):
        for match in re.finditer(pattern, normalized_text, re.IGNORECASE):
            raw_curr = match.group(1) if pat_idx == 0 else match.group(2)
            raw_val = match.group(2) if pat_idx == 0 else match.group(1)

            try:
                val = float(raw_val.replace(',', '.'))
            except ValueError:
                continue

            if not (20.0 <= val <= 135.0):
                continue

            # Exclude suspiciously round 100.00 promo deposits unless baseline was >= 82
            if val == 100.0 and prev_price < 82.0:
                continue

            start_pos = max(0, match.start() - 120)
            end_pos = min(len(normalized_text), match.end() + 120)
            window = normalized_text[start_pos:end_pos]
            window_lower = window.lower()

            # Immediate prefix window (25 chars before match) to catch "ab €44,50"
            prefix_lower = normalized_text[max(0, match.start() - 25):match.end()].lower()

            score = 0

            for kw in POSITIVE_KEYWORDS:
                if kw in window_lower:
                    score += 4

            for kw in NEGATIVE_KEYWORDS:
                if kw in window_lower:
                    score -= 6

            for teaser in TEASER_PREFIX_KEYWORDS:
                if teaser in prefix_lower or teaser in window_lower:
                    score -= 5

            if any(yr in window_lower for yr in ['2026', '2027', '26/27', '2026/27', '25/26']):
                score += 3
            if any(old_yr in window_lower for old_yr in ['2021', '2022', '2023', '22/23']):
                score -= 5

            # Forensic Fix #2 & #3: Baseline proximity with non-negative inflation bias
            if prev_price > 15.0:
                pct_diff = (val - prev_price) / prev_price
                if 0.0 <= pct_diff <= 0.165:
                    score += 10  # Ideal realistic 1-2 season ski pass increase (0% to +16.5%)
                elif 0.165 < pct_diff <= 0.22:
                    score += 4   # Plausible higher increase (+16.5% to +22%)
                elif -0.02 <= pct_diff < 0.0:
                    score += 2   # Virtually unchanged price
                elif -0.08 <= pct_diff < -0.02:
                    score -= 7   # Almost certainly an online early-bird teaser or off-peak price
                elif pct_diff < -0.08 or pct_diff > 0.22:
                    score -= 12  # Child pass, half-day pass, or multi-day/season card

                # Hard guardrail if strict_non_negative is enabled
                if strict_non_negative and (pct_diff < -0.03 or pct_diff > 0.22):
                    continue

            curr_symbol = 'CHF' if 'chf' in raw_curr.lower() or 'sfr' in raw_curr.lower() else '€'
            snippet_clean = window.strip()[:140]
            candidates.append((score, val, curr_symbol, snippet_clean))

    if not candidates:
        return None

    # Sort by highest score, then prefer the higher (peak) adult price among ties
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_val, best_curr, best_snippet = candidates[0]

    if best_score < 0:
        return None

    return best_val, best_curr, best_score, best_snippet


def parse_bergfex_price_page(html_content, prev_price=0.0):
    """
    Parses a Bergfex resort or /preise/ page specifically targeting the Adult (Erwachsene)
    1-Day (Tageskarte) price, avoiding child/youth columns.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Method A: Look for table rows or definition lists containing "Tageskarte" or "Erwachsene"
    for tr in soup.find_all(['tr', 'dl', 'div']):
        row_text = tr.get_text(' ', strip=True)
        row_lower = row_text.lower()
        if any(k in row_lower for k in ['tageskarte', '1 tag', 'tagesskipass', 'erwachsene', 'day ticket']):
            if len(row_text) < 300:
                cand = score_price_candidates(row_text, prev_price, strict_non_negative=True)
                if cand:
                    return cand

    # Method B: Full page text with contextual scoring
    full_text = soup.get_text(' ', strip=True)
    return score_price_candidates(full_text, prev_price, strict_non_negative=True)


def fetch_from_bergfex(resort_name, country, prev_price=0.0):
    """
    Forensic Fix #4: Tests transliterated slugs and alias overrides on both
    /slug/preise/ and /slug/ across bergfex.com and bergfex.at.
    """
    slugs = generate_bergfex_slugs(resort_name)
    for slug in slugs:
        urls_to_try = [
            f"https://www.bergfex.com/{slug}/preise/",
            f"https://www.bergfex.com/{slug}/",
            f"https://www.bergfex.at/{slug}/preise/"
        ]
        for b_url in urls_to_try:
            try:
                resp = requests.get(b_url, headers=HEADERS, timeout=8, verify=False, allow_redirects=True)
                if resp.status_code == 200 and 'bergfex' in resp.url:
                    # Reject if redirected to a generic country search page or summer page
                    if '/sommer/' in resp.url.lower():
                        continue
                    cand = parse_bergfex_price_page(resp.text, prev_price)
                    if cand:
                        stage_label = "Stage 1B (Bergfex /preise/)" if '/preise' in b_url else "Stage 1C (Bergfex Direct)"
                        return cand, stage_label, resp.url
            except Exception:
                continue
    return None, None, None


def fetch_from_skiresort_info(resort_name, prev_price=0.0):
    """Queries Skiresort.info resort overview page using transliterated slugs."""
    slugs = generate_bergfex_slugs(resort_name)
    for slug in slugs[:3]:
        s_url = f"https://www.skiresort.info/ski-resort/{slug}/"
        try:
            resp = requests.get(s_url, headers=HEADERS, timeout=8, verify=False, allow_redirects=True)
            if resp.status_code == 200 and '/ski-resort/' in resp.url:
                soup = BeautifulSoup(resp.text, 'html.parser')
                cand = score_price_candidates(soup.get_text(' ', strip=True), prev_price, strict_non_negative=True)
                if cand:
                    return cand, "Stage 1A (Skiresort.info)", resp.url
        except Exception:
            continue
    return None, None, None


def rank_internal_pricing_links(base_url, html_content):
    """
    Finds and ranks internal links likely to contain WINTER 1-day lift pass prices,
    strictly blacklisting summer (`preise-sommer`, `sommer-preise`) and season-card pages.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    base_domain = urlparse(base_url).netloc.replace('www.', '')
    scored_links = {}
    pdf_links = []

    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue

        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        link_domain = parsed.netloc.replace('www.', '')

        if base_domain not in link_domain and not any(t in link_domain for t in ['skidata', 'axess', 'feratel', 'starjack']):
            continue

        combined_text = (parsed.path + ' ' + a_tag.get_text(' ', strip=True)).lower()

        # Forensic Fix #1 & #2: Immediately discard summer or season-card links
        if any(bad in combined_text for bad in BLACKLIST_LINK_KEYWORDS):
            continue

        score = 0
        for kw in HIGH_PRIORITY_LINK_KEYWORDS:
            if kw in combined_text:
                score += 5

        if any(w in combined_text for w in ['winter', 'hiver', 'inverno', 'skibetrieb', 'tageskarten', '2026', '2027']):
            score += 6

        if score > 0:
            if parsed.path.lower().endswith('.pdf'):
                pdf_links.append((score, full_url))
            else:
                if full_url not in scored_links or score > scored_links[full_url]:
                    scored_links[full_url] = score

    sorted_html_links = [url for url, _ in sorted(scored_links.items(), key=lambda x: x[1], reverse=True)[:4]]
    sorted_pdf_links = [url for _, url in sorted(pdf_links, key=lambda x: x[0], reverse=True)[:2]]
    return sorted_html_links, sorted_pdf_links


def extract_from_pdf(pdf_url, prev_price=0.0):
    """Downloads a pricing PDF into memory and runs contextual candidate scoring."""
    if not PDF_AVAILABLE or any(bad in pdf_url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
        return None
    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=TIMEOUT_SECONDS, verify=False)
        if resp.status_code == 200 and b'%PDF' in resp.content[:1024]:
            reader = PdfReader(io.BytesIO(resp.content))
            full_text = []
            for page in reader.pages[:6]:
                full_text.append(page.extract_text() or '')
            return score_price_candidates(' '.join(full_text), prev_price, strict_non_negative=True)
    except Exception:
        pass
    return None


def scrape_with_playwright(url, prev_price=0.0):
    """Uses Headless Chromium to dismiss cookie banners, render JS widgets, and score prices."""
    if not PLAYWRIGHT_AVAILABLE or any(bad in url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
        return None, None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS['User-Agent'],
                locale='de-AT'
            )
            page = context.new_page()
            page.goto(url, timeout=14000, wait_until='domcontentloaded')
            page.wait_for_timeout(1200)

            cookie_selectors = [
                "button:has-text('Alle akzeptieren')",
                "button:has-text('Accept all')",
                "button:has-text('Tout accepter')",
                "button:has-text('Accetta tutto')",
                "#onetrust-accept-btn-handler",
                "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
            ]
            for sel in cookie_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=400):
                        btn.click(timeout=800)
                        page.wait_for_timeout(400)
                        break
                except Exception:
                    pass

            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(800)

            rendered_html = page.content()
            visible_text = page.inner_text('body')
            browser.close()

            candidate = score_price_candidates(visible_text, prev_price, strict_non_negative=True)
            return candidate, rendered_html
    except Exception:
        return None, None


def get_syndicate_cache_key(region_str, website_url):
    """Returns a normalized cache key if the resort belongs to a shared-price ski syndicate."""
    reg_lower = str(region_str).lower()
    for syn in SHARED_SYNDICATE_KEYS:
        if syn in reg_lower:
            return f"syndicate:{syn}"
    if website_url and website_url.strip().lower() not in ['n/a', 'closed', '']:
        t_url = website_url.strip()
        if not t_url.startswith(('http://', 'https://')):
            t_url = 'https://' + t_url
        domain = urlparse(t_url).netloc.lower().replace('www.', '')
        if domain:
            return f"domain:{domain}"
    return None


def deep_discover_resort_price(resort_name, country, region_str, website_url, prev_price, shared_cache):
    """
    Executes the v6 4-Stage Deep Discovery Pipeline:
      Stage 0: Shared Ski Area Syndicate & Domain Cache
      Stage 1: Bergfex (/preise/ & Direct with Umlaut/Alias Engine) + Skiresort.info
      Stage 2: Official Site Winter Spider (Summer & Season-Card Blacklisted) + PDF
      Stage 3: Headless Chromium JS Rendering (Playwright)
    """
    # STAGE 0: Shared Ski Area / Domain Cache
    cache_key = get_syndicate_cache_key(region_str, website_url)
    if cache_key and cache_key in shared_cache:
        cached_price, cached_source, cached_snippet = shared_cache[cache_key]
        cached_val = clean_numeric_price(cached_price)
        if prev_price <= 0 or (-0.03 <= (cached_val - prev_price) / prev_price <= 0.22):
            return cached_price, "Stage 0 (Shared Ski Area Domain Cache)", cached_source, cached_snippet

    # STAGE 1A/1B/1C: Bergfex Direct & /preise/ Lookup (Highest accuracy in v5)
    cand, stage_label, b_url = fetch_from_bergfex(resort_name, country, prev_price)
    if cand:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, b_url, snippet)
        return price_str, stage_label, b_url, snippet

    # STAGE 1D: Skiresort.info Direct Lookup
    cand, stage_label, s_url = fetch_from_skiresort_info(resort_name, prev_price)
    if cand:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, s_url, snippet)
        return price_str, stage_label, s_url, snippet

    # STAGE 2: Official Website Spider (with strict summer/season-card blacklist)
    if not website_url or website_url.strip().lower() in ['n/a', 'closed', '']:
        return None, "No URL", "SKIPPED", ""

    target_url = website_url.strip()
    if not target_url.startswith(('http://', 'https://')):
        target_url = 'https://' + target_url

    html_subpages = []
    pdf_links = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=TIMEOUT_SECONDS, verify=False)
        if resp.status_code == 200:
            html_subpages, pdf_links = rank_internal_pricing_links(target_url, resp.text)
    except Exception:
        pass

    for sub_url in html_subpages:
        if any(bad in sub_url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
            continue
        try:
            sub_resp = requests.get(sub_url, headers=HEADERS, timeout=TIMEOUT_SECONDS, verify=False)
            if sub_resp.status_code == 200 and not any(bad in sub_resp.url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
                sub_soup = BeautifulSoup(sub_resp.text, 'html.parser')
                cand = score_price_candidates(sub_soup.get_text(' ', strip=True), prev_price, strict_non_negative=True)
                if cand and cand[2] >= 6:
                    val, curr, score, snippet = cand
                    price_str = f"{curr}{val:.2f}"
                    if cache_key:
                        shared_cache[cache_key] = (price_str, sub_resp.url, snippet)
                    return price_str, "Stage 2 (Official Site Spider)", sub_resp.url, snippet

                _, more_pdfs = rank_internal_pricing_links(sub_resp.url, sub_resp.text)
                for p_url in more_pdfs:
                    if p_url not in pdf_links:
                        pdf_links.append(p_url)
        except Exception:
            continue

    # STAGE 2B: PDF Price Sheet Extraction
    for pdf_url in pdf_links[:2]:
        cand = extract_from_pdf(pdf_url, prev_price)
        if cand and cand[2] >= 4:
            val, curr, score, snippet = cand
            price_str = f"{curr}{val:.2f}"
            return price_str, "Stage 2B (Official PDF Price Sheet)", pdf_url, snippet

    # STAGE 3: Headless Chromium JS Rendering on best winter pricing subpage
    if html_subpages:
        js_target = html_subpages[0]
        cand, _ = scrape_with_playwright(js_target, prev_price)
        if cand and cand[2] >= 6:
            val, curr, score, snippet = cand
            price_str = f"{curr}{val:.2f}"
            if cache_key:
                shared_cache[cache_key] = (price_str, js_target, snippet)
            return price_str, "Stage 3 (Headless Chromium JS)", js_target, snippet

    return None, "Fallback (Index +3.5%)", target_url, ""


def write_github_step_summary(summary_rows, total_count, verified_count, estimated_count):
    """Writes a clean Markdown audit table directly to $GITHUB_STEP_SUMMARY if running in GitHub Actions."""
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return
    try:
        pct = (verified_count / total_count * 100.0) if total_count else 0.0
        lines = [
            "## 🎿 v6 Deep-Discovery Lift Pass Scraper — Batch Audit Summary",
            f"- **Resorts Processed:** `{total_count}`",
            f"- **Live Verified Scrapes:** `{verified_count}` (`{pct:.1f}%`)",
            f"- **Indexed Fallbacks (+3.5%):** `{estimated_count}`\n",
            "| # | Resort Name | Country | Baseline | 2026/27 Price | Var % | Status | Discovery Stage | Source |",
            "|---|---|---|---|---|---|---|---|---|"
        ]
        for r in summary_rows:
            idx, country, name, old_p, new_p, var_s, stage, status, src, _ = r
            src_display = f"[Link]({src})" if str(src).startswith('http') else str(src)
            lines.append(f"| {idx} | **{name}** | {country} | {old_p} | **{new_p}** | `{var_s}` | `{status}` | {stage} | {src_display} |")

        with open(summary_path, 'a', encoding='utf-8') as sf:
            sf.write('\n'.join(lines) + '\n')
        print("   [+] Wrote visual Markdown Audit Table to GitHub Actions Run Summary ($GITHUB_STEP_SUMMARY)!")
    except Exception as e:
        print(f"   [!] Could not write to $GITHUB_STEP_SUMMARY: {e}")


def main():
    parser = argparse.ArgumentParser(description="v6 Deep Discovery Lift Pass Scraper")
    parser.add_argument('--offset', type=int, default=0, help="Starting row index (0-based)")
    parser.add_argument('--limit', type=int, default=60, help="Max resorts to process in this batch (0 = all)")
    parser.add_argument('--auto-batch', action='store_true', help="Automatically select next unvalidated resorts")
    args = parser.parse_args()

    try:
        with open(MASTER_CSV, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except FileNotFoundError:
        print(f"[x] Error: Master CSV '{MASTER_CSV}' not found.")
        return

    country_idx = next((i for i, h in enumerate(header) if 'Country' in h), 0)
    name_idx = next((i for i, h in enumerate(header) if 'Resort Name' in h), 1)
    region_idx = next((i for i, h in enumerate(header) if 'Region' in h), 2)
    pass_idx = next((i for i, h in enumerate(header) if 'Day Pass' in h or 'Pass' in h), 5)
    website_idx = next((i for i, h in enumerate(header) if 'Website' in h), 9)
    validated_idx = next((i for i, h in enumerate(header) if 'Last Validated' in h), None)

    today_str = datetime.date.today().isoformat()

    if args.auto_batch and validated_idx is not None:
        unvalidated = [
            idx for idx, r in enumerate(rows)
            if len(r) > validated_idx and not str(r[validated_idx]).strip().startswith('2026-')
        ]
        target_indices = unvalidated[:args.limit] if args.limit > 0 else unvalidated
    else:
        start = max(0, args.offset)
        end = start + args.limit if args.limit > 0 else len(rows)
        target_indices = list(range(start, min(end, len(rows))))

    print("\n" + "=" * 78)
    print(" ENHANCED DEEP-DISCOVERY SKI LIFT PASS SCRAPER (v6 - Umlaut + Winter Guardrails)")
    print(f" Input File  : {MASTER_CSV}")
    print(f" Offset      : {args.offset} | Batch Limit: {len(target_indices)}")
    print(f" Capabilities: Requests=True, BS4=True, Playwright={PLAYWRIGHT_AVAILABLE}, PDF={PDF_AVAILABLE}")
    print("=" * 78 + "\n")

    updated_rows = [list(header)] + [list(r) for r in rows]
    audit_header = [
        'Row Index', 'Country', 'Resort Name', 'Old Price (24/25)',
        'New Price (26/27)', 'Variance (%)', 'Discovery Stage',
        'Review Status', 'Source URL', 'Matched Context Snippet'
    ]
    audit_rows = [audit_header]
    summary_rows = []
    shared_cache = {}

    live_scraped = 0
    indexed_fallback = 0

    for count, row_idx in enumerate(target_indices, 1):
        row = rows[row_idx]
        if len(row) <= max(country_idx, name_idx, pass_idx, website_idx):
            continue

        country = row[country_idx].strip()
        resort_name = row[name_idx].strip()
        region_str = row[region_idx].strip() if len(row) > region_idx else ""
        prev_price_str = row[pass_idx].strip()
        website = row[website_idx].strip()
        prev_val = clean_numeric_price(prev_price_str)
        curr = detect_currency(prev_price_str, 'CHF' if country == 'Switzerland' else '€')

        print(f"[{row_idx + 1}/{len(rows)}] {resort_name} ({country}) | Baseline: {curr}{prev_val:.2f}")

        scraped_price_str, stage, source_url, snippet = deep_discover_resort_price(
            resort_name, country, region_str, website, prev_val, shared_cache
        )

        updated_row = list(row)
        is_numeric_only_col = prev_price_str.replace('.', '', 1).replace(',', '', 1).isdigit()

        if scraped_price_str:
            new_val = clean_numeric_price(scraped_price_str)
            pct_change = ((new_val - prev_val) / prev_val * 100.0) if prev_val > 0 else 0.0
            variance_str = f"{pct_change:+.1f}%"
            status = "VERIFIED_SCRAPE"
            live_scraped += 1

            formatted_master_val = f"{new_val:.2f}" if is_numeric_only_col else f"{curr}{new_val:.2f}"
            display_new = f"{curr}{new_val:.2f}"
            updated_row[pass_idx] = formatted_master_val
            if validated_idx is not None and len(updated_row) > validated_idx:
                updated_row[validated_idx] = today_str

            print(f"   [+] VERIFIED_SCRAPE: {display_new} ({variance_str}) via {stage}")
            print(f"       Source: {source_url}")
        else:
            est_val = prev_val * (1.0 + SEASON_INFLATION_RATE) if prev_val > 0 else 0.0
            variance_str = "+3.5%"
            status = "ESTIMATED"
            stage = "Fallback (Index +3.5%)"
            indexed_fallback += 1

            formatted_master_val = f"{est_val:.2f}" if is_numeric_only_col else f"{curr}{est_val:.2f}"
            display_new = f"{curr}{est_val:.2f}"
            updated_row[pass_idx] = formatted_master_val
            print(f"   [~] ESTIMATED: {display_new} (Flagged for quick manual check)")

        updated_rows[row_idx + 1] = updated_row
        row_record = [
            row_idx + 1, country, resort_name, f"{curr}{prev_val:.2f}",
            display_new, variance_str, stage, status, source_url, snippet
        ]
        audit_rows.append(row_record)
        summary_rows.append(row_record)

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(updated_rows)

    # Save to both audit filenames so any workflow artifact step picks it up cleanly
    for audit_filename in [AUDIT_CSV, LEGACY_AUDIT_CSV]:
        with open(audit_filename, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerows(audit_rows)

    write_github_step_summary(summary_rows, len(target_indices), live_scraped, indexed_fallback)

    pct_verified = (live_scraped / len(target_indices) * 100.0) if target_indices else 0.0
    print("\n" + "=" * 78)
    print(f" BATCH COMPLETE: {len(target_indices)} Resorts Processed")
    print(f" Live Verified Prices : {live_scraped} ({pct_verified:.1f}%)")
    print(f" Indexed Fallbacks    : {indexed_fallback}")
    print(f" Output CSV Saved To  : {OUTPUT_CSV}")
    print(f" Audit Log Saved To   : {AUDIT_CSV}")
    print("=" * 78 + "\n")

    print("--- INLINE AUDIT LOG SUMMARY ---")
    print(f"{'Resort Name':<28} | {'Prev':<8} | {'New':<8} | {'Var %':<7} | {'Status':<15} | {'Stage'}")
    print("-" * 105)
    for r in summary_rows:
        _, _, r_name, old_p, new_p, var_s, stg, stat, _, _ = r
        print(f"{r_name[:28]:<28} | {old_p:<8} | {new_p:<8} | {var_s:<7} | {stat:<15} | {stg}")
    print("-" * 105)


if __name__ == '__main__':
    main()
