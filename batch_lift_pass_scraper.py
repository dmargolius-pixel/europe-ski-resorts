import argparse
import csv
import datetime
import io
import os
import re
import sys
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure real-time unbuffered console output in GitHub Actions logs
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# Optional Playwright & PDF support
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

# Persistent HTTP session for connection pooling and fewer TLS handshakes
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(HEADERS)

POSITIVE_KEYWORDS = [
    'erwachsene', 'erwachsener', 'adult', 'adults', 'adulte', 'adultes', 'adulti', 'adulto',
    '1 tag', '1-tag', '1 day', '1-day', '1 jour', '1 journée', '1 giorno', 'giornaliero',
    'tageskarte', 'tagesskipass', 'tages-skipass', 'day pass', 'day ticket', 'forfait 1 jour',
    'hauptsaison', 'hochsaison', 'peak', 'high season', 'haute saison', 'alta stagione',
    'kassa', 'kassenpreis', 'regulär', 'standard'
]

ADULT_KEYWORDS = ['erwachsene', 'erwachsener', 'adult', 'adults', 'adulte', 'adultes', 'adulti', 'adulto']
DAY_KEYWORDS = [
    '1 tag', '1-tag', 'tageskarte', 'tagesskipass', 'tages-skipass',
    '1 day', '1-day', 'day pass', 'day ticket', '1 jour', 'forfait 1 jour', '1 giorno', 'giornaliero'
]

CHILD_YOUTH_KEYWORDS = [
    'kind', 'kinder', 'child', 'children', 'enfant', 'enfants', 'bambini', 'bambino',
    'jugend', 'youth', 'junior', 'senior', 'senioren', 'student', 'kleinkind', 'mini'
]

NON_DAY_NEGATIVE_KEYWORDS = [
    'halbtag', 'half day', 'half-day', 'demi-journée', 'mattino', 'pomeriggio', 'vormittag', 'nachmittag',
    'stunden', 'hours', 'heures', 'ore', 'ab 11', 'ab 12', 'ab 13', 'ab 14',
    'verleih', 'rental', 'location', 'noleggio', 'skischule', 'ski school', 'lesson',
    'parking', 'parkplatz', 'depot', 'rodel', 'toboggan', 'sommer', 'summer', 'bike',
    'wandern', 'hiking', 'all-in-card', 'saisonkarte', 'jahreskarte', 'season pass',
    '2 tage', '3 tage', '4 tage', '5 tage', '6 tage', '2 days', '3 days', '6 days', '2 jours', '6 jours'
]

TEASER_PREFIX_KEYWORDS = [
    'ab €', 'ab eur', 'ab chf', 'from €', 'from eur', 'from chf',
    'à partir de', 'da €', 'nebensaison', 'vorsaison', 'low season', 'early bird', 'online-frühbucher'
]

HIGH_PRIORITY_LINK_KEYWORDS = [
    'skipass', 'tageskarte', 'tagesskipass', 'winterpreise', 'skitarife',
    'preise', 'tarife', 'tarifs', 'forfait', 'prezzi', 'ticket', 'prices', 'billetterie', 'cennik'
]

BLACKLIST_LINK_KEYWORDS = [
    'sommer', 'summer', 'ete', 'estate', 'bike', 'wandern', 'hiking', 'klettern',
    'paragleiten', 'tandem', 'rodel', 'toboggan', 'camping', 'hotel', 'unterkunft',
    'accommodation', 'webcam', 'wetter', 'weather', 'skischule', 'school', 'verleih',
    'rental', 'jobs', 'karriere', 'impressum', 'datenschutz', 'privacy', 'contact',
    'kontakt', 'all-in-card', 'saisonkarte', 'jahreskarte', 'season-pass', 'super-ski-card',
    'freizeitticket', 'snow-card', 'magic-pass', 'gutschein', 'voucher', 'loipe', 'langlauf'
]

# Country slug mappings for Bergfex and Skiresort.info directory pre-indexing
BERGFEX_COUNTRY_SLUGS = {
    'austria': 'oesterreich',
    'switzerland': 'schweiz',
    'france': 'frankreich',
    'italy': 'italien',
    'germany': 'deutschland',
    'andorra': 'andorra',
    'slovenia': 'slowenien',
    'poland': 'polen',
    'czechia': 'tschechien',
    'czech republic': 'tschechien',
    'slovakia': 'slowakei',
    'spain': 'spanien'
}

