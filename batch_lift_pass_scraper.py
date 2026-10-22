import argparse
import csv
import datetime
import io
import os
import re
import subprocess
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

# Optional Playwright support
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Auto-ensure pypdf is available even if workflow cached old requirements.txt
try:
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pypdf>=4.0.0"])
        from pypdf import PdfReader
        PDF_AVAILABLE = True
    except Exception:
        PDF_AVAILABLE = False

MASTER_CSV = 'Ski resorts - Master.csv'
OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
AUDIT_CSV = 'lift_pass_batch_audit.csv'
LEGACY_AUDIT_CSV = 'lift_pass_audit_report.csv'
TIMEOUT_SECONDS = 10
SEASON_INFLATION_RATE = 0.035  # +3.5% fallback index

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'de-AT,de;q=0.9,en-GB;q=0.8,en;q=0.7,fr;q=0.6,it;q=0.5'
}

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
    'jugend', 'youth', 'junior', 'senior', 'senioren', 'student', 'kleinkind', 'mini', 'baby'
]

NON_DAY_NEGATIVE_KEYWORDS = [
    'halbtag', 'half day', 'half-day', 'demi-journée', 'mattino', 'pomeriggio', 'vormittag', 'nachmittag',
    'stunden', 'hours', 'heures', 'ore', 'ab 11', 'ab 12', 'ab 13', 'ab 14',
    'verleih', 'rental', 'location', 'noleggio', 'skischule', 'ski school', 'lesson',
    'parking', 'parkplatz', 'depot', 'rodel', 'toboggan', 'luge', 'slittino', 'sommer', 'summer',
    'bike', 'wandern', 'hiking', 'all-in-card', 'saisonkarte', 'saisonskipass', 'stagionale',
    'jahreskarte', 'season pass', 'skitouren', 'tourengeher', 'ski-de-fond', 'nordique', 'langlauf',
    '2 tage', '3 tage', '4 tage', '5 tage', '6 tage', '2 days', '3 days', '6 days', '2 jours', '6 jours'
]

TEASER_PREFIX_KEYWORDS = [
    'ab €', 'ab eur', 'ab chf', 'from €', 'from eur', 'from chf',
    'à partir de', 'da €', 'nebensaison', 'vorsaison', 'low season', 'early bird', 'petits prix'
]

HIGH_PRIORITY_LINK_KEYWORDS = [
    'skipass', 'tageskarte', 'tagesskipass', 'winterpreise', 'skitarife',
    'preise', 'tarife', 'tarifs', 'forfait', 'prezzi', 'ticket', 'prices', 'billetterie', 'cennik'
]

# Expanded v8 Blacklist: blocks Nordic, Ski-touring, Rentals, Season Passes, Gondola-only & Promo pages
BLACKLIST_LINK_KEYWORDS = [
    'sommer', 'summer', 'ete', 'estate', 'bike', 'wandern', 'hiking', 'klettern',
    'paragleiten', 'tandem', 'rodel', 'toboggan', 'luge', 'slittino', 'camping', 'hotel',
    'unterkunft', 'accommodation', 'webcam', 'wetter', 'weather', 'skischule', 'school',
    'ecole', 'verleih', 'rental', 'location-de-ski', 'location-ski', 'noleggio', 'commerces',
    'jobs', 'karriere', 'impressum', 'datenschutz', 'privacy', 'contact', 'kontakt',
    'all-in-card', 'saisonkarte', 'saisonskipass', 'stagionale', 'jahreskarte', 'season-pass',
    'super-ski-card', 'superskirama', 'laendle-card', 'alpen-plus', 'freizeitticket',
    'snow-card', 'magic-pass', 'gutschein', 'voucher', 'loipe', 'langlauf', 'ski-de-fond',
    'nordique', 'activites-nordiques', 'skitouren', 'tourengeher', 'rando', 'pieton',
    'pass-skyvall', 'skyvall', 'petits-prix', 'bons-plans', 'camp', 'tapahtumat', 'event'
]

BERGFEX_COUNTRY_SLUGS = {
    'austria': ('oesterreich', 7),
    'switzerland': ('schweiz', 5),
    'france': ('frankreich', 5),
    'italy': ('italien', 5),
    'germany': ('deutschland', 4),
    'andorra': ('andorra', 1),
    'slovenia': ('slowenien', 2),
    'poland': ('polen', 2),
    'czechia': ('tschechien', 2),
    'czech republic': ('tschechien', 2),
    'slovakia': ('slowakei', 2),
    'spain': ('spanien', 2)
}

SKIRESORT_COUNTRY_SLUGS = {
    'albania': ('albania', 1),
    'andorra': ('andorra', 1),
    'austria': ('austria', 5),
    'bosnia & herzegovina': ('bosnia-and-herzegovina', 1),
    'bulgaria': ('bulgaria', 1),
    'cyprus': ('cyprus', 1),
    'czechia': ('czech-republic', 2),
    'czech republic': ('czech-republic', 2),
    'finland': ('finland', 2),
    'france': ('france', 5),
    'georgia': ('georgia', 1),
    'germany': ('germany', 3),
    'greece': ('greece', 1),
    'italy': ('italy', 4),
    'montenegro': ('montenegro', 1),
    'norway': ('norway', 2),
    'poland': ('poland', 2),
    'romania': ('romania', 1),
    'scotland': ('united-kingdom/scotland', 1),
    'serbia': ('serbia', 1),
    'slovakia': ('slovakia', 2),
    'slovenia': ('slovenia', 1),
    'spain': ('spain', 1),
    'sweden': ('sweden', 2),
    'switzerland': ('switzerland', 4)
}

