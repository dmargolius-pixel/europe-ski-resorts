import csv
import re
import os
import time
import ssl
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
import argparse
from html.parser import HTMLParser

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

# Sanity Guardrails: Extracted price must be within [0.80x, 1.40x] of previous known price
MIN_RATIO = 0.80
MAX_RATIO = 1.40
SEASON_INFLATION_RATE = 0.035

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-GB,en;q=0.9,de;q=0.8,fr;q=0.7,it;q=0.6,es;q=0.5',
    'Cache-Control': 'no-cache'
}

COUNTRY_TO_SKIRESORT_SLUG = {
    'austria': 'austria',
    'france': 'france',
    'switzerland': 'switzerland',
    'italy': 'italy',
    'germany': 'germany',
    'spain': 'spain',
    'andorra': 'andorra',
    'bulgaria': 'bulgaria',
    'slovenia': 'slovenia',
    'norway': 'norway',
    'sweden': 'sweden',
    'finland': 'finland',
    'scotland': 'united-kingdom',
    'uk': 'united-kingdom',
    'united kingdom': 'united-kingdom',
    'poland': 'poland',
    'czech republic': 'czech-republic',
    'slovakia': 'slovakia'
}

COUNTRY_TO_BERGFEX_SLUG = {
    'austria': 'oesterreich',
    'france': 'frankreich',
    'switzerland': 'schweiz',
    'italy': 'italien',
    'germany': 'deutschland',
    'slovenia': 'slowenien',
    'spain': 'spanien',
    'andorra': 'andorra',
    'poland': 'polen',
    'czech republic': 'tschechien',
    'slovakia': 'slowakei'
}

LINK_KEYWORDS_HIGH = [
    'skipass', 'ski-pass', 'tageskarte', 'tarif', 'preise', 'prices',
    'forfait', 'prezzi', 'listino', 'skiticket', 'liftpass', 'lift-pass',
    'day-pass', 'tageskarten', 'tarifas', 'ticketpreise', 'winterpreise'
]
LINK_KEYWORDS_MEDIUM = [
    'ticket', 'winter', 'bergbahn', 'remontees', 'impianti', 'rates', 'shop', 'tickets'
]

CONTEXT_POSITIVE_KEYWORDS = [
    '1 tag', '1-tag', 'tageskarte', 'erwachsene', '1 day', 'day pass', 'adult',
    '1 jour', 'journée', 'journee', 'adulte', '1 giorno', 'giornaliero', 'adulto',
    '1 día', '1 dia', 'forfait', 'skipass', 'hauptsaison', 'high season', 'peak',
    'day ticket', 'adults', 'main season'
]

CONTEXT_NEGATIVE_IMMEDIATE = [
    '6 tage', '6 days', '6 jours', '6 giorni', 'saison', 'season pass',
    'kinder', 'child', 'children', 'enfant', 'bambini', 'junior', 'senior',
    'halbtageskarte', 'half day', 'demi-journée', '4 stunden', '4 hours',
    '2 tage', '3 tage', '4 tage', '5 tage', '7 tage', 'youth', 'jugend',
    'kleinkind', '10 days', '2 days', '3 days'
]

STOPWORDS = {
    'ski', 'resort', 'area', 'glacier', 'gletscher', 'am', 'im', 'in', 'der',
    'die', 'das', 'du', 'de', 'la', 'le', 'les', 'des', 'sur', 'val', 'st',
    'saint', 'sankt', 'bad', 'arena', 'tirol', 'tyrol', 'salzburgerland',
    'salzburg', 'vorarlberg', 'carinthia', 'styria', 'savoie', 'haute',
    'valais', 'graubuenden', 'grisons', 'bernese', 'oberland', 'dolomites',
    'trentino', 'alto', 'adige', 'aosta', 'piedmont', 'lombardy', 'pyrenees',
    'bahnen', 'bergbahnen', 'skigebiet', 'snow', 'space'
}


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


def strip_invisible_chars(text):
    """Removes zero-width spaces (\u200b) and soft hyphens used by Skiresort.info."""
    if not text:
        return ""
    return re.sub(r'[\u200b\u200c\u200d\u00ad\ufeff]', '', str(text))


def strip_promotional_footer(html_or_text):
    """
    Removes the 'Further interesting ski resorts' / 'Weitere interessante Skigebiete'
    promotional widget at the bottom of Skiresort.info pages so ad prices (like € 74,- for Sillian)
    are never mistaken for the target resort's price.
    """
    if not html_or_text:
        return ""
    markers = [
        'Further interesting ski resorts',
        'Weitere interessante Skigebiete',
        'Autres domaines skiables intéressants',
        'Altri comprensori sciistici interessanti',
        'Ski resorts nearby'
    ]
    cut_idx = len(html_or_text)
    for marker in markers:
        pos = html_or_text.find(marker)
        if pos != -1 and pos < cut_idx:
            cut_idx = pos
    return html_or_text[:cut_idx]


def transliterate_text(text, german_umlauts=True):
    """
    Converts German umlauts (ä->ae, ö->oe, ü->ue, ß->ss) and accented characters
    (é->e, à->a, etc.) into clean ASCII for URL slugs and fuzzy matching.
    """
    s = strip_invisible_chars(text).lower()
    if german_umlauts:
        s = s.replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    else:
        s = s.replace('ß', 'ss')
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return s