SKIRESORT_COUNTRY_SLUGS = {
    'albania': 'albania',
    'andorra': 'andorra',
    'austria': 'austria',
    'bosnia & herzegovina': 'bosnia-and-herzegovina',
    'bulgaria': 'bulgaria',
    'cyprus': 'cyprus',
    'czechia': 'czech-republic',
    'czech republic': 'czech-republic',
    'finland': 'finland',
    'france': 'france',
    'georgia': 'georgia',
    'germany': 'germany',
    'greece': 'greece',
    'italy': 'italy',
    'MONTENEGRO': 'montenegro',
    'norway': 'norway',
    'poland': 'poland',
    'romania': 'romania',
    'scotland': 'united-kingdom/scotland',
    'serbia': 'serbia',
    'slovakia': 'slovakia',
    'slovenia': 'slovenia',
    'spain': 'spain',
    'sweden': 'sweden',
    'switzerland': 'switzerland'
}

# Verified Bergfex valley-suffix & compound slug overrides
BERGFEX_ALIAS_OVERRIDES = {
    'achensee - christlum': ['christlum-achenkirch', 'christlum'],
    'alpbach': ['alpbachtal', 'skijuwel'],
    'auffach': ['alpbachtal', 'skijuwel'],
    'niederau': ['alpbachtal', 'skijuwel'],
    'ankogel-mallnitz': ['ankogel-mallnitz', 'ankogel'],
    'annaberg': ['annaberg-lungoetz', 'annaberg'],
    'axamer lizum': ['axamer-lizum'],
    'bad gastein': ['bad-gastein', 'badhofgastein', 'schlossalm-angertal-stubnerkogel'],
    'bad hofgastein': ['badhofgastein', 'bad-hofgastein'],
    'bad kleinkirchheim': ['bad-kleinkirchheim'],
    'brandnertal': ['brandnertal'],
    'brixen im thale': ['brixen-im-thale', 'soell', 'skiwelt'],
    'dachstein krippenstein': ['dachstein-krippenstein'],
    'dachstein west': ['dachstein-west', 'gosau-dachstein-west'],
    'damüls-mellau': ['damuels', 'damuels-mellau'],
    'diedamskopf': ['diedamskopf'],
    'dreiländereck': ['dreilaendereck'],
    'ellmau': ['ellmau', 'soell', 'skiwelt'],
    'ellmau - going': ['ellmau', 'soell', 'skiwelt'],
    'söll': ['soell', 'skiwelt'],
    'hopfgarten': ['hopfgarten', 'soell', 'skiwelt'],
    'westendorf': ['westendorf', 'soell', 'skiwelt'],
    'emberger alm': ['embergeralm'],
    'fageralm': ['fageralm', 'reiteralm'],
    'faschina-fontanella': ['faschina'],
    'fendels': ['fendels'],
    'feuerkogel': ['feuerkogel-ebensee', 'feuerkogel'],
    'fieberbrunn': ['fieberbrunn', 'saalbach-hinterglemm-leogang'],
    'filzmoos': ['filzmoos'],
    'flachau': ['flachau'],
    'fügen - spieljoch': ['fuegen-spieljoch'],
    'gaberl': ['gaberl'],
    'gaißau-hintersee': ['gaissau-hintersee'],
    'galsterberg': ['galsterberg'],
    'galtür': ['galtuer', 'ischgl-silvapark-galtuer'],
    'gargellen': ['gargellen-montafon', 'gargellen'],
    'gemeindealpe': ['gemeindealpe-mitterbach', 'gemeindealpe'],
    'gerlitzen': ['gerlitzen-alpe', 'gerlitzen'],
    'gerlos': ['gerlos'],
    'glun gezer': ['glungezer-tulfes', 'glungezer'],
    'goldeck': ['goldeck'],
    'golm': ['golm-montafon', 'golm'],
    'grebenzen': ['grebenzen'],
    'großeck-speiereck': ['grosseck-speiereck'],
    'grossglockner resort kals-matrei': ['kals-matrei-grossglockner', 'kals-matrei'],
    'heiligenblut': ['heiligenblut-grossglockner', 'heiligenblut'],
    'heutal - unken': ['heutal-unken', 'heutal'],
    'hinterglemm': ['saalbach-hinterglemm-leogang'],
    'saalbach': ['saalbach-hinterglemm-leogang'],
    'leogang': ['saalbach-hinterglemm-leogang'],
    'hinterstoder': ['hinterstoder-hoess', 'hinterstoder'],
    'hintertux glacier': ['hintertux'],
    'hochficht': ['hochficht-boehmerwald', 'hochficht'],
    'hochkar': ['hochkar-goestling', 'hochkar'],
    'hochkönig': ['hochkoenig-maria-alm-dienten-muehlbach', 'hochkoenig'],
    'hochkössen': ['koessen-unterberg', 'koessen'],
    'hochkrimml': ['krimml-hochkrimml', 'gerlos'],
    'königsleiten': ['koenigsleiten', 'gerlos'],
    'zell am ziller': ['zell-am-ziller', 'gerlos'],
    'hochoetz': ['hochoetz-oetztal', 'hochoetz'],
    'hochrindl': ['hochrindl'],
    'hochzeiger': ['hochzeiger'],
    'hochzillertal-hochfügen': ['hochfuegen', 'hochzillertal', 'ski-gletscherwelt-zillertal3000-tux'],
    'hohentauern': ['hohentauern'],
    'vallnord': ['pal-arinsal', 'vallnord-pal-arinsal'],
    'ordino arcalis': ['ordino-arcalis'],
    'grandvalira': ['grandvalira']
}