# Explicit alias & valley-suffix map for both Bergfex and Skiresort.info matching
RESORT_ALIAS_MAP = {
    'achensee - christlum': ['christlum-achenkirch', 'christlum'],
    'alpbach': ['alpbachtal', 'ski-juwel-alpbachtal-wildschoenau', 'skijuwel'],
    'auffach': ['alpbachtal', 'ski-juwel-alpbachtal-wildschoenau', 'skijuwel'],
    'niederau': ['alpbachtal', 'ski-juwel-alpbachtal-wildschoenau', 'skijuwel'],
    'ankogel-mallnitz': ['ankogel-mallnitz', 'ankogel'],
    'annaberg': ['annaberg-lungoetz', 'annaberg'],
    'axamer lizum': ['axamer-lizum'],
    'bad gastein': ['bad-gastein', 'badhofgastein', 'schlossalm-angertal-stubnerkogel'],
    'bad hofgastein': ['badhofgastein', 'bad-hofgastein'],
    'bad kleinkirchheim': ['bad-kleinkirchheim'],
    'brandnertal': ['brandnertal'],
    'brixen im thale': ['brixen-im-thale', 'soell', 'skiwelt-wilder-kaiser-brixental', 'scheffau'],
    'dachstein krippenstein': ['dachstein-krippenstein'],
    'dachstein west': ['dachstein-west', 'russbach-am-pass-gschuett-dachstein-west', 'gosau'],
    'damüls-mellau': ['damuels', 'damuels-mellau'],
    'diedamskopf': ['diedamskopf'],
    'dreiländereck': ['dreilaendereck'],
    'ellmau': ['ellmau', 'soell', 'skiwelt-wilder-kaiser-brixental', 'scheffau'],
    'ellmau - going': ['ellmau', 'soell', 'skiwelt-wilder-kaiser-brixental', 'scheffau'],
    'söll': ['soell', 'skiwelt-wilder-kaiser-brixental', 'scheffau'],
    'hopfgarten': ['hopfgarten', 'soell', 'skiwelt-wilder-kaiser-brixental', 'scheffau'],
    'westendorf': ['westendorf', 'soell', 'skiwelt-wilder-kaiser-brixental', 'scheffau'],
    'emberger alm': ['embergeralm'],
    'fageralm': ['fageralm', 'fageralm-forstau', 'reiteralm'],
    'faschina-fontanella': ['faschina'],
    'fendels': ['fendels', 'ried-fendels'],
    'feuerkogel': ['feuerkogel-ebensee', 'feuerkogel'],
    'fieberbrunn': ['fieberbrunn', 'saalbach-hinterglemm-leogang'],
    'filzmoos': ['filzmoos'],
    'flachau': ['flachau', 'snow-space-salzburg'],
    'fügen - spieljoch': ['fuegen-spieljoch', 'spieljoch-fuegen'],
    'gaberl': ['gaberl'],
    'gaißau-hintersee': ['gaissau-hintersee'],
    'galsterberg': ['galsterberg', 'galsterbergalm-pruggern'],
    'galtür': ['galtuer', 'silvapark-galtuer'],
    'gargellen': ['gargellen-montafon', 'gargellen'],
    'gemeindealpe': ['gemeindealpe-mitterbach', 'gemeindealpe'],
    'gerlitzen': ['gerlitzen-alpe', 'gerlitzen'],
    'gerlos': ['gerlos', 'zillertal-arena'],
    'glun gezer': ['glungezer-tulfes', 'glungezer'],
    'goldeck': ['goldeck'],
    'golm': ['golm-montafon', 'golm'],
    'grebenzen': ['grebenzen'],
    'großeck-speiereck': ['grosseck-speiereck'],
    'grossglockner resort kals-matrei': ['kals-matrei-grossglockner', 'matrei', 'kals', 'grossglockner-resort-kals-matrei'],
    'heiligenblut': ['heiligenblut-grossglockner', 'heiligenblut'],
    'heutal - unken': ['heutal-unken', 'heutal'],
    'hinterglemm': ['saalbach-hinterglemm-leogang'],
    'saalbach': ['saalbach-hinterglemm-leogang'],
    'leogang': ['saalbach-hinterglemm-leogang'],
    'hinterstoder': ['hinterstoder-hoess', 'hinterstoder'],
    'hintertux glacier': ['hintertux', 'hintertuxer-gletscher'],
    'hochficht': ['hochficht-boehmerwald', 'hochficht'],
    'hochkar': ['hochkar-goestling', 'hochkar'],
    'hochkönig': ['hochkoenig-maria-alm-dienten-muehlbach', 'hochkoenig'],
    'hochkössen': ['koessen-unterberg', 'koessen', 'hochkoessen-unterberghorn-koessen'],
    'hochkrimml': ['krimml-hochkrimml', 'gerlos', 'zillertal-arena'],
    'königsleiten': ['koenigsleiten', 'gerlos', 'zillertal-arena'],
    'zell am ziller': ['zell-am-ziller', 'gerlos', 'zillertal-arena'],
    'hochoetz': ['hochoetz-oetztal', 'hochoetz-oetz', 'hochoetz'],
    'hochrindl': ['hochrindl'],
    'hochzeiger': ['hochzeiger', 'hochzeiger-jerzens'],
    'hochzillertal-hochfügen': ['hochfuegen', 'hochzillertal', 'kaltenbach-hochzillertal-hochfuegen'],
    'hohentauern': ['hohentauern'],
    'ischgl': ['ischgl', 'ischgl-samnaun-silvretta-arena'],
    'kitzbühel': ['kitzbuehel-kirchberg', 'kitzski-kitzbuehel-kirchberg', 'kitzbuehel'],
    'kitzsteinhorn - kaprun': ['kitzsteinhorn-kaprun', 'kitzsteinhorn'],
    'kleinwalsertal (kanzelwand - ifen)': ['kanzelwand-fellhorn', 'ifen', 'kleinwalsertal'],
    'lech zürs': ['lech-zuers-arlberg', 'st-anton-st-christoph-stuben-lech-zuers-warth-schroecken'],
    'st. anton am arlberg': ['st-anton-am-arlberg', 'st-anton-st-christoph-stuben-lech-zuers-warth-schroecken'],
    'warth-schröcken': ['warth-schroecken', 'st-anton-st-christoph-stuben-lech-zuers-warth-schroecken'],
    'lermoos': ['lermoos', 'lermoos-grubigstein'],
    'loferer alm': ['lofer', 'almenwelt-lofer'],
    'mayrhofen': ['mayrhofen', 'zillertal-3000'],
    'obergurgl-hochgurgl': ['obergurgl-hochgurgl', 'gurgl-obergurgl-hochgurgl'],
    'pitztal glacier': ['pitztaler-gletscher', 'pitztal-gletscher'],
    'reiteralm': ['reiteralm', 'schladming'],
    'schladming': ['schladming', 'planai', 'hochwurzen'],
    'seefeld - rosshütte': ['rosshuette', 'seefeld-rosshuette'],
    'st. jakob im defereggental': ['st-jakob-im-defereggental', 'brunnalm'],
    'st. johann in tirol': ['st-johann-in-tirol'],
    'steinplatte-waidring': ['steinplatte-waidring', 'steinplatte'],
    'wildkogel': ['wildkogel-neukirchen-bramberg', 'wildkogel'],
    'wurzeralm': ['wurzeralm-spital-am-pyhrn', 'wurzeralm'],
    'zell am see': ['schmittenhoehe-zell-am-see', 'zell-am-see'],
    'vallnord': ['pal-arinsal', 'pal-arinsal-la-massana'],
    'ordino arcalis': ['ordino-arcalis'],
    'grandvalira': ['grandvalira'],
    '3 zinnen dolomites': ['3-zinnen-dolomiten', 'three-peaks-dolomites'],
    'arabba / marmolada': ['arabba', 'marmolada'],
    'canazei': ['val-di-fassa', 'belvedere-canazei'],
    'cervinia': ['breuil-cervinia', 'zermatt-breuil-cervinia'],
    'cortina d\'ampezzo': ['cortina-d-ampezzo'],
    'folgaria - fiorentini': ['folgaria-fiorentini'],
    'folgarida - marilleva': ['folgarida-marilleva', 'madonna-di-campiglio-pinzolo-folgarida-marilleva'],
    'madonna di campiglio': ['madonna-di-campiglio-pinzolo-folgarida-marilleva', 'madonna-di-campiglio'],
    'gitschberg jochtal': ['gitschberg-jochtal'],
    'klausberg': ['klausberg-skiworld-ahrntal', 'klausberg'],
    'kronplatz': ['kronplatz-dolomiten', 'kronplatz-plan-de-corones'],
    'ladurns': ['ladurns-pflerschtal', 'ladurns'],
    'madesimo': ['valchiavenna-madesimo-campodolcino', 'madesimo'],
    'meran 2000': ['meran-2000'],
    'mondolè ski (prato nevoso)': ['mondole-ski', 'prato-nevoso'],
    'pila': ['pila'],
    'plose': ['plose-brixen', 'plose'],
    'pragelato': ['via-lattea', 'pragelato'],
    'claviere': ['via-lattea', 'claviere'],
    'ratschings-jaufen': ['racines-giovo-ratschings-jaufen', 'ratschings-jaufen'],
    'alpe d\'huez': ['alpe-d-huez'],
    'chamonix': ['chamonix', 'brévent-flégère'],
    'courchevel': ['courchevel', 'les-3-vallees'],
    'val d\'isère': ['val-d-isere', 'tignes-val-d-isere'],
    'val thorens': ['val-thorens', 'les-3-vallees'],
    'les deux alpes': ['les-2-alpes', 'les-deux-alpes']
}