def make_slugs(name):
    """Returns candidate URL slugs (both German 'oe/ue/ae' style and plain ASCII style)."""
    clean = re.sub(r'\(.*?\)', '', strip_invisible_chars(name)).strip()
    slug_de = re.sub(r'[^a-z0-9]+', '-', transliterate_text(clean, german_umlauts=True)).strip('-')
    slug_ascii = re.sub(r'[^a-z0-9]+', '-', transliterate_text(clean, german_umlauts=False)).strip('-')
    slugs = []
    for s in [slug_de, slug_ascii]:
        if s and s not in slugs:
            slugs.append(s)
    parts = re.split(r'[/\-–]', clean)
    if len(parts) > 1:
        first_part = parts[0].strip()
        if len(first_part) >= 4:
            sub_de = re.sub(r'[^a-z0-9]+', '-', transliterate_text(first_part, german_umlauts=True)).strip('-')
            if sub_de and sub_de not in slugs:
                slugs.append(sub_de)
    return slugs


def extract_significant_tokens(text):
    """Extracts meaningful location tokens from a resort or region name for matching."""
    norm = transliterate_text(text, german_umlauts=True)
    raw_tokens = re.findall(r'[a-z0-9]{3,}', norm)
    return [t for t in raw_tokens if t not in STOPWORDS]


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
            cleaned = strip_invisible_chars(data).strip()
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
                return strip_invisible_chars(resp.text), resp.url
        except Exception:
            return None, url
    else:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as response:
                if response.status == 200:
                    raw = response.read()
                    return strip_invisible_chars(raw.decode('utf-8', errors='replace')), response.geturl()
        except Exception:
            return None, url
    return None, url


def clean_numeric_price(price_str):
    """Converts formatted strings like '€68.50', '€ 83,-', or 'CHF 75,00' to float."""
    if not price_str or str(price_str).strip().lower() in ['n/a', 'closed', 'none', 'dynamic', '']:
        return 0.0
    normalized = strip_invisible_chars(price_str).replace(',-', '.00').replace('.-', '.00').replace(',', '.')
    match = re.search(r'([0-9]+(?:\.[0-9]+)?)', normalized)
    return float(match.group(1)) if match else 0.0


def validate_price_bounds(val, prev_price_val):
    """Validates candidate price against previous year's baseline or realistic bounds."""
    if val <= 0:
        return False
    if prev_price_val > 0:
        return (prev_price_val * MIN_RATIO) <= val <= (prev_price_val * MAX_RATIO)
    return 25.0 <= val <= 150.0


def parse_currency_price_from_snippet(snippet, prev_price_val=0.0, default_currency='€'):
    """
    Extracts a currency price from a short card or table cell snippet, handling
    '€ 83,-', '€ 81.50', 'Price: € 82,00', and 'SFr. 94,- / approx. € 101,-'.
    """
    if not snippet:
        return None
    clean_snip = strip_invisible_chars(snippet)

    if default_currency == 'CHF':
        chf_match = re.search(
            r'(?:CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*(?:,-|\.\-|-))?',
            clean_snip, re.IGNORECASE
        )
        if not chf_match:
            chf_match = re.search(
                r'([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\s*(?:,-|\.\-|-)?\s*(?:CHF|SFr\.?)',
                clean_snip, re.IGNORECASE
            )
        if chf_match:
            val = clean_numeric_price(chf_match.group(1))
            if validate_price_bounds(val, prev_price_val):
                return f"CHF{val:.2f}"

    pattern = re.compile(
        r'(?:(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*(?:,-|\.\-|-))?|'
        r'([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\s*(?:,-|\.\-|-)?\s*(€|EUR|CHF|SFr\.?))',
        re.IGNORECASE
    )
    for m in pattern.finditer(clean_snip):
        c1, v1, v2, c2 = m.groups()
        raw_curr = (c1 or c2 or '').upper()
        val = clean_numeric_price(v1 or v2)
        if validate_price_bounds(val, prev_price_val):
            symbol = 'CHF' if ('CHF' in raw_curr or 'SFR' in raw_curr or default_currency == 'CHF') else '€'
            return f"{symbol}{val:.2f}"
    return None