SHARED_SYNDICATE_KEYS = [
    'skiwelt', 'ski juwel', 'zillertal arena', 'skicircus', 'snow space salzburg',
    '3 vallées', 'portes du soleil', 'paradiski', '4 vallées', 'via lattea', 'grand massif'
]

# Caches populated once per country during batch execution
BERGFEX_COUNTRY_INDEX = {}     # country_lower -> list of (slug, link_text_norm)
SKIRESORT_COUNTRY_INDEX = {}   # country_lower -> list of (resort_url, name_norm, inline_price_val, inline_curr)


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
    """Transliterates German/European characters (ä->ae, ö->oe, ü->ue, ß->ss) into clean slugs."""
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
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s


def is_within_guardrail(val, prev_price, strict_non_negative=True):
    """Checks whether candidate price is within realistic 2-season bounds (-3% to +25%)."""
    if not (18.0 <= val <= 135.0):
        return False
    if val == 100.0 and prev_price < 82.0:
        return False
    if prev_price > 15.0 and strict_non_negative:
        pct_diff = (val - prev_price) / prev_price
        if pct_diff < -0.03 or pct_diff > 0.25:
            return False
    return True


def preindex_bergfex_country(country):
    """
    Forensic Fix #2: Downloads the Bergfex country directory page once per country
    and indexes all resort slugs (e.g. /hinterstoder-hoess/, /hochoetz-oetztal/, /damuels/).
    """
    c_key = country.lower().strip()
    if c_key in BERGFEX_COUNTRY_INDEX:
        return BERGFEX_COUNTRY_INDEX[c_key]

    BERGFEX_COUNTRY_INDEX[c_key] = []
    b_country = BERGFEX_COUNTRY_SLUGS.get(c_key)
    if not b_country:
        return []

    print(f"   [*] Pre-indexing Bergfex directory for {country} ({b_country})...")
    dir_urls = [
        f"https://www.bergfex.at/{b_country}/",
        f"https://www.bergfex.com/{b_country}/"
    ]
    seen_slugs = set()
    entries = []

    for d_url in dir_urls:
        try:
            resp = HTTP_SESSION.get(d_url, timeout=10, verify=False)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href'].strip()
                    m = re.match(r'^/([a-z0-9\-]{3,60})/?$', href)
                    if not m:
                        continue
                    slug = m.group(1)
                    if slug in seen_slugs or slug == b_country or any(bad in slug for bad in BLACKLIST_LINK_KEYWORDS):
                        continue
                    seen_slugs.add(slug)
                    link_text_norm = transliterate_slug(a_tag.get_text(' ', strip=True))
                    entries.append((slug, link_text_norm))
                if entries:
                    break
        except Exception:
            continue

    BERGFEX_COUNTRY_INDEX[c_key] = entries
    return entries