# Only true single-price interconnected ski areas (excludes multi-resort operator portals like n-py.com)
UNIFIED_SYNDICATE_KEYS = [
    'skiwelt', 'ski juwel', 'zillertal arena', 'skicircus', 'snow space salzburg'
]

MULTI_RESORT_PORTAL_DOMAINS = [
    'n-py.com', 'turismofvg.it', 'skisport.com', 'skistar.com',
    'compagniedesalpes.com', 'aramon.com', 'bergbahnen.org'
]

# Bulk In-Memory Country Caches
# Each entry: {'slug': str, 'url': str, 'title_norm': str, 'price': float, 'curr': str, 'raw_title': str}
BERGFEX_BULK_CACHE = {}
SKIRESORT_BULK_CACHE = {}


def clean_numeric_price(price_str):
    if not price_str:
        return 0.0
    match = re.search(r'([0-9]+(?:[\.,][0-9]+)?)', str(price_str))
    if not match:
        return 0.0
    return float(match.group(1).replace(',', '.'))


def detect_currency(text, fallback_str=''):
    if 'CHF' in str(text) or 'SFr' in str(text) or 'CHF' in str(fallback_str):
        return 'CHF'
    return '€'


def transliterate_slug(text):
    s = str(text).lower().strip()
    replacements = {
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'á': 'a', 'à': 'a', 'â': 'a', 'å': 'aa',
        'ó': 'o', 'ò': 'o', 'ô': 'o', 'ø': 'oe',
        'ú': 'u', 'ù': 'u', 'û': 'u', 'ñ': 'n', 'ç': 'c',
        'ř': 'r', 'š': 's', 'č': 'c', 'ž': 'z', 'ě': 'e', 'ý': 'y', 'ů': 'u'
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s


def is_within_guardrail(val, prev_price, is_trusted_aggregator=False):
    """
    Checks whether candidate price is within realistic 2-season bounds.
    Trusted aggregator bulk tables (Skiresort.info / Bergfex) allow -6% to +26%.
    Official site spiders enforce a tighter -2% to +22% window to block promo/season traps.
    """
    if not (16.0 <= val <= 135.0):
        return False
    if val == 100.0 and prev_price < 82.0:
        return False
    if prev_price > 15.0:
        pct_diff = (val - prev_price) / prev_price
        min_bound = -0.06 if is_trusted_aggregator else -0.02
        max_bound = 0.26 if is_trusted_aggregator else 0.22
        if pct_diff < min_bound or pct_diff > max_bound:
            return False
    return True


def parse_aggregator_price_string(text):
    """
    Parses prices formatted like '€ 83,-', '€ 66.50', '€ 79,50', 'CHF 89,00', 'SFr. 116,- / approx. € 125,-'.
    Prefers CHF for Swiss listings if SFr./CHF is first.
    """
    if not text:
        return 0.0, '€'
    # Match currency symbol followed by digits and optional decimal or ',-'
    m = re.search(
        r'(CHF|SFr\.?|€|EUR)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-|\b)',
        str(text),
        re.IGNORECASE
    )
    if m:
        curr_raw = m.group(1).upper()
        curr = 'CHF' if ('CHF' in curr_raw or 'SFR' in curr_raw) else '€'
        val = float(m.group(2).replace(',', '.'))
        return val, curr
    return 0.0, '€'


def bulk_ingest_bergfex_country(country):
    """
    v8 Phase 1A: Downloads Bergfex country overview tables (?page=1..N) once per country,
    extracting BOTH the inline 1-day winter price from the 'Preis' column AND the resort slug.
    """
    c_key = re.sub(r'\s+', ' ', country.lower().strip())
    if c_key in BERGFEX_BULK_CACHE:
        return BERGFEX_BULK_CACHE[c_key]

    BERGFEX_BULK_CACHE[c_key] = []
    if c_key not in BERGFEX_COUNTRY_SLUGS:
        return []

    b_slug, max_pages = BERGFEX_COUNTRY_SLUGS[c_key]
    print(f"   [*] Bulk-ingesting Bergfex country tables for {country} ({b_slug}, up to {max_pages} pages)...")
    records = []
    seen_slugs = set()

    for page_num in range(1, max_pages + 1):
        url = f"https://www.bergfex.at/{b_slug}/" if page_num == 1 else f"https://www.bergfex.at/{b_slug}/?page={page_num}"
        try:
            resp = HTTP_SESSION.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, 'html.parser')
            page_new_count = 0

            # Parse every table row or list item containing a resort link
            for tr in soup.find_all(['tr', 'li', 'div']):
                # Must be a compact row/card, not the whole page body
                row_text = tr.get_text(' ', strip=True)
                if len(row_text) < 10 or len(row_text) > 450:
                    continue
                if 'Pisten' not in row_text and 'Lifte' not in row_text and 'Preis' not in row_text:
                    continue

                a_tag = tr.find('a', href=True)
                if not a_tag:
                    continue
                href = a_tag['href'].strip()
                m_slug = re.match(r'^/([a-z0-9\-]{3,65})/?$', href)
                if not m_slug:
                    continue
                slug = m_slug.group(1)
                if slug == b_slug or any(bad in slug for bad in BLACKLIST_LINK_KEYWORDS):
                    continue

                val, curr = parse_aggregator_price_string(row_text)
                title_norm = transliterate_slug(row_text.split('Pisten')[0])

                # Keep record with valid price if duplicate appears
                if slug in seen_slugs:
                    if val > 0:
                        for rec in records:
                            if rec['slug'] == slug and rec['price'] == 0:
                                rec['price'] = val
                                rec['curr'] = curr
                    continue

                seen_slugs.add(slug)
                page_new_count += 1
                records.append({
                    'slug': slug,
                    'url': f"https://www.bergfex.at/{slug}/",
                    'title_norm': f"{slug}-{title_norm}",
                    'price': val,
                    'curr': curr,
                    'raw_title': a_tag.get_text(' ', strip=True)[:80]
                })

            time.sleep(0.25)
            if page_new_count == 0:
                break
        except Exception:
            break

    priced_count = sum(1 for r in records if r['price'] > 0)
    print(f"       -> Indexed {len(records)} Bergfex resorts ({priced_count} with instant inline prices).")
    BERGFEX_BULK_CACHE[c_key] = records
    return records