class MultiAggregatorDirectoryIndex:
    """
    Stage 0 Pre-Indexer (v4):
      1. Crawls Skiresort.info's Slope-Length & Country Directories (/sorted/slope-length/ and default),
         properly ignoring bare intro-paragraph links so real resort cards are never skipped.
      2. Crawls Bergfex's Country Overview & Snow-Report Directories (/oesterreich/, /frankreich/,
         /schweiz/, /italien/, etc.), which list individual satellite villages with their exact
         1-day pass price and resort slug.
    """
    def __init__(self):
        self.indexed_countries = set()
        self.europe_top_indexed = False
        self.sr_entries = {}  # keyed by full_url -> dict
        self.bf_entries = {}  # keyed by full_url -> dict

    def ensure_country_indexed(self, country_name):
        c_key = (country_name or '').strip().lower()

        if not self.europe_top_indexed:
            self.europe_top_indexed = True
            print("   [*] Pre-indexing Skiresort.info Europe Top Resorts (/europe/sorted/slope-length/)...")
            for page_num in range(1, 5):
                url = (
                    "https://www.skiresort.info/ski-resorts/europe/sorted/slope-length/"
                    if page_num == 1
                    else f"https://www.skiresort.info/ski-resorts/europe/sorted/slope-length/page/{page_num}/"
                )
                html, _ = fetch_html(url, timeout=12)
                if not html or self._parse_skiresort_directory_page(html) == 0:
                    break

        if c_key in self.indexed_countries:
            return
        self.indexed_countries.add(c_key)

        sr_slug = COUNTRY_TO_SKIRESORT_SLUG.get(c_key)
        if sr_slug:
            print(f"   [*] Pre-indexing Skiresort.info directory for {country_name.title()} ({sr_slug})...")
            # Crawl both slope-length sorted (puts all major resorts first) and default listing
            url_templates = [
                f"https://www.skiresort.info/ski-resorts/{sr_slug}/sorted/slope-length/",
                f"https://www.skiresort.info/ski-resorts/{sr_slug}/sorted/slope-length/page/2/",
                f"https://www.skiresort.info/ski-resorts/{sr_slug}/sorted/slope-length/page/3/",
                f"https://www.skiresort.info/ski-resorts/{sr_slug}/",
                f"https://www.skiresort.info/ski-resorts/{sr_slug}/page/2/",
                f"https://www.skiresort.info/ski-resorts/{sr_slug}/page/3/"
            ]
            for url in url_templates:
                html, _ = fetch_html(url, timeout=12)
                if html:
                    self._parse_skiresort_directory_page(html)

        bf_slug = COUNTRY_TO_BERGFEX_SLUG.get(c_key)
        if bf_slug:
            print(f"   [*] Pre-indexing Bergfex directory for {country_name.title()} ({bf_slug})...")
            # 1. Main country overview page (contains 80 resorts with 'Price: € XX,XX')
            bf_main_url = f"https://www.bergfex.com/{bf_slug}/"
            bf_html, _ = fetch_html(bf_main_url, timeout=12)
            if bf_html:
                self._parse_bergfex_country_page(bf_html)
            # 2. Country snow-report index (contains ALL resorts & villages on a single page with exact slugs)
            bf_snow_url = f"https://www.bergfex.com/{bf_slug}/schneewerte/"
            bf_snow_html, _ = fetch_html(bf_snow_url, timeout=12)
            if bf_snow_html:
                self._parse_bergfex_snow_index(bf_snow_html)

    def _parse_skiresort_directory_page(self, html_content):
        if not HAS_BS4:
            return 0

        soup = BeautifulSoup(html_content, 'html.parser')
        added_or_updated = 0

        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            if not re.search(r'/ski-resort/[^/]+/?$', href):
                continue

            anchor_text = strip_invisible_chars(a.get_text(separator=' ')).strip()
            if not anchor_text or anchor_text.lower() in [
                'details', 'ski resort status', 'snow report', 'trail map', 'test result'
            ]:
                continue

            clean_title = re.sub(r'^\d+\.\s*', '', anchor_text).strip()
            if len(clean_title) < 3:
                continue

            full_url = urllib.parse.urljoin("https://www.skiresort.info", href)
            if not full_url.endswith('/'):
                full_url += '/'

            # Walk up the DOM to find the enclosing resort card
            card_node = a
            card_text = ""
            for _ in range(6):
                parent = card_node.parent
                if not parent:
                    break
                p_text = strip_invisible_chars(parent.get_text(separator=' ')).strip()
                if p_text.count('Details') > 2 or len(p_text) > 1500:
                    break
                card_node = parent
                card_text = p_text
                if ('ski lifts' in p_text.lower() or 'test result' in p_text.lower()) and (
                    '€' in p_text or 'SFr' in p_text or 'Details' in p_text
                ):
                    break

            # Critical v4 fix: Ignore inline links inside introductory paragraphs that aren't resort cards
            is_valid_card = ('ski lifts' in card_text.lower() or 'test result' in card_text.lower() or 'details' in card_text.lower())
            has_price_symbol = ('€' in card_text or 'SFr' in card_text or 'CHF' in card_text)

            existing = self.sr_entries.get(full_url)
            if existing:
                # Only overwrite existing entry if the new card has a price and the old one didn't
                if has_price_symbol and not existing.get('has_price'):
                    self.sr_entries[full_url] = {
                        'title': clean_title,
                        'norm_title': transliterate_text(clean_title, german_umlauts=True),
                        'norm_ascii': transliterate_text(clean_title, german_umlauts=False),
                        'norm_context': transliterate_text(card_text, german_umlauts=True),
                        'url': full_url,
                        'raw_card_text': card_text,
                        'has_price': True,
                        'source': 'Skiresort.info'
                    }
                    added_or_updated += 1
                continue

            if not is_valid_card and not has_price_symbol:
                continue

            self.sr_entries[full_url] = {
                'title': clean_title,
                'norm_title': transliterate_text(clean_title, german_umlauts=True),
                'norm_ascii': transliterate_text(clean_title, german_umlauts=False),
                'norm_context': transliterate_text(card_text, german_umlauts=True),
                'url': full_url,
                'raw_card_text': card_text,
                'has_price': has_price_symbol,
                'source': 'Skiresort.info'
            }
            added_or_updated += 1

        return added_or_updated

    def _parse_bergfex_country_page(self, html_content):
        if not HAS_BS4:
            return 0

        soup = BeautifulSoup(html_content, 'html.parser')
        count = 0
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            # Bergfex resort links look like "/soelden/" or "/kitzsteinhorn-kaprun/"
            if not re.match(r'^/[a-z0-9\-]+/?$', href):
                continue
            if any(x in href for x in [
                '/oesterreich', '/schweiz', '/frankreich', '/italien', '/deutschland',
                '/slowenien', '/spanien', '/andorra', '/polen', '/tschechien', '/slowakei',
                '/sommer', '/winter', '/wetter', '/webcams', '/schneewerte', '/pauschalen'
            ]):
                continue

            title = strip_invisible_chars(a.get_text(separator=' ')).strip()
            if len(title) < 3:
                continue

            full_url = urllib.parse.urljoin("https://www.bergfex.com", href)
            if not full_url.endswith('/'):
                full_url += '/'

            # Find enclosing row/card text (which contains "Price: € 82,00")
            card_node = a
            card_text = ""
            for _ in range(4):
                parent = card_node.parent
                if not parent:
                    break
                p_text = strip_invisible_chars(parent.get_text(separator=' ')).strip()
                if len(p_text) > 800:
                    break
                card_node = parent
                card_text = p_text
                if 'price:' in p_text.lower() or 'preis:' in p_text.lower() or '€' in p_text or 'chf' in p_text.lower():
                    break

            has_price = ('€' in card_text or 'CHF' in card_text)
            existing = self.bf_entries.get(full_url)
            if not existing or (has_price and not existing.get('has_price')):
                self.bf_entries[full_url] = {
                    'title': title,
                    'norm_title': transliterate_text(title, german_umlauts=True),
                    'norm_ascii': transliterate_text(title, german_umlauts=False),
                    'norm_context': transliterate_text(card_text, german_umlauts=True),
                    'url': full_url,
                    'raw_card_text': card_text,
                    'has_price': has_price,
                    'source': 'Bergfex'
                }
                count += 1
        return count

    def _parse_bergfex_snow_index(self, html_content):
        if not HAS_BS4:
            return 0

        soup = BeautifulSoup(html_content, 'html.parser')
        count = 0
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            m = re.match(r'^/([a-z0-9\-]+)/schneewerte/?$', href)
            if not m:
                continue
            slug = m.group(1)
            title = strip_invisible_chars(a.get_text(separator=' ')).strip()
            if len(title) < 3:
                continue
            full_url = f"https://www.bergfex.com/{slug}/"
            if full_url not in self.bf_entries:
                self.bf_entries[full_url] = {
                    'title': title,
                    'norm_title': transliterate_text(title, german_umlauts=True),
                    'norm_ascii': transliterate_text(title, german_umlauts=False),
                    'norm_context': transliterate_text(title, german_umlauts=True),
                    'url': full_url,
                    'raw_card_text': '',
                    'has_price': False,
                    'source': 'Bergfex'
                }
                count += 1
        return count

    def _score_entries(self, entries_dict, resort_name, region_name):
        clean_resort = re.sub(r'\(.*?\)', '', strip_invisible_chars(resort_name)).strip()
        norm_de = transliterate_text(clean_resort, german_umlauts=True)
        norm_ascii = transliterate_text(clean_resort, german_umlauts=False)

        resort_tokens = extract_significant_tokens(clean_resort)
        region_tokens = extract_significant_tokens(region_name)

        scored = []
        for entry in entries_dict.values():
            score = 0
            title_de = entry['norm_title']
            title_ascii = entry['norm_ascii']
            ctx_de = entry['norm_context']

            if norm_de == title_de or norm_ascii == title_ascii:
                score += 110
            elif norm_de and len(norm_de) >= 4 and (
                re.search(r'\b' + re.escape(norm_de) + r'\b', title_de) or
                re.search(r'\b' + re.escape(norm_ascii) + r'\b', title_ascii)
            ):
                score += 75
            elif norm_de and len(norm_de) >= 5 and (norm_de in title_de or norm_ascii in title_ascii):
                score += 55

            matched_resort_tokens = 0
            for tok in resort_tokens:
                if re.search(r'\b' + re.escape(tok) + r'\b', title_de) or re.search(r'\b' + re.escape(tok) + r'\b', title_ascii):
                    score += 32
                    matched_resort_tokens += 1
                elif len(tok) >= 5 and (tok in title_de or tok in title_ascii):
                    score += 20
                    matched_resort_tokens += 1
                elif re.search(r'\b' + re.escape(tok) + r'\b', ctx_de):
                    score += 12

            if resort_tokens and matched_resort_tokens == 0 and score < 30:
                continue

            for r_tok in region_tokens:
                if r_tok in title_de or r_tok in title_ascii:
                    score += 22
                elif r_tok in ctx_de:
                    score += 10

            if entry.get('has_price'):
                score += 5

            if score >= 30:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:3]]

    def find_candidates(self, resort_name, region_name, country_name):
        self.ensure_country_indexed(country_name)
        sr_matches = self._score_entries(self.sr_entries, resort_name, region_name)
        bf_matches = self._score_entries(self.bf_entries, resort_name, region_name)
        return sr_matches, bf_matches