def preindex_skiresort_country(country):
    """
    Forensic Fix #3: Downloads the Skiresort.info country directory once per country,
    capturing both the exact long resort URL and any inline 1-day adult price shown on the card.
    """
    c_key = country.lower().strip()
    if c_key in SKIRESORT_COUNTRY_INDEX:
        return SKIRESORT_COUNTRY_INDEX[c_key]

    SKIRESORT_COUNTRY_INDEX[c_key] = []
    s_country = SKIRESORT_COUNTRY_SLUGS.get(c_key)
    if not s_country:
        return []

    print(f"   [*] Pre-indexing Skiresort.info directory for {country} ({s_country})...")
    d_url = f"https://www.skiresort.info/ski-resorts/{s_country}/"
    entries = []

    try:
        resp = HTTP_SESSION.get(d_url, timeout=10, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].strip()
                if '/ski-resort/' not in href:
                    continue
                full_url = urljoin(d_url, href)
                name_text = a_tag.get_text(' ', strip=True)
                if not name_text or len(name_text) < 3:
                    continue
                name_norm = transliterate_slug(name_text)

                # Check parent card container for inline ski pass price (e.g. "€ 72,-" or "€ 66.50")
                inline_val = 0.0
                inline_curr = '€'
                card = a_tag.find_parent(['div', 'tr', 'li'])
                if card:
                    card_text = card.get_text(' ', strip=True)
                    m_price = re.search(r'(€|EUR|CHF)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)', card_text)
                    if m_price:
                        inline_curr = 'CHF' if 'CHF' in m_price.group(1) else '€'
                        inline_val = float(m_price.group(2).replace(',', '.'))

                entries.append((full_url, name_norm, inline_val, inline_curr))
    except Exception:
        pass

    SKIRESORT_COUNTRY_INDEX[c_key] = entries
    return entries


def generate_bergfex_slugs(resort_name, country=''):
    """
    Generates prioritized candidate Bergfex slugs using:
      1. Explicit alias dictionary (including valley suffixes)
      2. Live Bergfex Country Directory fuzzy/token match
      3. German umlaut transliteration & compound name splitting
    """
    raw_lower = resort_name.lower().strip()
    candidates = []

    if raw_lower in BERGFEX_ALIAS_OVERRIDES:
        candidates.extend(BERGFEX_ALIAS_OVERRIDES[raw_lower])

    primary = transliterate_slug(resort_name)
    cleaned_words = re.sub(
        r'\b(glacier|gletscher|resort|ski|arena|area|bergbahnen|skigebiet)\b',
        '', raw_lower
    ).strip()
    cleaned_slug = transliterate_slug(cleaned_words)
    no_space = transliterate_slug(raw_lower.replace(' ', ''))

    search_tokens = [t for t in [primary, cleaned_slug, no_space] if t]
    if '-' in primary:
        search_tokens.extend([p for p in primary.split('-') if len(p) >= 4])

    # Match against live Bergfex Country Directory Index
    if country:
        dir_entries = preindex_bergfex_country(country)
        for slug, link_norm in dir_entries:
            for tok in search_tokens:
                if slug == tok or slug.startswith(tok + '-') or ('-' + tok) in slug or tok in link_norm:
                    if slug not in candidates:
                        candidates.append(slug)

    for tok in [primary, cleaned_slug, no_space]:
        if tok and tok not in candidates:
            candidates.append(tok)

    if ' - ' in raw_lower or '-' in raw_lower:
        parts = [transliterate_slug(p) for p in re.split(r'\s*-\s*', raw_lower) if len(p.strip()) >= 4]
        for p in parts:
            if p and p not in candidates:
                candidates.append(p)

    return candidates[:5]