def bulk_ingest_skiresort_country(country):
    """
    v8 Phase 1B: Downloads Skiresort.info country directory pages (page 1..N) once per country,
    parsing the outer '.panel.resort-list-item' container so we capture the inline '€ 83,-' price!
    """
    c_key = re.sub(r'\s+', ' ', country.lower().strip())
    if c_key in SKIRESORT_BULK_CACHE:
        return SKIRESORT_BULK_CACHE[c_key]

    SKIRESORT_BULK_CACHE[c_key] = []
    if c_key not in SKIRESORT_COUNTRY_SLUGS:
        return []

    s_slug, max_pages = SKIRESORT_COUNTRY_SLUGS[c_key]
    print(f"   [*] Bulk-ingesting Skiresort.info directory for {country} ({s_slug}, up to {max_pages} pages)...")
    records = []
    seen_urls = set()

    for page_num in range(1, max_pages + 1):
        url = (
            f"https://www.skiresort.info/ski-resorts/{s_slug}/"
            if page_num == 1
            else f"https://www.skiresort.info/ski-resorts/{s_slug}/page/{page_num}/"
        )
        try:
            resp = HTTP_SESSION.get(url, timeout=10, verify=False)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, 'html.parser')
            page_new_count = 0

            # Find all canonical resort links (excluding subpages like /ski-lifts/, /trail-map/, /test-report/)
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].strip()
                m_url = re.search(r'(https?://www\.skiresort\.info/ski-resort/[a-z0-9\-]+/|/ski-resort/[a-z0-9\-]+/)$', href)
                if not m_url:
                    continue

                full_url = urljoin(url, href)
                link_text = a_tag.get_text(' ', strip=True)
                if not link_text or link_text.lower() in ['details', 'test report', 'report', 'view ski resort', 'overview']:
                    continue

                # Climb up DOM to find the outer card container that holds both the title and the '€ XX,-' price
                card_text = link_text
                curr_node = a_tag
                for _ in range(5):
                    parent = curr_node.find_parent(['div', 'article', 'li'])
                    if not parent:
                        break
                    p_text = parent.get_text(' ', strip=True)
                    if len(p_text) > 1200:
                        break
                    card_text = p_text
                    if ('ski lifts' in p_text.lower() or 'km' in p_text.lower()) and ('€' in p_text or 'SFr' in p_text or 'CHF' in p_text):
                        break
                    curr_node = parent

                val, curr = parse_aggregator_price_string(card_text)
                slug = full_url.rstrip('/').split('/')[-1]
                title_norm = transliterate_slug(f"{slug} {link_text}")

                if full_url in seen_urls:
                    if val > 0:
                        for rec in records:
                            if rec['url'] == full_url and rec['price'] == 0:
                                rec['price'] = val
                                rec['curr'] = curr
                    continue

                seen_urls.add(full_url)
                page_new_count += 1
                records.append({
                    'slug': slug,
                    'url': full_url,
                    'title_norm': title_norm,
                    'price': val,
                    'curr': curr,
                    'raw_title': link_text[:80]
                })

            time.sleep(0.25)
            if page_new_count == 0:
                break
        except Exception:
            break

    priced_count = sum(1 for r in records if r['price'] > 0)
    print(f"       -> Indexed {len(records)} Skiresort.info resorts ({priced_count} with instant inline prices).")
    SKIRESORT_BULK_CACHE[c_key] = records
    return records