def extract_structured_table_price(html_content, prev_price_val=0.0, default_currency='€'):
    """
    Targets structured HTML pricing tables and summary boxes on Skiresort.info, Bergfex,
    and official resort websites. Automatically strips 'Further interesting ski resorts' ads.
    """
    if not html_content:
        return None

    clean_html = strip_promotional_footer(strip_invisible_chars(html_content))

    # 1. Check Bergfex overview page summary widget: "Prices € 75,50 (day ticket)" / "Preise € 75,50 (Tageskarte)"
    bf_summary = re.search(
        r'(?:Prices|Preise|Price|Preis)\s*[:\s]*((?:€|EUR|CHF|SFr\.?)\s*[0-9]{2,3}(?:[\.,][0-9]{1,2})?(?:\s*(?:,-|\.\-|-))?)\s*\((?:day ticket|Tageskarte)\)',
        re.sub(r'<[^>]+>', ' ', clean_html),
        re.IGNORECASE
    )
    if bf_summary:
        price = parse_currency_price_from_snippet(bf_summary.group(1), prev_price_val, default_currency)
        if price:
            return price

    # 2. Check Skiresort.info plain-text sequence: "Adults Youth Children € 81.50"
    plain_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', clean_html))
    sr_pattern = re.search(
        r'(?:Adults|Erwachsene)(?:\s+(?:Youth|Jugend(?:liche)?))?(?:\s+(?:Children|Kinder))?(?:\s+(?:Seniors|Senioren))?\s*'
        r'((?:€|EUR|CHF|SFr\.?)\s*[0-9]{2,3}(?:[\.,][0-9]{1,2})?(?:\s*(?:,-|\.\-|-))?)',
        plain_text, re.IGNORECASE
    )
    if sr_pattern:
        price = parse_currency_price_from_snippet(sr_pattern.group(1), prev_price_val, default_currency)
        if price:
            return price

    if not HAS_BS4:
        return None

    soup = BeautifulSoup(clean_html, 'html.parser')

    # 3. Check Skiresort.info exact Adult Day Pass cell (id="selAd")
    sel_ad = soup.find(id='selAd')
    if sel_ad:
        cell_text = sel_ad.get_text(separator=' ').strip()
        price = parse_currency_price_from_snippet(cell_text, prev_price_val, default_currency)
        if price:
            return price

    # 4. Inspect HTML Tables (Row-oriented & Column-oriented)
    adult_headers = ['erwachsene', 'adults', 'adult', 'adulte', 'adulti', 'adultos']
    day_row_keywords = [
        '1 tag', '1-tag', 'tageskarte', '1 day', 'day ticket', 'day pass',
        '1 jour', 'journée', '1 giorno', 'giornaliero', 'main season', 'hauptsaison'
    ]
    neg_row_only = ['6 tage', '6 days', 'saison', 'season pass', 'halbtageskarte', 'half day']

    for table in soup.find_all('table'):
        table_text = strip_invisible_chars(table.get_text(separator=' ')).strip()
        # Check if this table has "Adults ... € XX" sequence
        tbl_sr = re.search(
            r'(?:Adults|Erwachsene)(?:\s+(?:Youth|Jugend(?:liche)?))?(?:\s+(?:Children|Kinder))?\s*'
            r'((?:€|EUR|CHF|SFr\.?)\s*[0-9]{2,3}(?:[\.,][0-9]{1,2})?(?:\s*(?:,-|\.\-|-))?)',
            table_text, re.IGNORECASE
        )
        if tbl_sr:
            price = parse_currency_price_from_snippet(tbl_sr.group(1), prev_price_val, default_currency)
            if price:
                return price

        rows = table.find_all('tr')
        for tr in rows:
            row_text = tr.get_text(separator=' ').strip()
            row_lower = row_text.lower()
            if any(nk in row_lower for nk in neg_row_only):
                continue
            if any(ah in row_lower for ah in adult_headers) or any(dk in row_lower for dk in day_row_keywords):
                first_cell = tr.find(['td', 'th'])
                first_cell_lower = first_cell.get_text(separator=' ').strip().lower() if first_cell else ''
                if any(ck in first_cell_lower for ck in ['kinder', 'child', 'youth', 'jugend', 'senior']):
                    continue
                price = parse_currency_price_from_snippet(row_text, prev_price_val, default_currency)
                if price:
                    return price

    return None