def extract_adult_price_from_html_tables(soup, prev_price=0.0):
    """
    Forensic Fix #1: Parses HTML <table> and <dl> structures directly so that
    Youth/Child columns in the same row do not penalize the Adult 1-Day price!
    """
    price_regex = re.compile(
        r'(?:(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)|([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\s*(€|EUR|CHF|SFr\.?))',
        re.IGNORECASE
    )

    best_table_cand = None

    for tr in soup.find_all(['tr', 'dl', 'li']):
        row_text = tr.get_text(' ', strip=True)
        row_lower = row_text.lower()

        if len(row_text) > 350 or len(row_text) < 6:
            continue

        # Skip rows that are clearly multi-day, half-day, hourly, or child-only rows
        if any(neg in row_lower for neg in NON_DAY_NEGATIVE_KEYWORDS):
            continue

        has_day_kw = any(k in row_lower for k in DAY_KEYWORDS)
        has_adult_kw = any(k in row_lower for k in ADULT_KEYWORDS)

        if not (has_day_kw or has_adult_kw):
            continue

        # If row is specifically labeled as a Child/Youth/Senior row (and not Adult), skip it
        if not has_adult_kw and any(ck in row_lower for ck in CHILD_YOUTH_KEYWORDS):
            continue

        row_prices = []
        for m in price_regex.finditer(row_text):
            curr_raw = m.group(1) or m.group(4) or '€'
            val_raw = m.group(2) or m.group(3)
            try:
                val = float(val_raw.replace(',', '.'))
            except ValueError:
                continue

            # Check immediate 30-char prefix before this specific price so we don't grab "Kind €41"
            prefix = row_text[max(0, m.start() - 30):m.start()].lower()
            if any(ck in prefix for ck in CHILD_YOUTH_KEYWORDS) and not any(ak in prefix for ak in ADULT_KEYWORDS):
                continue
            if any(tk in prefix for tk in TEASER_PREFIX_KEYWORDS):
                continue

            if is_within_guardrail(val, prev_price, strict_non_negative=True):
                curr_sym = 'CHF' if 'chf' in curr_raw.lower() or 'sfr' in curr_raw.lower() else '€'
                score = 12
                if has_day_kw and has_adult_kw:
                    score += 6
                if prev_price > 15.0:
                    pct_diff = (val - prev_price) / prev_price
                    if 0.0 <= pct_diff <= 0.20:
                        score += 8
                row_prices.append((score, val, curr_sym, row_text[:140]))

        if row_prices:
            # In a 1-Day row [Adult, Youth, Child], Adult is the highest valid 1-day price in the row
            row_prices.sort(key=lambda x: (x[0], x[1]), reverse=True)
            top = row_prices[0]
            if best_table_cand is None or (top[0], top[1]) > (best_table_cand[2], best_table_cand[0]):
                best_table_cand = (top[1], top[2], top[0], top[3])

    return best_table_cand