def build_resort_search_tokens(resort_name):
    """Generates clean normalized tokens and explicit aliases for a resort name."""
    raw_lower = resort_name.lower().strip()
    tokens = []

    if raw_lower in RESORT_ALIAS_MAP:
        tokens.extend(RESORT_ALIAS_MAP[raw_lower])

    primary = transliterate_slug(resort_name)
    cleaned_words = re.sub(
        r'\b(glacier|gletscher|resort|ski|arena|area|bergbahnen|skigebiet|mount|dolomites|dolomiten)\b',
        '', raw_lower
    ).strip()
    cleaned_slug = transliterate_slug(cleaned_words)
    no_space = transliterate_slug(raw_lower.replace(' ', ''))

    for t in [primary, cleaned_slug, no_space]:
        if t and len(t) >= 3 and t not in tokens:
            tokens.append(t)

    # Split compound names (e.g., 'Ravascletto-Zoncolan' -> 'ravascletto', 'zoncolan')
    for part in re.split(r'[\-/(),]+', raw_lower):
        p_slug = transliterate_slug(part)
        if len(p_slug) >= 4 and p_slug not in ['ski', 'area', 'nord', 'sud', 'st', 'val', 'les', 'la', 'le']:
            if p_slug not in tokens:
                tokens.append(p_slug)

    return tokens


def match_in_bulk_cache(resort_name, bulk_records):
    """Scores and returns matching records from a bulk country cache."""
    tokens = build_resort_search_tokens(resort_name)
    scored_matches = []

    for rec in bulk_records:
        slug = rec['slug']
        t_norm = rec['title_norm']
        best_tok_score = 0

        for idx, tok in enumerate(tokens):
            priority_bonus = max(0, 6 - idx)
            if slug == tok:
                best_tok_score = max(best_tok_score, 30 + priority_bonus)
            elif slug.startswith(tok + '-') or slug.endswith('-' + tok):
                best_tok_score = max(best_tok_score, 24 + priority_bonus)
            elif f"-{tok}-" in f"-{t_norm}-":
                best_tok_score = max(best_tok_score, 18 + priority_bonus)
            elif len(tok) >= 5 and tok in t_norm:
                best_tok_score = max(best_tok_score, 12 + priority_bonus)

        if best_tok_score > 0:
            # Prefer records that already have an inline price > 0
            price_bonus = 5 if rec['price'] > 0 else 0
            scored_matches.append((best_tok_score + price_bonus, rec))

    scored_matches.sort(key=lambda x: x[0], reverse=True)
    return [m[1] for m in scored_matches[:3]]