def extract_best_price_from_text(text, prev_price_val=0.0, default_currency='€'):
    """
    Finds all currency-number pairs in the text, scores each candidate by surrounding
    keywords, and avoids promotional footer ads.
    """
    if not text:
        return None

    clean_text = strip_promotional_footer(strip_invisible_chars(text))

    # Fast check for Bergfex "(day ticket)" / "(Tageskarte)" or Skiresort "Adults Youth Children"
    bf_match = re.search(
        r'((?:€|EUR|CHF|SFr\.?)\s*[0-9]{2,3}(?:[\.,][0-9]{1,2})?(?:\s*(?:,-|\.\-|-))?)\s*\((?:day ticket|Tageskarte)\)',
        clean_text, re.IGNORECASE
    )
    if bf_match:
        price = parse_currency_price_from_snippet(bf_match.group(1), prev_price_val, default_currency)
        if price:
            return price

    sr_text_match = re.search(
        r'(?:Adults|Erwachsene)(?:\s+(?:Youth|Jugend(?:liche)?))?(?:\s+(?:Children|Kinder))?\s*'
        r'((?:€|EUR|CHF|SFr\.?)\s*[0-9]{2,3}(?:[\.,][0-9]{1,2})?(?:\s*(?:,-|\.\-|-))?)',
        clean_text, re.IGNORECASE
    )
    if sr_text_match:
        price = parse_currency_price_from_snippet(sr_text_match.group(1), prev_price_val, default_currency)
        if price:
            return price

    pattern = re.compile(
        r'(?:(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*(?:,-|\.\-|-))?|'
        r'([0-9]{2,3}(?:[\.,][0-9]{1,2})?)\s*(?:,-|\.\-|-)?\s*(€|EUR|CHF|SFr\.?))',
        re.IGNORECASE
    )

    candidates = []

    for match in pattern.finditer(clean_text):
        curr1, val1, val2, curr2 = match.groups()
        raw_curr = (curr1 or curr2 or '').upper()
        val = clean_numeric_price(val1 or val2)

        if not validate_price_bounds(val, prev_price_val):
            continue

        pre_15 = clean_text[max(0, match.start() - 15):match.start()].lower()
        if default_currency == 'CHF' and 'approx' in pre_15 and '€' in raw_curr:
            continue

        start_ctx = max(0, match.start() - 120)
        end_ctx = min(len(clean_text), match.end() + 80)
        context = clean_text[start_ctx:end_ctx].lower()
        immediate_pre = clean_text[max(0, match.start() - 40):match.start()].lower()

        score = 0
        for pos_kw in CONTEXT_POSITIVE_KEYWORDS:
            if pos_kw in context:
                score += 6
        for neg_kw in CONTEXT_NEGATIVE_IMMEDIATE:
            if neg_kw in immediate_pre and not any(ak in immediate_pre for ak in ['adult', 'erwachsene', 'adulte', 'adulti']):
                score -= 10

        # Do not accept an uncontextualized number on a page that doesn't mention day/adult ticket nearby
        if score == 0:
            continue

        if prev_price_val > 0:
            ratio = val / prev_price_val
            if 1.00 <= ratio <= 1.15:
                score += 10
            elif 0.95 <= ratio < 1.00 or 1.15 < ratio <= 1.28:
                score += 5

        symbol = 'CHF' if ('CHF' in raw_curr or 'SFR' in raw_curr or default_currency == 'CHF') else '€'
        candidates.append((score, val, f"{symbol}{val:.2f}"))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, _, best_formatted = candidates[0]

    if best_score < 4:
        return None

    return best_formatted