def score_price_candidates(text, prev_price=0.0, strict_non_negative=True):
    """
    Forensic Fix #1: Directional context scoring.
    Prevents subsequent "| Jugend €65 | Kinder €41" labels from penalizing a preceding
    "Erwachsene €82" match, and widens the 2-season inflation bonus to 0%..+20%.
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

            if not is_within_guardrail(val, prev_price, strict_non_negative):
                continue

            # Broader context window for positive topic signals (1 Tag, Tageskarte, Hauptsaison)
            start_pos = max(0, match.start() - 100)
            end_pos = min(len(normalized_text), match.end() + 80)
            window = normalized_text[start_pos:end_pos]
            window_lower = window.lower()

            # Immediate preceding window (45 chars before price) for binding age/teaser labels
            immediate_prefix = normalized_text[max(0, match.start() - 45):match.end()].lower()
            immediate_suffix = normalized_text[match.start():min(len(normalized_text), match.end() + 20)].lower()

            score = 0

            for kw in POSITIVE_KEYWORDS:
                if kw in window_lower:
                    score += 4

            # Extra boost if "Erwachsene" / "Adult" is immediately before this number
            if any(ak in immediate_prefix for ak in ADULT_KEYWORDS):
                score += 6

            # Only penalize for Child/Youth/Senior if it appears in the immediate prefix (not after a slash for the next price)
            if any(ck in immediate_prefix for ck in CHILD_YOUTH_KEYWORDS) and not any(ak in immediate_prefix for ak in ADULT_KEYWORDS):
                score -= 10
            elif any(ck in immediate_suffix for ck in CHILD_YOUTH_KEYWORDS):
                score -= 6

            for kw in NON_DAY_NEGATIVE_KEYWORDS:
                if kw in immediate_prefix:
                    score -= 8
                elif kw in window_lower:
                    score -= 4

            for teaser in TEASER_PREFIX_KEYWORDS:
                if teaser in immediate_prefix:
                    score -= 7

            if any(yr in window_lower for yr in ['2026', '2027', '26/27', '2026/27', '25/26']):
                score += 3
            if any(old_yr in window_lower for old_yr in ['2021', '2022', '2023', '22/23']):
                score -= 5

            # 2-Season Baseline Proximity Scoring (0% to +20% ideal window)
            if prev_price > 15.0:
                pct_diff = (val - prev_price) / prev_price
                if 0.0 <= pct_diff <= 0.20:
                    score += 10
                elif 0.20 < pct_diff <= 0.25:
                    score += 4
                elif -0.03 <= pct_diff < 0.0:
                    score += 2
                else:
                    score -= 12

            curr_symbol = 'CHF' if 'chf' in raw_curr.lower() or 'sfr' in raw_curr.lower() else '€'
            snippet_clean = window.strip()[:140]
            candidates.append((score, val, curr_symbol, snippet_clean))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_val, best_curr, best_snippet = candidates[0]

    if best_score < 2:
        return None

    return best_val, best_curr, best_score, best_snippet


def parse_html_for_best_price(html_content, prev_price=0.0):
    """Runs structured HTML table extraction first, then falls back to directional text scoring."""
    soup = BeautifulSoup(html_content, 'html.parser')

    table_cand = extract_adult_price_from_html_tables(soup, prev_price)
    if table_cand:
        return table_cand

    full_text = soup.get_text(' ', strip=True)
    return score_price_candidates(full_text, prev_price, strict_non_negative=True)


def fetch_from_bergfex(resort_name, country, prev_price=0.0):
    """
    Tests prioritized Bergfex slugs (from Alias Map + Country Directory Index)
    across /preise/ and overview tabs using a persistent HTTP session.
    """
    slugs = generate_bergfex_slugs(resort_name, country)
    for slug in slugs:
        urls_to_try = [
            f"https://www.bergfex.at/{slug}/preise/",
            f"https://www.bergfex.com/{slug}/preise/",
            f"https://www.bergfex.com/{slug}/"
        ]
        for b_url in urls_to_try:
            try:
                resp = HTTP_SESSION.get(b_url, timeout=8, verify=False, allow_redirects=True)
                if resp.status_code == 200 and 'bergfex' in resp.url:
                    if '/sommer/' in resp.url.lower():
                        continue
                    cand = parse_html_for_best_price(resp.text, prev_price)
                    if cand:
                        stage_label = "Stage 1B (Bergfex /preise/)" if '/preise' in b_url else "Stage 1C (Bergfex Direct)"
                        return cand, stage_label, resp.url
            except Exception:
                continue
    return None, None, None


def fetch_from_skiresort_info(resort_name, country, prev_price=0.0):
    """
    Forensic Fix #3: Uses the Skiresort.info Country Directory Index first (matching
    long URLs like Grandvalira, Ordino Arcalis, Pal Arinsal / Vallnord), then falls back to direct slugs.
    """
    raw_lower = resort_name.lower().strip()
    primary_tok = transliterate_slug(resort_name)
    tokens = [primary_tok]
    if raw_lower in BERGFEX_ALIAS_OVERRIDES:
        tokens.extend(BERGFEX_ALIAS_OVERRIDES[raw_lower])
    if '-' in primary_tok:
        tokens.extend([p for p in primary_tok.split('-') if len(p) >= 4])

    dir_entries = preindex_skiresort_country(country)
    matched_urls = []

    for full_url, name_norm, inline_val, inline_curr in dir_entries:
        if any(tok and (tok in name_norm or tok in full_url.lower()) for tok in tokens):
            if inline_val > 0 and is_within_guardrail(inline_val, prev_price, strict_non_negative=True):
                snippet = f"Skiresort.info {country} directory listing: {inline_curr}{inline_val:.2f}"
                return (inline_val, inline_curr, 15, snippet), "Stage 1A (Skiresort.info Directory)", full_url
            if full_url not in matched_urls:
                matched_urls.append(full_url)

    for s_url in matched_urls[:2]:
        try:
            resp = HTTP_SESSION.get(s_url, timeout=8, verify=False, allow_redirects=True)
            if resp.status_code == 200 and '/ski-resort/' in resp.url:
                cand = parse_html_for_best_price(resp.text, prev_price)
                if cand:
                    return cand, "Stage 1A (Skiresort.info Resort Page)", resp.url
        except Exception:
            continue

    # Direct slug fallback
    for slug in tokens[:2]:
        s_url = f"https://www.skiresort.info/ski-resort/{slug}/"
        if s_url in matched_urls:
            continue
        try:
            resp = HTTP_SESSION.get(s_url, timeout=8, verify=False, allow_redirects=True)
            if resp.status_code == 200 and '/ski-resort/' in resp.url:
                cand = parse_html_for_best_price(resp.text, prev_price)
                if cand:
                    return cand, "Stage 1A (Skiresort.info Direct)", resp.url
        except Exception:
            continue

    return None, None, None


def rank_internal_pricing_links(base_url, html_content):
    """Finds and ranks winter pricing links while blacklisting summer & season-card URLs."""
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

        if base_domain not in link_domain and not any(t in link_domain for t in ['skidata', 'axess', 'feratel', 'starjack', 'skisport']):
            continue

        combined_text = (parsed.path + ' ' + a_tag.get_text(' ', strip=True)).lower()

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
    """Downloads a pricing PDF into memory and runs directional candidate scoring."""
    if not PDF_AVAILABLE or any(bad in pdf_url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
        return None
    try:
        resp = HTTP_SESSION.get(pdf_url, timeout=TIMEOUT_SECONDS, verify=False)
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
    """Uses Headless Chromium to dismiss cookie banners, render JS tables, and extract prices."""
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

            candidate = parse_html_for_best_price(rendered_html, prev_price)
            if not candidate:
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
    Executes the v7 4-Stage Deep Discovery Pipeline:
      Stage 0: Shared Ski Area Syndicate & Domain Cache
      Stage 1: Bergfex (Country Directory + Valley-Suffix Slugs + Table Parser) & Skiresort.info Directory
      Stage 2: Official Site Winter Spider (Table Parser + Directional Scorer) + PDF
      Stage 3: Headless Chromium JS Rendering (Playwright)
    """
    # STAGE 0: Shared Ski Area / Domain Cache
    cache_key = get_syndicate_cache_key(region_str, website_url)
    if cache_key and cache_key in shared_cache:
        cached_price, cached_source, cached_snippet = shared_cache[cache_key]
        cached_val = clean_numeric_price(cached_price)
        if is_within_guardrail(cached_val, prev_price, strict_non_negative=True):
            return cached_price, "Stage 0 (Shared Ski Area Domain Cache)", cached_source, cached_snippet

    # STAGE 1B/1C: Bergfex Lookup (with Country Directory Pre-Indexing & Table Parser)
    cand, stage_label, b_url = fetch_from_bergfex(resort_name, country, prev_price)
    if cand:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, b_url, snippet)
        return price_str, stage_label, b_url, snippet

    # STAGE 1A: Skiresort.info Country Directory & Resort Lookup
    cand, stage_label, s_url = fetch_from_skiresort_info(resort_name, country, prev_price)
    if cand:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, s_url, snippet)
        return price_str, stage_label, s_url, snippet

    # STAGE 2: Official Website Spider
    if not website_url or website_url.strip().lower() in ['n/a', 'closed', '']:
        return None, "No URL", "SKIPPED", ""

    target_url = website_url.strip()
    if not target_url.startswith(('http://', 'https://')):
        target_url = 'https://' + target_url

    html_subpages = []
    pdf_links = []

    try:
        resp = HTTP_SESSION.get(target_url, timeout=TIMEOUT_SECONDS, verify=False)
        if resp.status_code == 200:
            html_subpages, pdf_links = rank_internal_pricing_links(target_url, resp.text)
    except Exception:
        pass

    for sub_url in html_subpages:
        if any(bad in sub_url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
            continue
        try:
            sub_resp = HTTP_SESSION.get(sub_url, timeout=TIMEOUT_SECONDS, verify=False)
            if sub_resp.status_code == 200 and not any(bad in sub_resp.url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
                cand = parse_html_for_best_price(sub_resp.text, prev_price)
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

    # STAGE 3: Headless Chromium JS Rendering on best winter pricing subpage or homepage
    js_target = html_subpages[0] if html_subpages else target_url
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
            "## 🎿 v7 Deep-Discovery Lift Pass Scraper — Batch Audit Summary",
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
    parser = argparse.ArgumentParser(description="v7 Deep Discovery Lift Pass Scraper")
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
    print(" ENHANCED DEEP-DISCOVERY SKI LIFT PASS SCRAPER (v7 - Directory Index + Table Parser)")
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
        print(f"{r_name[:28]:<28} | {old_p:<8} | {old_p if not new_p else new_p:<8} | {var_s:<7} | {stat:<15} | {stg}")
    print("-" * 105)


if __name__ == '__main__':
    main()