def extract_skiresort_detail_price(html_content, prev_price=0.0):
    """
    v8 Fix for Skiresort.info Resort Detail Pages:
    Specifically parses 'Day ticket Main season Adults Youth Children € 83,- € 51,- € 51,-'
    where the first price after 'Adults' is the Adult 1-day peak price.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    full_text = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))

    # Pattern 1: Look for 'Day ticket' / 'Main season' / 'Adults' block on Skiresort.info
    m_block = re.search(
        r'(?:Day ticket|Tageskarte).*?(?:Adults|Erwachsene).*?(CHF|SFr\.?|€|EUR)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-|\b)',
        full_text,
        re.IGNORECASE
    )
    if m_block:
        curr_raw = m_block.group(1).upper()
        curr = 'CHF' if ('CHF' in curr_raw or 'SFR' in curr_raw) else '€'
        val = float(m_block.group(2).replace(',', '.'))
        if is_within_guardrail(val, prev_price, is_trusted_aggregator=True):
            snippet = m_block.group(0)[:140]
            return val, curr, 25, snippet

    return parse_html_for_best_price(html_content, prev_price, is_trusted_aggregator=True)


def extract_adult_price_from_html_tables(soup, prev_price=0.0, is_trusted_aggregator=False):
    """Parses HTML <tr>, <dl>, and <li> rows directly for 1-Day Adult prices."""
    price_regex = re.compile(
        r'(?:(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-|\b)|\b([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-)?\s*(€|EUR|CHF|SFr\.?))',
        re.IGNORECASE
    )

    best_table_cand = None

    for tr in soup.find_all(['tr', 'dl', 'li']):
        row_text = tr.get_text(' ', strip=True)
        row_lower = row_text.lower()

        if len(row_text) > 350 or len(row_text) < 6:
            continue

        if any(neg in row_lower for neg in NON_DAY_NEGATIVE_KEYWORDS):
            continue

        has_day_kw = any(k in row_lower for k in DAY_KEYWORDS)
        has_adult_kw = any(k in row_lower for k in ADULT_KEYWORDS)

        if not (has_day_kw or has_adult_kw):
            continue

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

            prefix = row_text[max(0, m.start() - 25):m.start()].lower()
            if any(ck in prefix for ck in CHILD_YOUTH_KEYWORDS) and not any(ak in prefix for ak in ADULT_KEYWORDS):
                continue
            if any(tk in prefix for tk in TEASER_PREFIX_KEYWORDS):
                continue

            if is_within_guardrail(val, prev_price, is_trusted_aggregator=is_trusted_aggregator):
                curr_sym = 'CHF' if ('chf' in curr_raw.lower() or 'sfr' in curr_raw.lower()) else '€'
                score = 14
                if has_day_kw and has_adult_kw:
                    score += 6
                if prev_price > 15.0:
                    pct_diff = (val - prev_price) / prev_price
                    if 0.0 <= pct_diff <= 0.18:
                        score += 8
                row_prices.append((score, val, curr_sym, row_text[:140]))

        if row_prices:
            row_prices.sort(key=lambda x: (x[0], x[1]), reverse=True)
            top = row_prices[0]
            if best_table_cand is None or (top[0], top[1]) > (best_table_cand[2], best_table_cand[0]):
                best_table_cand = (top[1], top[2], top[0], top[3])

    return best_table_cand


def score_price_candidates(text, prev_price=0.0, is_trusted_aggregator=False):
    if not text:
        return None

    normalized_text = re.sub(r'\s+', ' ', text)
    patterns = [
        r'(€|EUR|CHF|SFr\.?)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-|\b)',
        r'\b([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-)?\s*(€|EUR|CHF|SFr\.?)'
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

            if not is_within_guardrail(val, prev_price, is_trusted_aggregator=is_trusted_aggregator):
                continue

            start_pos = max(0, match.start() - 100)
            end_pos = min(len(normalized_text), match.end() + 80)
            window = normalized_text[start_pos:end_pos]
            window_lower = window.lower()

            immediate_prefix = normalized_text[max(0, match.start() - 40):match.end()].lower()
            immediate_suffix = normalized_text[match.start():min(len(normalized_text), match.end() + 20)].lower()

            score = 0
            for kw in POSITIVE_KEYWORDS:
                if kw in window_lower:
                    score += 4

            if any(ak in immediate_prefix for ak in ADULT_KEYWORDS):
                score += 6

            if any(ck in immediate_prefix for ck in CHILD_YOUTH_KEYWORDS) and not any(ak in immediate_prefix for ak in ADULT_KEYWORDS):
                score -= 10
            elif any(ck in immediate_suffix for ck in CHILD_YOUTH_KEYWORDS):
                score -= 6

            for kw in NON_DAY_NEGATIVE_KEYWORDS:
                if kw in immediate_prefix:
                    score -= 10
                elif kw in window_lower:
                    score -= 5

            for teaser in TEASER_PREFIX_KEYWORDS:
                if teaser in immediate_prefix:
                    score -= 8

            if any(yr in window_lower for yr in ['2026', '2027', '26/27', '2026/27', '25/26']):
                score += 3
            if any(old_yr in window_lower for old_yr in ['2021', '2022', '2023', '22/23']):
                score -= 5

            if prev_price > 15.0:
                pct_diff = (val - prev_price) / prev_price
                if 0.0 <= pct_diff <= 0.18:
                    score += 10
                elif 0.18 < pct_diff <= 0.25:
                    score += 4
                elif -0.06 <= pct_diff < 0.0:
                    score += 3
                else:
                    score -= 12

            curr_symbol = 'CHF' if ('chf' in raw_curr.lower() or 'sfr' in raw_curr.lower()) else '€'
            snippet_clean = window.strip()[:140]
            candidates.append((score, val, curr_symbol, snippet_clean))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_val, best_curr, best_snippet = candidates[0]

    if best_score < 4:
        return None

    return best_val, best_curr, best_score, best_snippet


def parse_html_for_best_price(html_content, prev_price=0.0, is_trusted_aggregator=False):
    soup = BeautifulSoup(html_content, 'html.parser')
    table_cand = extract_adult_price_from_html_tables(soup, prev_price, is_trusted_aggregator)
    if table_cand:
        return table_cand
    full_text = soup.get_text(' ', strip=True)
    return score_price_candidates(full_text, prev_price, is_trusted_aggregator)


def resolve_via_aggregators(resort_name, country, prev_price=0.0):
    """
    v8 Re-Engineered Phase 1 & Phase 2:
      1. Instant lookup in Bulk In-Memory Bergfex & Skiresort.info Country Tables.
      2. If both aggregators have an inline price in the bulk table, checks consensus!
      3. If matched in the directory but price was 'dynamisch' or 0, fetches at most
         1 Skiresort.info detail page and 1 Bergfex /preise/ page with polite pacing.
    """
    b_records = bulk_ingest_bergfex_country(country)
    s_records = bulk_ingest_skiresort_country(country)

    b_matches = match_in_bulk_cache(resort_name, b_records)
    s_matches = match_in_bulk_cache(resort_name, s_records)

    # 1A. Check instant inline price from Bergfex Bulk Country Table
    for b_rec in b_matches:
        if b_rec['price'] > 0 and is_within_guardrail(b_rec['price'], prev_price, is_trusted_aggregator=True):
            snippet = f"Bergfex {country} Bulk Table ({b_rec['raw_title']}): {b_rec['curr']}{b_rec['price']:.2f}"
            return (b_rec['price'], b_rec['curr'], 25, snippet), "Stage 1A (Bergfex Bulk Country Table)", b_rec['url']

    # 1B. Check instant inline price from Skiresort.info Bulk Country Directory
    for s_rec in s_matches:
        if s_rec['price'] > 0 and is_within_guardrail(s_rec['price'], prev_price, is_trusted_aggregator=True):
            snippet = f"Skiresort.info {country} Directory ({s_rec['raw_title']}): {s_rec['curr']}{s_rec['price']:.2f}"
            return (s_rec['price'], s_rec['curr'], 25, snippet), "Stage 1B (Skiresort.info Bulk Directory)", s_rec['url']

    # Phase 2A: Targeted Bergfex /preise/ lookup (at most 2 slugs, polite 0.3s delay)
    slugs_to_try = [m['slug'] for m in b_matches[:2]]
    for tok in build_resort_search_tokens(resort_name)[:2]:
        if tok not in slugs_to_try:
            slugs_to_try.append(tok)

    for slug in slugs_to_try[:2]:
        for b_url in [f"https://www.bergfex.at/{slug}/preise/", f"https://www.bergfex.com/{slug}/preise/"]:
            try:
                time.sleep(0.25)
                resp = HTTP_SESSION.get(b_url, timeout=8, verify=False, allow_redirects=True)
                if resp.status_code == 200 and 'bergfex' in resp.url and '/sommer/' not in resp.url.lower():
                    cand = parse_html_for_best_price(resp.text, prev_price, is_trusted_aggregator=True)
                    if cand:
                        return cand, "Stage 1C (Bergfex /preise/ Page)", resp.url
            except Exception:
                continue

    # Phase 2B: Targeted Skiresort.info Resort Detail Page lookup
    s_urls_to_try = [m['url'] for m in s_matches[:2]]
    for tok in build_resort_search_tokens(resort_name)[:1]:
        direct_s_url = f"https://www.skiresort.info/ski-resort/{tok}/"
        if direct_s_url not in s_urls_to_try:
            s_urls_to_try.append(direct_s_url)

    for s_url in s_urls_to_try[:2]:
        try:
            time.sleep(0.25)
            resp = HTTP_SESSION.get(s_url, timeout=8, verify=False, allow_redirects=True)
            if resp.status_code == 200 and '/ski-resort/' in resp.url:
                cand = extract_skiresort_detail_price(resp.text, prev_price)
                if cand:
                    return cand, "Stage 1D (Skiresort.info Detail Page)", resp.url
        except Exception:
            continue

    return None, None, None


def rank_internal_pricing_links(base_url, html_content):
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

        if any(bad in combined_text for bad in BLACKLIST_LINK_KEYWORDS):
            continue

        score = 0
        for kw in HIGH_PRIORITY_LINK_KEYWORDS:
            if kw in combined_text:
                score += 5

        if any(w in combined_text for w in ['winter', 'hiver', 'inverno', 'skibetrieb', 'tageskarten', 'forfaits-ski', '2026', '2027']):
            score += 6

        if score > 0:
            if parsed.path.lower().endswith('.pdf'):
                pdf_links.append((score, full_url))
            else:
                if full_url not in scored_links or score > scored_links[full_url]:
                    scored_links[full_url] = score

    sorted_html_links = [url for url, _ in sorted(scored_links.items(), key=lambda x: x[1], reverse=True)[:3]]
    sorted_pdf_links = [url for _, url in sorted(pdf_links, key=lambda x: x[0], reverse=True)[:2]]
    return sorted_html_links, sorted_pdf_links


def extract_from_pdf(pdf_url, prev_price=0.0):
    if not PDF_AVAILABLE or any(bad in pdf_url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
        return None
    try:
        resp = HTTP_SESSION.get(pdf_url, timeout=TIMEOUT_SECONDS, verify=False)
        if resp.status_code == 200 and b'%PDF' in resp.content[:1024]:
            reader = PdfReader(io.BytesIO(resp.content))
            full_text = []
            for page in reader.pages[:6]:
                full_text.append(page.extract_text() or '')
            return score_price_candidates(' '.join(full_text), prev_price, is_trusted_aggregator=False)
    except Exception:
        pass
    return None


def scrape_with_playwright(url, prev_price=0.0):
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
            page.goto(url, timeout=12000, wait_until='domcontentloaded')
            page.wait_for_timeout(1000)

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
                    if btn.is_visible(timeout=300):
                        btn.click(timeout=600)
                        page.wait_for_timeout(300)
                        break
                except Exception:
                    pass

            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(600)

            rendered_html = page.content()
            visible_text = page.inner_text('body')
            browser.close()

            candidate = parse_html_for_best_price(rendered_html, prev_price, is_trusted_aggregator=False)
            if not candidate:
                candidate = score_price_candidates(visible_text, prev_price, is_trusted_aggregator=False)
            return candidate, rendered_html
    except Exception:
        return None, None


def get_syndicate_cache_key(region_str, website_url, prev_price):
    """
    v8 Fix for Finding #3 (Cache Poisoning):
    1. Includes `prev_price:.2f` in the cache key so two resorts only share a cached price
       if they ALSO shared the exact same baseline price in Master.csv!
    2. Never caches bare multi-resort operator portals (n-py.com, turismofvg.it, skistar.com)
       unless the full URL path is identical.
    """
    reg_lower = str(region_str).lower()
    for syn in UNIFIED_SYNDICATE_KEYS:
        if syn in reg_lower:
            return f"syndicate:{syn}:{prev_price:.2f}"

    if website_url and website_url.strip().lower() not in ['n/a', 'closed', '']:
        t_url = website_url.strip()
        if not t_url.startswith(('http://', 'https://')):
            t_url = 'https://' + t_url
        parsed = urlparse(t_url)
        domain = parsed.netloc.lower().replace('www.', '')
        path = parsed.path.rstrip('/')
        if any(portal in domain for portal in MULTI_RESORT_PORTAL_DOMAINS):
            return f"url:{domain}{path}:{prev_price:.2f}"
        if domain:
            return f"domain:{domain}:{prev_price:.2f}"
    return None


def deep_discover_resort_price(resort_name, country, region_str, website_url, prev_price, shared_cache):
    """
    Executes the v8 Re-Engineered Discovery Pipeline:
      Stage 0 : Baseline-Verified Shared Ski Area Cache
      Stage 1A: Bergfex Bulk Country Table (Instant In-Memory)
      Stage 1B: Skiresort.info Bulk Country Directory (Instant In-Memory)
      Stage 1C: Bergfex /preise/ Subpage (Rate-Limited)
      Stage 1D: Skiresort.info Resort Detail Page (Structured Adult Parser)
      Stage 2 : Hardened Official Site Spider + PDF Extractor
      Stage 3 : Headless Chromium JS Fallback
    """
    cache_key = get_syndicate_cache_key(region_str, website_url, prev_price)
    if cache_key and cache_key in shared_cache:
        cached_price, cached_source, cached_snippet = shared_cache[cache_key]
        cached_val = clean_numeric_price(cached_price)
        if is_within_guardrail(cached_val, prev_price, is_trusted_aggregator=True):
            return cached_price, "Stage 0 (Shared Ski Area Domain Cache)", cached_source, cached_snippet

    # STAGE 1 (Bulk Country Tables + Targeted Aggregator Pages)
    cand, stage_label, agg_url = resolve_via_aggregators(resort_name, country, prev_price)
    if cand:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, agg_url, snippet)
        return price_str, stage_label, agg_url, snippet

    # STAGE 2: Hardened Official Website Spider
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
                cand = parse_html_for_best_price(sub_resp.text, prev_price, is_trusted_aggregator=False)
                if cand and cand[2] >= 8:
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
        if cand and cand[2] >= 6:
            val, curr, score, snippet = cand
            price_str = f"{curr}{val:.2f}"
            return price_str, "Stage 2B (Official PDF Price Sheet)", pdf_url, snippet

    # STAGE 3: Headless Chromium JS Rendering
    js_target = html_subpages[0] if html_subpages else target_url
    cand, _ = scrape_with_playwright(js_target, prev_price)
    if cand and cand[2] >= 8:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, js_target, snippet)
        return price_str, "Stage 3 (Headless Chromium JS)", js_target, snippet

    return None, "Fallback (Index +3.5%)", target_url, ""


def write_github_step_summary(summary_rows, total_count, verified_count, estimated_count):
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not summary_path:
        return
    try:
        pct = (verified_count / total_count * 100.0) if total_count else 0.0
        lines = [
            "## 🎿 v8 Re-Engineered Bulk-Aggregator Lift Pass Scraper — Audit Summary",
            f"- **Resorts Processed:** `{total_count}`",
            f"- **Live Verified Scrapes:** `{verified_count}` (`{pct:.1f}%`)",
            f"- **Indexed Fallbacks (+3.5%):** `{estimated_count}`\n",
            "| # | Resort Name | Country | Baseline | 2026/27 Price | Var % | Status | Discovery Stage | Source |",
            "|---|---|---|---|---|---|---|---|---|"
        ]
        for r in summary_rows:
            idx, country, name, old_p, new_p, var_s, stage, status, src, _ = r
            c_clean = str(country).replace('\n', ' ')
            src_display = f"[Link]({src})" if str(src).startswith('http') else str(src)
            lines.append(f"| {idx} | **{name}** | {c_clean} | {old_p} | **{new_p}** | `{var_s}` | `{status}` | {stage} | {src_display} |")

        with open(summary_path, 'a', encoding='utf-8') as sf:
            sf.write('\n'.join(lines) + '\n')
        print("   [+] Wrote visual Markdown Audit Table to GitHub Actions Run Summary ($GITHUB_STEP_SUMMARY)!")
    except Exception as e:
        print(f"   [!] Could not write to $GITHUB_STEP_SUMMARY: {e}")


def main():
    parser = argparse.ArgumentParser(description="v8 Re-Engineered Bulk Aggregator Lift Pass Scraper")
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
    print(" RE-ENGINEERED SKI LIFT PASS SCRAPER (v8 - Bulk Aggregator Table Engine)")
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

        country = re.sub(r'\s+', ' ', row[country_idx].strip())
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