def parse_html_text_and_links(html_content, base_url):
    """Extracts visible text and scored candidate links from raw HTML."""
    clean_html = strip_promotional_footer(html_content)
    links = []
    if HAS_BS4:
        soup = BeautifulSoup(clean_html, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            text = strip_invisible_chars(a.get_text(separator=' ')).strip()
            links.append((href, text))
        for tag in soup(['script', 'style', 'noscript', 'svg']):
            tag.decompose()
        visible_text = strip_invisible_chars(soup.get_text(separator=' '))
    else:
        parser = SimpleHTMLParser()
        parser.feed(clean_html)
        links = parser.links
        visible_text = ' '.join(parser.text_parts)

    visible_text = re.sub(r'\s+', ' ', visible_text)

    scored_urls = {}
    parsed_base = urllib.parse.urlparse(base_url)
    base_domain = parsed_base.netloc.lower().replace('www.', '')

    for href, anchor_text in links:
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
        full_url = urllib.parse.urljoin(base_url, href)
        parsed_link = urllib.parse.urlparse(full_url)
        link_domain = parsed_link.netloc.lower().replace('www.', '')

        if base_domain not in link_domain and not any(k in link_domain for k in ['ticket', 'skipass', 'shop', 'tarif', 'bergbahn']):
            continue

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


def stage1_aggregator_search(resort_name, region_name, country, prev_price_val, default_currency, agg_index):
    """
    Stage 1 (v4):
      1A: Looks up the resort in the pre-indexed Skiresort.info & Bergfex Country Directories.
      1B: Visits matched Skiresort.info profile page & Bergfex overview + /preise/ pages.
      1C: Tries direct umlaut-transliterated Skiresort.info & Bergfex URLs.
    """
    sr_matches, bf_matches = agg_index.find_candidates(resort_name, region_name, country)

    # 1A: Check Skiresort.info Directory Card Price & Profile Page
    for sr_entry in sr_matches:
        card_price = parse_currency_price_from_snippet(
            sr_entry['raw_card_text'], prev_price_val, default_currency
        )
        if card_price:
            return card_price, "Stage 1A (Skiresort.info Directory Index)", sr_entry['url']

        html, final_url = fetch_html(sr_entry['url'], timeout=10)
        if html:
            price = extract_structured_table_price(html, prev_price_val, default_currency)
            if not price:
                text, _ = parse_html_text_and_links(html, final_url)
                price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 1A (Skiresort.info Profile Page)", final_url

    # 1B: Check Bergfex Directory Card Price, Main Overview Page ("Prices € XX (day ticket)"), and /preise/
    for bf_entry in bf_matches:
        card_price = parse_currency_price_from_snippet(
            bf_entry['raw_card_text'], prev_price_val, default_currency
        )
        if card_price:
            return card_price, "Stage 1B (Bergfex Directory Index)", bf_entry['url']

        for bf_url in [bf_entry['url'], bf_entry['url'].rstrip('/') + '/preise/']:
            html, final_url = fetch_html(bf_url, timeout=10)
            if html:
                price = extract_structured_table_price(html, prev_price_val, default_currency)
                if not price:
                    text, _ = parse_html_text_and_links(html, final_url)
                    price = extract_best_price_from_text(text, prev_price_val, default_currency)
                if price:
                    return price, "Stage 1B (Bergfex Resort Page)", final_url

    # 1C: Direct Slug Probing on Skiresort.info & Bergfex (both overview and /preise/)
    candidate_slugs = make_slugs(resort_name)
    if '/' in region_name:
        area_part = region_name.split('/')[-1].strip()
        for s in make_slugs(area_part):
            if s not in candidate_slugs:
                candidate_slugs.append(s)

    for slug in candidate_slugs[:3]:
        sr_url = f"https://www.skiresort.info/ski-resort/{slug}/"
        html, final_url = fetch_html(sr_url, timeout=10)
        if html and ('Ski pass prices' in html or 'Day ticket' in html or 'selAd' in html):
            price = extract_structured_table_price(html, prev_price_val, default_currency)
            if not price:
                text, _ = parse_html_text_and_links(html, final_url)
                price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 1C (Skiresort.info Direct)", final_url

        for bf_url in [f"https://www.bergfex.com/{slug}/", f"https://www.bergfex.com/{slug}/preise/"]:
            bf_html, bf_final = fetch_html(bf_url, timeout=10)
            if bf_html and ('day ticket' in bf_html.lower() or 'tageskarte' in bf_html.lower() or 'erwachsene' in bf_html.lower()):
                price = extract_structured_table_price(bf_html, prev_price_val, default_currency)
                if not price:
                    text, _ = parse_html_text_and_links(bf_html, bf_final)
                    price = extract_best_price_from_text(text, prev_price_val, default_currency)
                if price:
                    return price, "Stage 1C (Bergfex Direct)", bf_final

    return None, None, None


def stage2_spider_official_site(website_url, prev_price_val, default_currency):
    """
    Stage 2: Crawls the resort's official website, discovers pricing/skipass links in the DOM,
    and visits top candidate pages to extract the 1-day adult price.
    """
    if not website_url or website_url.strip().lower() in ['n/a', 'closed', '']:
        return None, None, []

    base_url = website_url.strip().rstrip('/')
    if not base_url.startswith(('http://', 'https://')):
        base_url = 'https://' + base_url

    html, final_url = fetch_html(base_url)
    if not html:
        return None, None, [base_url]

    home_text, candidate_links = parse_html_text_and_links(html, final_url)

    parsed_root = urllib.parse.urlparse(final_url)
    root_domain_url = f"{parsed_root.scheme}://{parsed_root.netloc}"
    for fallback_slug in ['/skipass', '/tarifs', '/preise', '/prices', '/tickets', '/prezzi']:
        fallback_full = root_domain_url + fallback_slug
        if fallback_full not in candidate_links:
            candidate_links.append(fallback_full)

    for link_url in candidate_links[:6]:
        sub_html, sub_final_url = fetch_html(link_url, timeout=10)
        if sub_html:
            price = extract_structured_table_price(sub_html, prev_price_val, default_currency)
            if not price:
                sub_text, _ = parse_html_text_and_links(sub_html, sub_final_url)
                price = extract_best_price_from_text(sub_text, prev_price_val, default_currency)
            if price:
                return price, "Stage 2 (Official Site Spider)", [sub_final_url]

    price = extract_structured_table_price(html, prev_price_val, default_currency)
    if not price:
        price = extract_best_price_from_text(home_text, prev_price_val, default_currency)
    if price:
        return price, "Stage 2 (Official Homepage)", [final_url]

    return None, None, (candidate_links[:3] if candidate_links else [final_url])


def stage3_headless_browser(pw_page, target_urls, resort_name, country, prev_price_val, default_currency):
    """
    Stage 3: Uses a persistent Playwright Chromium page to render JS-heavy official ticket pages
    AND perform a live browser search if static requests were blocked.
    """
    if not pw_page:
        return None, None, None

    for url in (target_urls or [])[:2]:
        try:
            pw_page.goto(url, timeout=15000, wait_until="domcontentloaded")
            pw_page.wait_for_timeout(1800)
            rendered_html = strip_invisible_chars(pw_page.content())
            price = extract_structured_table_price(rendered_html, prev_price_val, default_currency)
            if not price:
                text, _ = parse_html_text_and_links(rendered_html, url)
                price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 3A (Playwright Official JS)", url
        except Exception:
            continue

    clean_name = re.sub(r'\(.*?\)', '', strip_invisible_chars(resort_name)).strip()
    search_queries = [
        f'"{clean_name}" {country} ski pass price adult day skiresort.info',
        f'"{clean_name}" {country} skipass preise bergfex'
    ]
    for q in search_queries:
        try:
            b_url = f"https://www.bing.com/search?q={urllib.parse.quote(q)}"
            pw_page.goto(b_url, timeout=14000, wait_until="domcontentloaded")
            pw_page.wait_for_timeout(1200)
            b_html = strip_invisible_chars(pw_page.content())

            if HAS_BS4:
                soup = BeautifulSoup(b_html, 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if 'skiresort.info/ski-resort/' in href or 'bergfex.' in href:
                        pw_page.goto(href, timeout=14000, wait_until="domcontentloaded")
                        pw_page.wait_for_timeout(1000)
                        agg_html = strip_invisible_chars(pw_page.content())
                        price = extract_structured_table_price(agg_html, prev_price_val, default_currency)
                        if not price:
                            text, _ = parse_html_text_and_links(agg_html, href)
                            price = extract_best_price_from_text(text, prev_price_val, default_currency)
                        if price:
                            return price, "Stage 3B (Playwright Search -> Aggregator)", href

            text, _ = parse_html_text_and_links(b_html, b_url)
            price = extract_best_price_from_text(text, prev_price_val, default_currency)
            if price:
                return price, "Stage 3B (Playwright Search Snippet)", b_url
        except Exception:
            continue

    return None, None, None


def process_batch(master_csv, output_csv, audit_csv, offset, limit):
    if not os.path.exists(master_csv) and os.path.exists(FALLBACK_MASTER_CSV):
        master_csv = FALLBACK_MASTER_CSV

    print("\n" + "=" * 78)
    print(" ENHANCED DEEP-DISCOVERY SKI LIFT PASS SCRAPER (v4 - Dual Directory Indexer)")
    print(f" Input File  : {master_csv}")
    print(f" Offset      : {offset} | Batch Limit: {limit}")
    print(f" Capabilities: Requests={HAS_REQUESTS}, BS4={HAS_BS4}, Playwright={HAS_PLAYWRIGHT}")
    print("=" * 78 + "\n")

    if not os.path.exists(master_csv):
        print(f"[x] Error: Master CSV '{master_csv}' not found in {os.getcwd()}")
        return

    with open(master_csv, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    country_idx = next((i for i, h in enumerate(header) if 'Country' in h), 0)
    name_idx = next((i for i, h in enumerate(header) if 'Resort Name' in h), 1)
    region_idx = next((i for i, h in enumerate(header) if 'Region' in h or 'Area' in h), 2)
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

    domain_price_cache = {}
    agg_index = MultiAggregatorDirectoryIndex()

    pw_context_mgr = sync_playwright().start() if HAS_PLAYWRIGHT else None
    pw_browser = None
    pw_page = None
    if pw_context_mgr:
        try:
            pw_browser = pw_context_mgr.chromium.launch(headless=True)
            pw_page = pw_browser.new_page(user_agent=HEADERS['User-Agent'])
        except Exception:
            pw_page = None

    try:
        for idx, row in enumerate(batch_rows, start=offset):
            if len(row) <= max(name_idx, website_idx, pass_idx):
                continue

            country = row[country_idx] if len(row) > country_idx else ""
            resort_name = row[name_idx]
            region_name = row[region_idx] if len(row) > region_idx else ""
            website = row[website_idx]
            prev_price_str = row[pass_idx]
            prev_val = clean_numeric_price(prev_price_str)
            default_curr = 'CHF' if ('CHF' in prev_price_str or country.strip().lower() == 'switzerland') else '€'

            print(f"[{idx + 1}/{len(rows)}] {resort_name} ({country}) | Baseline: {prev_price_str}")

            price, method, source_url = None, None, None

            norm_domain = urllib.parse.urlparse(website if '://' in website else f"https://{website}").netloc.lower().replace('www.', '')
            if norm_domain and norm_domain in domain_price_cache:
                cached_price, cached_url = domain_price_cache[norm_domain]
                if validate_price_bounds(clean_numeric_price(cached_price), prev_val):
                    price = cached_price
                    method = "Stage 0 (Shared Ski Area Domain Cache)"
                    source_url = cached_url

            # Stage 1: Dual Directory Index (Skiresort.info + Bergfex) & Direct Profile Lookup
            if not price:
                price, method, source_url = stage1_aggregator_search(
                    resort_name, region_name, country, prev_val, default_curr, agg_index
                )

            # Stage 2: Official Resort Website Link Spidering
            candidate_js_urls = []
            if not price:
                price, method, candidate_js_urls = stage2_spider_official_site(website, prev_val, default_curr)
                if price and candidate_js_urls:
                    source_url = candidate_js_urls[0]

            # Stage 3: Persistent Headless Browser (Playwright JS Rendering + Browser Search)
            if not price and pw_page:
                price, method, js_url = stage3_headless_browser(
                    pw_page, candidate_js_urls, resort_name, country, prev_val, default_curr
                )
                if price:
                    source_url = js_url

            if price:
                if norm_domain:
                    domain_price_cache[norm_domain] = (price, source_url)
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
                    print(f"   [~] ESTIMATED: {price} (Flagged for quick manual check)")
                else:
                    price = prev_price_str
                    variance_pct = "0.0%"
                    method = "None"
                    source_url = website
                    status = "MANUAL_REQUIRED"
                    stats['manual'] += 1
                    print(f"   [!] MANUAL REQUIRED: No baseline or live price found")

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

            time.sleep(0.5)
    finally:
        if pw_browser:
            try:
                pw_browser.close()
            except Exception:
                pass
        if pw_context_mgr:
            try:
                pw_context_mgr.stop()
            except Exception:
                pass

    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(all_output_rows)

    audit_mode = 'a' if (os.path.exists(audit_csv) and offset > 0) else 'w'
    with open(audit_csv, audit_mode, newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if audit_mode == 'w':
            writer.writerows(audit_rows)
        else:
            writer.writerows(audit_rows[1:])

    total_batch = stats['scraped'] + stats['estimated'] + stats['manual']
    capture_rate = (stats['scraped'] / total_batch * 100) if total_batch > 0 else 0.0

    print("\n" + "=" * 78)
    print(f" BATCH COMPLETE: {total_batch} Resorts Processed")
    print(f" Live Verified Prices : {stats['scraped']} ({capture_rate:.1f}%)")
    print(f" Indexed Fallbacks    : {stats['estimated']}")
    print(f" Manual Check Needed  : {stats['manual']}")
    print(f" Output CSV Saved To  : {output_csv}")
    print(f" Audit Log Saved To   : {audit_csv}")
    print("=" * 78)

    # Print plain-text Audit Table directly to console logs for immediate inspection
    print("\n--- INLINE AUDIT LOG SUMMARY ---")
    print(f"{'Resort Name':<28} | {'Prev':<8} | {'New':<8} | {'Var %':<7} | {'Status':<15} | {'Stage'}")
    print("-" * 105)
    for r in audit_rows[1:]:
        print(f"{r[0][:28]:<28} | {r[3]:<8} | {r[4]:<8} | {r[5]:<7} | {r[8]:<15} | {r[6]}")
    print("-" * 105 + "\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Enhanced Deep Discovery Ski Lift Pass Scraper v4")
    parser.add_argument('--master', type=str, default=DEFAULT_MASTER_CSV, help="Master input CSV path")
    parser.add_argument('--output', type=str, default=DEFAULT_OUTPUT_CSV, help="Updated output CSV path")
    parser.add_argument('--audit', type=str, default=DEFAULT_AUDIT_CSV, help="Audit log CSV path")
    parser.add_argument('--offset', type=int, default=0, help="Starting row offset (default: 0)")
    parser.add_argument('--limit', type=int, default=60, help="Number of resorts per batch (default: 60)")
    args = parser.parse_args()

    process_batch(args.master, args.output, args.audit, args.offset, args.limit)
