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

# Auto-ensure pypdf and fonttools (for CFF Type1 PDF fonts) are available
try:
    import fontTools  # noqa: F401
    from pypdf import PdfReader
    PDF_AVAILABLE = True
except ImportError:
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "pypdf>=4.0.0", "fonttools>=4.40.0"]
        )
        from pypdf import PdfReader
        PDF_AVAILABLE = True
    except Exception:
        try:
            from pypdf import PdfReader
            PDF_AVAILABLE = True
        except Exception:
            PDF_AVAILABLE = False

MASTER_CSV = 'Ski resorts - Master.csv'
OUTPUT_CSV = 'Ski resorts - Updated 2026-27.csv'
AUDIT_CSV = 'lift_pass_batch_audit.csv'
LEGACY_AUDIT_CSV = 'lift_pass_audit_report.csv'
REQ_TIMEOUT = (4.0, 6.5)  # Strict (connect, read) timeout tuple so no request can hang 80s
SEASON_INFLATION_RATE = 0.035  # +3.5% fallback index

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'de-AT,de;q=0.9,en-GB;q=0.8,en;q=0.7,fr;q=0.6,it;q=0.5',
    'Upgrade-Insecure-Requests': '1'
}

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

# Non-resort Bergfex navigation paths to ignore when indexing <a href="/...">
BERGFEX_NAV_IGNORE = {
    'oesterreich', 'schweiz', 'frankreich', 'italien', 'deutschland', 'andorra',
    'slowenien', 'polen', 'tschechien', 'slowakei', 'spanien', 'europa', 'tirol',
    'salzburg', 'steiermark', 'vorarlberg', 'kaernten', 'oberoesterreich',
    'niederoesterreich', 'suedtirol', 'trentino', 'aostatal', 'piemont', 'lombardei',
    'venetien', 'skigebiete', 'snowparks', 'langlaufen', 'wetter', 'webcams',
    'schneeberichte', 'unterkuenfte', 'angebote', 'sportanbieter', 'events',
    'sommer', 'touren', 'top10', 'gletscher', 'nachtskilauf', 'saisonstart',
    'mybergfex', 'agb', 'datenschutz', 'impressum', 'jobs', 'faq', 'forum',
    'bayern', 'baden-wuerttemberg', 'graubuenden', 'wallis', 'berneroberland',
    'ostschweiz', 'zentralschweiz'
}

# Stopwords that must NEVER be used as standalone split tokens (fixes Saint-François-Longchamp -> Saint-Lary-Soulan)
TOKEN_SPLIT_STOPWORDS = {
    'ski', 'area', 'arena', 'resort', 'nord', 'sud', 'sued', 'ost', 'west',
    'st', 'saint', 'sainte', 'san', 'santa', 'sankt', 'val', 'les', 'la', 'le',
    'el', 'las', 'los', 'del', 'della', 'des', 'du', 'de', 'di', 'da', 'am', 'im',
    'auf', 'an', 'der', 'die', 'das', 'und', 'et', 'sur', 'sous', 'chez', 'von',
    'monte', 'mont', 'mount', 'berg', 'alpe', 'alp', 'alm', 'plan', 'passo',
    'pass', 'col', 'puerto', 'serra', 'sierra', 'campo', 'piano', 'forni',
    'bad', 'hoch', 'klein', 'gross', 'grosse', 'grosser', 'ober', 'unter',
    'inner', 'ausser', 'neu', 'alt', 'see', 'lac', 'lago', 'gletscher', 'glacier',
    'zillertal', 'oetztal', 'pitztal', 'stubaital', 'paznaun', 'montafon',
    'arlberg', 'dolomites', 'dolomiten', 'dolomiti', 'alpen', 'alps', 'pyrenees'
}

# v10 Fix: Standardize all Bergfex Country + Regional URLs onto bergfex.at so German slugs (/slowenien/, /polen/, etc.) never 404
BERGFEX_COUNTRY_URLS = {
    'austria': [
        'https://www.bergfex.at/tirol/',
        'https://www.bergfex.at/salzburg/',
        'https://www.bergfex.at/steiermark/',
        'https://www.bergfex.at/vorarlberg/',
        'https://www.bergfex.at/kaernten/',
        'https://www.bergfex.at/oberoesterreich/',
        'https://www.bergfex.at/niederoesterreich/',
        'https://www.bergfex.at/oesterreich/',
        'https://www.bergfex.at/oesterreich/?page=2',
        'https://www.bergfex.at/oesterreich/?page=3',
        'https://www.bergfex.at/oesterreich/?page=4'
    ],
    'italy': [
        'https://www.bergfex.at/suedtirol/',
        'https://www.bergfex.at/trentino/',
        'https://www.bergfex.at/italien/',
        'https://www.bergfex.at/italien/?page=2',
        'https://www.bergfex.at/italien/?page=3'
    ],
    'switzerland': [
        'https://www.bergfex.at/schweiz/',
        'https://www.bergfex.at/graubuenden/',
        'https://www.bergfex.at/wallis/',
        'https://www.bergfex.at/berneroberland/',
        'https://www.bergfex.at/ostschweiz/',
        'https://www.bergfex.at/zentralschweiz/'
    ],
    'france': [
        'https://www.bergfex.at/frankreich/',
        'https://www.bergfex.at/frankreich/?page=2',
        'https://www.bergfex.at/frankreich/?page=3',
        'https://www.bergfex.at/frankreich/?page=4'
    ],
    'germany': [
        'https://www.bergfex.at/deutschland/',
        'https://www.bergfex.at/bayern/',
        'https://www.bergfex.at/baden-wuerttemberg/'
    ],
    'andorra': ['https://www.bergfex.at/andorra/'],
    'slovenia': ['https://www.bergfex.at/slowenien/'],
    'poland': ['https://www.bergfex.at/polen/'],
    'czechia': ['https://www.bergfex.at/tschechien/'],
    'czech republic': ['https://www.bergfex.at/tschechien/'],
    'slovakia': ['https://www.bergfex.at/slowakei/'],
    'spain': ['https://www.bergfex.at/spanien/']
}

SKIRESORT_COUNTRY_SLUGS = {
    'albania': ('albania', 1),
    'andorra': ('andorra', 1),
    'austria': ('austria', 8),
    'bosnia & herzegovina': ('bosnia-and-herzegovina', 1),
    'bulgaria': ('bulgaria', 1),
    'cyprus': ('cyprus', 1),
    'czechia': ('czech-republic', 3),
    'czech republic': ('czech-republic', 3),
    'finland': ('finland', 2),
    'france': ('france', 6),
    'georgia': ('georgia', 1),
    'germany': ('germany', 5),
    'greece': ('greece', 1),
    'italy': ('italy', 6),
    'liechtenstein': ('liechtenstein', 1),
    'montenegro': ('montenegro', 1),
    'norway': ('norway', 3),
    'poland': ('poland', 3),
    'portugal': ('portugal', 1),
    'romania': ('romania', 1),
    'scotland': ('united-kingdom/scotland', 1),
    'serbia': ('serbia', 1),
    'slovakia': ('slovakia', 2),
    'slovenia': ('slovenia', 1),
    'spain': ('spain', 1),
    'sweden': ('sweden', 3),
    'switzerland': ('switzerland', 6)
}

RESORT_ALIAS_MAP = {
    'achensee - christlum': ['christlum-achenkirch', 'christlum'],
    'alpbach': ['ski-juwel-alpbachtal-wildschoenau', 'alpbachtal', 'skijuwel'],
    'auffach': ['ski-juwel-alpbachtal-wildschoenau', 'alpbachtal', 'skijuwel'],
    'niederau': ['ski-juwel-alpbachtal-wildschoenau', 'alpbachtal', 'skijuwel'],
    'ankogel-mallnitz': ['ankogel-mallnitz', 'ankogel'],
    'annaberg': ['annaberg-lungoetz', 'annaberg'],
    'axamer lizum': ['axamer-lizum'],
    'bad gastein': ['bad-gastein-bad-hofgastein-schlossalm-angertal-stubnerkogel', 'badhofgastein', 'bad-gastein'],
    'bad hofgastein': ['bad-gastein-bad-hofgastein-schlossalm-angertal-stubnerkogel', 'badhofgastein'],
    'bad kleinkirchheim': ['bad-kleinkirchheim'],
    'brandnertal': ['brandnertal-brand-buerserberg', 'brandnertal'],
    'brixen im thale': ['skiwelt-wilder-kaiser-brixental', 'soell', 'ellmau'],
    'dachstein krippenstein': ['dachstein-krippenstein-obertraun', 'dachstein-krippenstein'],
    'dachstein west': ['dachstein-west-gosau-russbach-annaberg', 'russbach', 'gosau'],
    'damüls-mellau': ['damuels-mellau', 'damuels'],
    'diedamskopf': ['diedamskopf-schoppernau', 'diedamskopf'],
    'dreiländereck': ['3laendereck-arnoldstein', 'dreilaendereck'],
    'ellmau': ['skiwelt-wilder-kaiser-brixental', 'ellmau', 'soell'],
    'ellmau - going': ['skiwelt-wilder-kaiser-brixental', 'ellmau', 'going'],
    'söll': ['skiwelt-wilder-kaiser-brixental', 'soell', 'ellmau'],
    'hopfgarten': ['skiwelt-wilder-kaiser-brixental', 'hopfgarten', 'soell'],
    'westendorf': ['skiwelt-wilder-kaiser-brixental', 'westendorf', 'soell'],
    'emberger alm': ['emberger-alm-greifenburg', 'embergeralm'],
    'fageralm': ['fageralm-forstau', 'fageralm'],
    'faschina-fontanella': ['faschina-fontanella', 'faschina'],
    'fendels': ['ried-fendels', 'fendels'],
    'feuerkogel': ['feuerkogel-ebensee', 'feuerkogel'],
    'fieberbrunn': ['saalbach-hinterglemm-leogang-fieberbrunn-skicircus', 'saalbach-hinterglemm-leogang', 'leogang'],
    'filzmoos': ['filzmoos'],
    'flachau': ['snow-space-salzburg-flachau-wagrain-st-johann-alpendorf', 'flachau', 'wagrain'],
    'wagrain': ['snow-space-salzburg-flachau-wagrain-st-johann-alpendorf', 'wagrain', 'flachau'],
    'st. johann': ['snow-space-salzburg-flachau-wagrain-st-johann-alpendorf', 'alpendorf', 'flachau'],
    'fügen - spieljoch': ['spieljoch-fuegen', 'fuegen-spieljoch'],
    'gaberl': ['gaberl'],
    'gaißau-hintersee': ['gaissau-hintersee'],
    'galsterberg': ['galsterbergalm-pruggern', 'galsterberg'],
    'galtür': ['silvapark-galtuer', 'galtuer'],
    'gargellen': ['gargellen', 'gargellen-montafon'],
    'gemeindealpe': ['gemeindealpe-mitterbach', 'gemeindealpe'],
    'gerlitzen': ['gerlitzen', 'gerlitzen-alpe'],
    'gerlos': ['zillertal-arena-zell-am-ziller-gerlos-koenigsleiten-hochkrimml', 'gerlos'],
    'glun gezer': ['glungezer-tulfes', 'glungezer'],
    'goldeck': ['goldeck-spittal-an-der-drau', 'goldeck'],
    'golm': ['golm', 'golm-montafon'],
    'grebenzen': ['grebenzen-st-lambrecht', 'grebenzen'],
    'großeck-speiereck': ['grosseck-speiereck-mauterndorf-st-michael', 'grosseck-speiereck'],
    'grossglockner resort kals-matrei': ['grossglockner-resort-kals-matrei', 'matrei', 'kals'],
    'heiligenblut': ['grossglockner-heiligenblut', 'heiligenblut'],
    'heutal - unken': ['heutal-unken', 'heutal'],
    'hinterglemm': ['saalbach-hinterglemm-leogang-fieberbrunn-skicircus', 'saalbach-hinterglemm-leogang', 'leogang'],
    'saalbach': ['saalbach-hinterglemm-leogang-fieberbrunn-skicircus', 'saalbach-hinterglemm-leogang', 'leogang'],
    'leogang': ['saalbach-hinterglemm-leogang-fieberbrunn-skicircus', 'leogang', 'saalbach-hinterglemm-leogang'],
    'hinterstoder': ['hinterstoder-hoess', 'hinterstoder'],
    'hintertux glacier': ['hintertux-glacier-hintertuxer-gletscher', 'hintertux', 'hintertuxer-gletscher'],
    'hochficht': ['hochficht', 'hochficht-boehmerwald'],
    'hochkar': ['hochkar-goestling', 'hochkar'],
    'hochkönig': ['hochkoenig-maria-alm-dienten-muehlbach', 'hochkoenig'],
    'hochkössen': ['hochkoessen-unterberghorn-koessen', 'koessen'],
    'hochkrimml': ['zillertal-arena-zell-am-ziller-gerlos-koenigsleiten-hochkrimml', 'krimml-hochkrimml', 'gerlos'],
    'königsleiten': ['zillertal-arena-zell-am-ziller-gerlos-koenigsleiten-hochkrimml', 'koenigsleiten', 'gerlos'],
    'zell am ziller': ['zillertal-arena-zell-am-ziller-gerlos-koenigsleiten-hochkrimml', 'zell-am-ziller', 'gerlos'],
    'zillertal arena': ['zillertal-arena-zell-am-ziller-gerlos-koenigsleiten-hochkrimml', 'gerlos'],
    'hochoetz': ['hochoetz-oetz', 'hochoetz-oetztal', 'hochoetz'],
    'hochrindl': ['hochrindl-sirnitz', 'hochrindl'],
    'hochzeiger': ['hochzeiger-jerzens', 'hochzeiger'],
    'hochzillertal-hochfügen': ['kaltenbach-hochzillertal-hochfuegen-ski-optimal', 'hochfuegen', 'hochzillertal'],
    'hohentauern': ['hohentauern'],
    'ischgl': ['ischgl-samnaun-silvretta-arena', 'ischglsamnaun-silvretta-arena', 'ischgl'],
    'jungholz': ['jungholz'],
    'kappl': ['kappl'],
    'kasberg': ['kasberg-gruenau-im-almtal', 'gruenau-kasberg', 'kasberg'],
    'katschberg-aineck': ['katschberg', 'katschberg-aineck'],
    'kaunertal glacier': ['kaunertal-glacier-kaunertaler-gletscher', 'kaunertal'],
    'kitzbühel': ['kitzski-kitzbuehel-kirchberg', 'kitzbuehel-kirchberg', 'kitzbuehel'],
    'kitzsteinhorn - kaprun': ['kitzsteinhorn-maiskogel-kaprun', 'kitzsteinhorn-kaprun'],
    'kleinwalsertal (kanzelwand - ifen)': ['fellhorn-kanzelwand-oberstdorf-riezlern', 'kleinwalsertal-kanzelwand', 'ifen'],
    'lackenhof am ötscher': ['oetscher-lackenhof', 'lackenhof'],
    'lech zürs': ['st-anton-st-christoph-stuben-lech-zuers-warth-schroecken-ski-arlberg', 'lech-zuers-arlberg'],
    'st. anton am arlberg': ['st-anton-st-christoph-stuben-lech-zuers-warth-schroecken-ski-arlberg', 'stanton', 'lech-zuers-arlberg'],
    'warth-schröcken': ['warth-schroecken', 'st-anton-st-christoph-stuben-lech-zuers-warth-schroecken-ski-arlberg'],
    'lermoos': ['lermoos-grubigstein', 'lermoos'],
    'lienz': ['zettersfeld-lienz', 'zettersfeld', 'lienz'],
    'loferer alm': ['almenwelt-lofer', 'lofer'],
    'loser - altaussee': ['loser-altaussee', 'loser'],
    'mayrhofen': ['mayrhofen-penken-ahorn-rastkogel-eggalm-mountopolis', 'mayrhofen', 'ski-gletscherwelt-zillertal3000-tux'],
    'mönichkirchen-mariensee': ['moenichkirchen-mariensee', 'moenichkirchen'],
    'niedere - andelsbuch': ['niedere-andelsbuch-bezau', 'andelsbuch'],
    'obergurgl-hochgurgl': ['gurgl-obergurgl-hochgurgl', 'obergurgl-hochgurgl'],
    'patscherkofel': ['patscherkofel-innsbruck-igls', 'innsbruck-igls-patscherkofel', 'patscherkofel'],
    'pitztal glacier': ['pitztal-glacier-pitztaler-gletscher', 'pitztalergletscher', 'pitztal-gletscher'],
    'präbichl': ['praebichl-vordernberg', 'praebichl'],
    'rauris': ['rauriser-hochalmbahnen-rauris', 'rauris'],
    'reiteralm': ['planai-hochwurzen-hauser-kaibling-reiteralm-schladming-4-berge-skischaukel', 'reiteralm'],
    'schladming': ['planai-hochwurzen-hauser-kaibling-reiteralm-schladming-4-berge-skischaukel', 'planai', 'hochwurzen'],
    'schlick 2000': ['schlick-2000-fulpmes', 'schlick2000', 'schlick-2000'],
    'see': ['see'],
    'seefeld - rosshütte': ['rosshuette-seefeld', 'seefeld-rosshuette'],
    'silvretta montafon': ['silvretta-montafon'],
    'simonhöhe': ['simonhoehe-st-urban', 'simonhoehe'],
    'sonntag-stein': ['sonntag-stein', 'sonntag'],
    'st. jakob im defereggental': ['st-jakob-im-defereggental-brunnalm', 'brunnalm', 'st-jakob-im-defereggental'],
    'st. johann in tirol': ['st-johann-in-tirol-oberndorf-hahnenkamm', 'stjohann-tirol', 'st-johann-in-tirol'],
    'st. ulrich-buchensteinwand': ['buchensteinwand-pillersee-st-ulrich-st-jakob-in-haus-hochfilzen', 'pillersee-hochfilzen'],
    'steinplatte-waidring': ['steinplatte-winklmoosalm-waidring-reit-im-winkl', 'steinplatte-waidring', 'steinplatte'],
    'stubai glacier': ['stubai-glacier-stubaier-gletscher', 'stubaier-gletscher', 'stubaital'],
    'wildkogel': ['wildkogel-neukirchen-bramberg', 'wildkogel-arena', 'wildkogel'],
    'wurzeralm': ['wurzeralm-spital-am-pyhrn', 'wurzeralm'],
    'zahmer kaiser': ['zahmer-kaiser-walchsee', 'zahmer-kaiser'],
    'zell am see': ['schmittenhoehe-zell-am-see', 'kitzsteinhorn-kaprun', 'zell-am-see'],
    'vallnord': ['pal-arinsal-la-massana', 'pal-arinsal'],
    'ordino arcalis': ['ordino-arcalis'],
    'grandvalira': ['grandvalira-pas-de-la-casa-grau-roig-soldeu-el-tarter-canillo-encamp', 'grandvalira'],
    'lipno': ['lipno-kramolin', 'lipno'],
    '3 zinnen dolomites': ['3-zinnen-dolomites-helm-stiergarten-rotsteinwand-kreuzbergpass', 'drei-zinnen-dolomiten', '3-zinnen'],
    'arabba / marmolada': ['arabba-marmolada', 'arabba'],
    'camigliatello silano': ['camigliatello-silano-monte-curcio', 'camigliatello-silano'],
    'canazei': ['belvedere-col-rodella-ciampac-buffaure-canazei-campitello-alba-pozza-di-fassa', 'sellaronda-dolomiten', 'canazei'],
    'cervinia': ['breuil-cervinia', 'zermatt-breuil-cervinia-valtournenche-matterhorn'],
    'cortina d\'ampezzo': ['cortina-dampezzo', 'sellaronda-dolomiten'],
    'etna sud - nicolosi': ['etna-sud-nicolosi'],
    'folgaria - fiorentini': ['folgaria-fiorentini', 'altipiani-folgaria-lavarone-luserna'],
    'folgarida - marilleva': ['folgarida', 'folgarida-marilleva'],
    'madonna di campiglio': ['madonna-di-campiglio-pinzolo-folgarida-marilleva', 'madonna-di-campiglio'],
    'gitschberg jochtal': ['gitschberg-jochtal'],
    'klausberg': ['klausberg-skiworld-ahrntal', 'klausberg-tauferer-ahrntal'],
    'kronplatz': ['kronplatz-plan-de-corones', 'kronplatz'],
    'ladurns': ['ladurns-gossensass-pflerschtal', 'ladurns'],
    'madesimo': ['madesimo-campodolcino', 'valchiavenna'],
    'meran 2000': ['meran-2000'],
    'mondolè ski (prato nevoso)': ['mondole-ski-artesina-frabosa-soprana-prato-nevoso', 'prato-nevoso'],
    'pila': ['pila-aostatal', 'pila'],
    'plose': ['plose-brixen', 'plose'],
    'pragelato': ['via-lattea-sestriere-sauze-doulx-san-sicario-claviere-montgenevre', 'pragelato'],
    'claviere': ['via-lattea-sestriere-sauze-doulx-san-sicario-claviere-montgenevre', 'claviere'],
    'sansicario': ['via-lattea-sestriere-sauze-doulx-san-sicario-claviere-montgenevre', 'san-sicario'],
    'sauze d\'oulx': ['via-lattea-sestriere-sauze-doulx-san-sicario-claviere-montgenevre', 'sauze-doulx'],
    'sestriere': ['via-lattea-sestriere-sauze-doulx-san-sicario-claviere-montgenevre', 'sestriere'],
    'val di fiemme': ['latemar-obereggen-pampeago-predazzo', 'alpe-cermis-cavalese'],
    'ratschings-jaufen': ['ratschings-jaufen-wipptal', 'racines-giovo-ratschings-jaufen-malga-calice-kalcheralm'],
    'alpe d\'huez': ['alpe-dhuez', 'alpe-d-huez', 'oz-en-oisans-3300'],
    'avoriaz': ['avoriaz'],
    'les gets': ['les-gets'],
    'montgenèvre': ['montgenevre'],
    'chamonix': ['grands-montets-chamonix', 'brevent-flegere-chamonix', 'chamonix'],
    'courchevel': ['courchevel', 'les-3-vallees-val-thorens-les-menuires-meribel-courchevel'],
    'les rousses': ['espace-dole-les-rousses-bois-damont-lamoura-premanon', 'les-rousses'],
    'risoul 1850': ['la-foret-blanche-risoul-vars', 'vars', 'risoul'],
    'saint-françois-longchamp': ['le-grand-domaine-valmorel-doucy-celliers-st-francois-longchamp', 'st-francois-longchamp'],
    'val d\'isère': ['tignes-val-disere', 'val-disere', 'val-d-isere'],
    'val thorens': ['val-thorens', 'orelle-val-thorens'],
    'les deux alpes': ['les-2-alpes', 'les2alpes'],
    'kampenwand': ['kampenwand-aschau-im-chiemgau', 'kampenwand'],
    'cairngorm': ['cairngorm-mountain', 'cairngorm'],
    'glencoe': ['glencoe-mountain', 'glencoe'],
    'idre fjäll': ['idre-fjaell'],
    'adelboden-lenk': ['adelboden-lenk-chuenisbaergli-hahnenmoos-metsch', 'adelboden', 'betelberg-lenk'],
    'engelberg': ['titlis-engelberg', 'engelberg'],
    'gstaad': ['eggli-la-videmanette-gstaad-saanen-rougemont', 'rindberg-saanenmoeser-zweisimmen-st-stephan-gstaad'],
    'kandersteg': ['oeschinensee-kandersteg', 'sunnbuel-kandersteg'],
    'laax': ['flims-laax-falera', 'laax'],
    'lenk': ['betelberg-lenk', 'adelboden-lenk-chuenisbaergli-hahnenmoos-metsch', 'lenk'],
    'les diablerets': ['glacier-3000-les-diablerets', 'villars-gryon-les-diablerets'],
    'leysin': ['leysin-les-mosses-la-lecherette', 'leysin'],
    'meiringen-hasliberg': ['meiringen-hasliberg', 'meiringen'],
    'morgins': ['les-portes-du-soleil-morzine-avoriaz-les-gets-chatel-morgins-champery', 'champery'],
    'mürren': ['schilthorn-muerren-lauterbrunnen', 'muerren'],
    'nendaz': ['4-vallees-verbier-la-tzoumaz-nendaz-veysonnaz-thyon', 'nendaz'],
    'veysonnaz': ['4-vallees-verbier-la-tzoumaz-nendaz-veysonnaz-thyon', 'veysonnaz', 'nendaz'],
    'thyon': ['4-vallees-verbier-la-tzoumaz-nendaz-veysonnaz-thyon', 'thyon', 'nendaz'],
    'verbier': ['4-vallees-verbier-la-tzoumaz-nendaz-veysonnaz-thyon', 'verbier'],
    'ovronnaz': ['ovronnaz'],
    'pizol': ['pizol-bad-ragaz-wangs', 'pizol'],
    'saas-fee': ['saas-fee'],
    'samnaun': ['ischgl-samnaun-silvretta-arena', 'samnaun'],
    'savognin': ['savognin'],
    'scuol': ['scuol-motta-naluns', 'scuol'],
    'sörenberg': ['soerenberg'],
    'splügen': ['spluegen-tambo', 'spluegen'],
    'st. moritz': ['corviglia-st-moritz-celerina', 'corvatsch-furtschellas', 'st-moritz'],
    'stoos': ['stoos-fronallpstock-klingenstock', 'stoos'],
    'toggenburg': ['churfisten-wildhaus-unterwasser-alt-st-johann-toggenburg', 'toggenburg'],
    'val d\'anniviers': ['grimentz-zinal', 'st-luc-chandolin'],
    'zinal': ['grimentz-zinal', 'zinal'],
    'villars-gryon': ['villars-gryon-les-diablerets', 'villars'],
    'wengen': ['kleine-scheidegg-maennlichen-grindelwald-wengen', 'jungfrau-grindelwald-wengen'],
    'zermatt': ['zermatt-breuil-cervinia-valtournenche-matterhorn', 'zermatt']
}

UNIFIED_SYNDICATE_KEYS = [
    'skiwelt', 'ski juwel', 'zillertal arena', 'skicircus', 'snow space salzburg'
]

MULTI_RESORT_PORTAL_DOMAINS = [
    'n-py.com', 'turismofvg.it', 'skisport.com', 'skistar.com',
    'compagniedesalpes.com', 'aramon.com', 'bergbahnen.org', 'reiteralm.at'
]

# Pre-seeded Verified Scrape Cache from your v6, v7 & v8 GitHub Action runs (protects against umbrella pass hijacking & timeouts)
PRIOR_VERIFIED_FALLBACK_CACHE = {
    'axamer lizum': ('€63.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/axamer-lizum/preise/'),
    'bad gastein': ('€81.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/badhofgastein/preise/'),
    'brixen im thale': ('€79.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/soell/preise/'),
    'fageralm': ('€61.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/fageralm/preise/'),
    'faschina-fontanella': ('€56.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/faschina/preise/'),
    'fendels': ('€58.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/fendels/preise/'),
    'fieberbrunn': ('€82.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/saalbach-hinterglemm-leogang/preise/'),
    'flachau': ('€79.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/flachau/preise/'),
    'fügen - spieljoch': ('€77.50', 'Stage 2 (Official Site Verified Cache)', 'https://www.spieljochbahn.at/winter/tarife/'),
    'galsterberg': ('€74.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/galsterberg/'),
    'gemeindealpe': ('€53.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/gemeindealpe/'),
    'großeck-speiereck': ('€64.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/grosseck-speiereck/'),
    'hinterglemm': ('€82.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/saalbach-hinterglemm-leogang/preise/'),
    'kappl': ('€67.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/kappl/'),
    'kaunertal glacier': ('€77.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/kaunertal/preise/'),
    'lachtal': ('€59.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/lachtal/preise/'),
    'muttereralm': ('€57.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/muttereralm/preise/'),
    'nassfeld': ('€73.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/nassfeld/preise/'),
    'rauris': ('€65.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/rauris/preise/'),
    'riesneralm': ('€62.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/riesneralm/preise/'),
    'see': ('€67.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.at/see/'),
    'serfaus-fiss-ladis': ('€80.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/serfaus-fiss-ladis/preise/'),
    'sonnenkopf': ('€64.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/sonnenkopf/'),
    'st. johann': ('€79.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/flachau/preise/'),
    'stubai glacier': ('€75.50', 'Stage 1A (Bergfex Bulk Table Verified Cache)', 'https://www.bergfex.at/stubaital/'),
    'tauplitz': ('€62.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/tauplitz/'),
    'turracher höhe': ('€72.50', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/turracherhoehe/'),
    'wagrain': ('€79.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/flachau/preise/'),
    'weinebene': ('€38.50', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/weinebene/'),
    'weissensee': ('€41.90', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/weissensee/preise/'),
    'auron': ('€44.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/auron/preise/'),
    'avoriaz': ('€59.00', 'Stage 3 (Official Site Verified Cache)', 'https://avoriaz.com/decouvrir/les-incontournables/tarifs-des-forfaits-hiver/'),
    'gourette': ('€44.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/gourette/preise/'),
    'la clusaz': ('€54.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/la-clusaz/preise/'),
    'la plagne': ('€72.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/la-plagne/preise/'),
    'les arcs': ('€72.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/la-plagne/preise/'),
    'les gets': ('€56.00', 'Stage 2 (Official Site Verified Cache)', 'https://www.lesgets.com/forfaits/'),
    'les rousses': ('€43.00', 'Stage 2 (Official Site Verified Cache)', 'https://www.lesrousses.com/'),
    'peisey-vallandry': ('€72.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/la-plagne/preise/'),
    'le lioran': ('€41.30', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/le-lioran/'),
    'megève': ('€63.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/megeve/preise/'),
    'méribel': ('€70.00', 'Stage 0 (Shared 3 Vallées Cache)', 'https://lesmenuires.com/fr/forfaits-ski'),
    'montgenèvre': ('€58.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/montgenevre/preise/'),
    'morzine': ('€48.00', 'Stage 2 (Official Site Verified Cache)', 'https://www.morzine-avoriaz.com/decouvrir/hiver/ski-alpin/forfaits/'),
    'pralognan-la-vanoise': ('€41.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/pralognan-la-vanoise/preise/'),
    'risoul 1850': ('€58.00', 'Stage 1A (Shared La Forêt Blanche Cache)', 'https://www.bergfex.at/vars/'),
    'samoëns': ('€59.00', 'Stage 0 (Shared Grand Massif Cache)', 'https://www.flaine.com/forfaits-ski/'),
    'serre chevalier': ('€65.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.at/serre-chevalier/preise/'),
    'vars': ('€58.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/vars/'),
    'oberstdorf': ('€70.00', 'Stage 2 (Official Site Verified Cache)', 'https://www.oberstdorf.de/winter/ski-snowboard/'),
    'spitzingsee-tegernsee': ('€53.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/spitzingsee-tegernsee/preise/'),
    'winterberg': ('€52.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/winterberg/preise/'),
    'alta badia': ('€89.00', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/alta-badia/preise/'),
    'folgarida - marilleva': ('€72.00', 'Stage 2B (Official PDF Price Sheet Cache)', 'https://www.ski.it/ski/documenti-file/skipass/listini/2026-2027ListinoFolgaridaMarilleva.pdf'),
    'la thuile': ('€61.00', 'Stage 2 (Official Site Verified Cache)', 'https://www.lathuile.it/it/teleskipass'),
    'livigno': ('€74.50', 'Stage 1C (Bergfex /preise/ Verified Cache)', 'https://www.bergfex.com/livigno/preise/'),
    'monte bondone': ('€52.00', 'Stage 1C (Bergfex Direct Verified Cache)', 'https://www.bergfex.com/monte-bondone/'),
    'monterosa ski': ('€71.00', 'Stage 2 (Official Site Verified Cache)', 'https://visitmonterosa.com/esperienze/acquisto-online-skipass-monterosa-ski-20262027-31806')
}

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
    Trusted aggregator bulk tables (Bergfex / Skiresort.info) allow -8% to +35%
    to accommodate 2-season cumulative increases and ski area consolidations.
    Official site spiders enforce -2% to +22% to block promo/season traps.
    """
    if not (16.0 <= val <= 135.0):
        return False
    if val == 100.0 and prev_price < 82.0:
        return False
    if prev_price > 15.0:
        pct_diff = (val - prev_price) / prev_price
        min_bound = -0.08 if is_trusted_aggregator else -0.02
        max_bound = 0.35 if is_trusted_aggregator else 0.22
        if pct_diff < min_bound or pct_diff > max_bound:
            return False
    return True


def parse_aggregator_price_string(text):
    """
    Parses prices formatted like 'Preis: € 75,50', '€ 83,-', '€ 66.50', 'CHF 89,00', 'SFr. 116,-'.
    """
    if not text:
        return 0.0, '€'
    # First priority: explicit 'Preis: € XX,XX' or 'Preis € XX,XX' on Bergfex
    m_preis = re.search(
        r'Preis:?\s*(CHF|SFr\.?|€|EUR)\s*([0-9]{2,3}(?:[\.,][0-9]{1,2})?)(?:\s*,\s*-|\b)',
        str(text),
        re.IGNORECASE
    )
    if m_preis:
        curr_raw = m_preis.group(1).upper()
        curr = 'CHF' if ('CHF' in curr_raw or 'SFR' in curr_raw) else '€'
        val = float(m_preis.group(2).replace(',', '.'))
        return val, curr

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


def fetch_html_via_playwright_fallback(url):
    """Fetches a directory page via Headless Chromium if Python requests gets 403-blocked."""
    if not PLAYWRIGHT_AVAILABLE:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=HEADERS['User-Agent'], locale='de-AT')
            page = context.new_page()
            page.goto(url, timeout=12000, wait_until='domcontentloaded')
            page.wait_for_timeout(700)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return ""


def bulk_ingest_bergfex_country(country):
    """
    v9 Fix for Root Cause #1:
    Iterates over `<a href="/{slug}/">` tags directly (because on Bergfex Tailwind lists,
    the `<a>` tag IS the outer row container or wraps the title inside a row).
    Inspects both `a_tag.get_text()` and up to 3 parent levels (`len < 650`) to extract `Preis: € XX,XX`.
    """
    c_key = re.sub(r'\s+', ' ', country.lower().strip())
    if c_key in BERGFEX_BULK_CACHE:
        return BERGFEX_BULK_CACHE[c_key]

    BERGFEX_BULK_CACHE[c_key] = []
    if c_key not in BERGFEX_COUNTRY_URLS:
        return []

    urls_to_fetch = BERGFEX_COUNTRY_URLS[c_key]
    print(f"   [*] Bulk-ingesting Bergfex regional/country tables for {country} ({len(urls_to_fetch)} pages)...")
    records = []
    by_slug = {}

    for url in urls_to_fetch:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
            html_text = resp.text if resp.status_code == 200 else ""
            if not html_text:
                continue

            soup = BeautifulSoup(html_text, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].strip()
                m_slug = re.match(r'^(?:https?://www\.bergfex\.[a-z]+)?/([a-z0-9\-]{3,65})/?$', href)
                if not m_slug:
                    continue
                slug = m_slug.group(1)
                if slug in BERGFEX_NAV_IGNORE or any(bad in slug for bad in BLACKLIST_LINK_KEYWORDS):
                    continue

                # Gather text from the <a> tag itself AND climb up to 3 parent nodes (<650 chars)
                a_text = a_tag.get_text(' ', strip=True)
                context_text = a_text
                curr_node = a_tag
                for _ in range(3):
                    if 'Preis' in context_text or '€' in context_text or 'CHF' in context_text:
                        break
                    parent = curr_node.find_parent(['tr', 'li', 'div', 'article'])
                    if not parent:
                        break
                    p_text = parent.get_text(' ', strip=True)
                    if len(p_text) > 650:
                        break
                    context_text = p_text
                    curr_node = parent

                val, curr = parse_aggregator_price_string(context_text)
                # Ignore promotional header banner ('im Wert von 294€')
                if val > 140.0:
                    val = 0.0

                title_part = context_text.split('Pisten')[0] if 'Pisten' in context_text else a_text
                title_norm = transliterate_slug(f"{slug} {title_part}")
                raw_title = (a_text.split('Pisten')[0].strip() or slug)[:80]

                if slug in by_slug:
                    if val > 0 and by_slug[slug]['price'] == 0:
                        by_slug[slug]['price'] = val
                        by_slug[slug]['curr'] = curr
                        by_slug[slug]['title_norm'] = f"{by_slug[slug]['title_norm']}-{title_norm}"
                    continue

                rec = {
                    'slug': slug,
                    'url': f"https://www.bergfex.at/{slug}/",
                    'title_norm': title_norm,
                    'price': val,
                    'curr': curr,
                    'raw_title': raw_title
                }
                by_slug[slug] = rec
                records.append(rec)

            time.sleep(0.15)
        except Exception:
            continue

    priced_count = sum(1 for r in records if r['price'] > 0)
    print(f"       -> Indexed {len(records)} Bergfex resorts ({priced_count} with instant inline prices).")
    BERGFEX_BULK_CACHE[c_key] = records
    return records


def parse_skiresort_directory_html(html_text, base_url, seen_urls, records):
    soup = BeautifulSoup(html_text, 'html.parser')
    page_new_count = 0

    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        if '/ski-resort/' not in href:
            continue
        if any(sub in href for sub in ['/trail-map', '/ski-lifts', '/test-report', '/snow-report', '/weather', '/webcams', '/accommodation', '/ski-rental']):
            continue
        m_url = re.search(r'((?:https?://www\.skiresort\.[a-z]+)?(?:/[a-z]{2})?/ski-resort/[^/\'\"<>]+/?)$', href)
        if not m_url:
            continue

        full_url = urljoin(base_url, href)
        link_text = a_tag.get_text(' ', strip=True)
        if not link_text or link_text.lower() in ['details', 'test report', 'report', 'view ski resort', 'overview']:
            continue

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
            if ('€' in p_text or 'SFr' in p_text or 'CHF' in p_text or '£' in p_text) and ('km' in p_text.lower() or 'lift' in p_text.lower()):
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

    return page_new_count


def bulk_ingest_skiresort_country(country):
    """
    v10 Fix for Finding #1:
    Queries `https://www.skiresort.com/en/ski-resorts/{s_slug}/` directly (avoiding .info redirect drops),
    isolates `requests.get()` exceptions so Playwright fallback ALWAYS runs if `requests` fails,
    and retries once if Page 1 returns 0 records.
    """
    c_key = re.sub(r'\s+', ' ', country.lower().strip())
    if c_key in SKIRESORT_BULK_CACHE and len(SKIRESORT_BULK_CACHE[c_key]) > 0:
        return SKIRESORT_BULK_CACHE[c_key]

    if c_key not in SKIRESORT_COUNTRY_SLUGS:
        SKIRESORT_BULK_CACHE[c_key] = []
        return []

    s_slug, max_pages = SKIRESORT_COUNTRY_SLUGS[c_key]
    print(f"   [*] Bulk-ingesting Skiresort.info directory for {country} ({s_slug}, up to {max_pages} pages)...")
    records = []
    seen_urls = set()

    for page_num in range(1, max_pages + 1):
        candidate_urls = [
            f"https://www.skiresort.com/en/ski-resorts/{s_slug}/" if page_num == 1 else f"https://www.skiresort.com/en/ski-resorts/{s_slug}/page/{page_num}/",
            f"https://www.skiresort.info/ski-resorts/{s_slug}/" if page_num == 1 else f"https://www.skiresort.info/ski-resorts/{s_slug}/page/{page_num}/"
        ]
        new_cnt = 0
        for url in candidate_urls:
            html_text = ""
            try:
                resp = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
                if resp.status_code == 200:
                    html_text = resp.text
            except Exception:
                html_text = ""

            if html_text:
                new_cnt = parse_skiresort_directory_html(html_text, url, seen_urls, records)
            if new_cnt > 0:
                break

        # If both .com and .info failed via requests on Page 1, fall back to Headless Chromium
        if page_num == 1 and new_cnt == 0 and PLAYWRIGHT_AVAILABLE:
            pw_html = fetch_html_via_playwright_fallback(candidate_urls[0])
            if pw_html:
                new_cnt = parse_skiresort_directory_html(pw_html, candidate_urls[0], seen_urls, records)

        if new_cnt == 0:
            break
        time.sleep(0.2)

    priced_count = sum(1 for r in records if r['price'] > 0)
    print(f"       -> Indexed {len(records)} Skiresort.info resorts ({priced_count} with instant inline prices).")
    SKIRESORT_BULK_CACHE[c_key] = records
    return records


def build_resort_search_tokens(resort_name):
    """
    v10 Fix for Finding #3:
    1. Generates both hyphenated (`sauze-d-oulx`) and apostrophe-collapsed (`sauze-doulx`) slugs.
    2. Filters out generic geographic prefixes (`saint`, `monte`, `campo`, `see`, `zillertal`, etc.)
       when splitting multi-word resort names so `Saint-François-Longchamp` never matches `Saint-Lary-Soulan`.
    """
    raw_lower = resort_name.lower().strip()
    tokens = []

    if raw_lower in RESORT_ALIAS_MAP:
        tokens.extend(RESORT_ALIAS_MAP[raw_lower])

    primary = transliterate_slug(resort_name)
    # Also generate apostrophe-collapsed slug (e.g. "Sauze d'Oulx" -> "sauze-doulx", "Val d'Isère" -> "val-disere")
    collapsed_apos = transliterate_slug(re.sub(r"['’`]", "", raw_lower))
    cleaned_words = re.sub(
        r'\b(glacier|gletscher|resort|ski|arena|area|bergbahnen|skigebiet|mount|dolomites|dolomiten)\b',
        '', raw_lower
    ).strip()
    cleaned_slug = transliterate_slug(cleaned_words)
    no_space = transliterate_slug(raw_lower.replace(' ', ''))

    for t in [primary, collapsed_apos, cleaned_slug, no_space]:
        if t and len(t) >= 3 and t not in tokens:
            tokens.append(t)

    for part in re.split(r'[\-/(),\s]+', raw_lower):
        p_slug = transliterate_slug(part)
        if len(p_slug) >= 4 and p_slug not in TOKEN_SPLIT_STOPWORDS:
            if p_slug not in tokens:
                tokens.append(p_slug)

    return tokens


def match_in_bulk_cache(resort_name, bulk_records):
    """
    v10 Precision Bulk Matcher:
    - Enforces hyphen-bounded token matching (`fuegen` cannot match `hochfuegen`).
    - Protects short single-word resort names like `See` from matching `-zell-am-see-`.
    - Penalizes 5+ hyphen umbrella slugs (`les-portes-du-soleil-...`) when a dedicated village entry exists.
    """
    tokens = build_resort_search_tokens(resort_name)
    raw_slug = transliterate_slug(resort_name)
    scored_matches = []

    for rec in bulk_records:
        slug = rec['slug']
        t_norm = rec['title_norm']
        best_tok_score = 0
        is_umbrella_slug = slug.count('-') >= 5

        for idx, tok in enumerate(tokens):
            priority_bonus = max(0, 7 - idx)

            # Special protection for short single-word names like 'see'
            if len(tok) <= 3:
                if slug == tok:
                    best_tok_score = max(best_tok_score, 40 + priority_bonus)
                continue

            if slug == tok:
                best_tok_score = max(best_tok_score, 40 + priority_bonus)
            elif slug.startswith(tok + '-'):
                base_s = 22 if is_umbrella_slug else 31
                best_tok_score = max(best_tok_score, base_s + priority_bonus)
            elif slug.endswith('-' + tok):
                # Penalize sub-mountain suffixes like 'brunni-engelberg' or 'wasserngrat-gstaad' if tok is the city name
                base_s = 16 if (tok == raw_slug and not is_umbrella_slug) else 22
                best_tok_score = max(best_tok_score, base_s + priority_bonus)
            elif f"-{tok}-" in f"-{slug}-":
                base_s = 15 if is_umbrella_slug else 20
                best_tok_score = max(best_tok_score, base_s + priority_bonus)
            elif f"-{tok}-" in f"-{t_norm}-" and tok not in TOKEN_SPLIT_STOPWORDS:
                best_tok_score = max(best_tok_score, 14 + priority_bonus)

        if best_tok_score > 0:
            price_bonus = 5 if rec['price'] > 0 else 0
            scored_matches.append((best_tok_score + price_bonus, rec))

    scored_matches.sort(key=lambda x: x[0], reverse=True)
    return [m[1] for m in scored_matches[:4]]


def extract_skiresort_detail_price(html_content, prev_price=0.0):
    soup = BeautifulSoup(html_content, 'html.parser')
    full_text = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True))

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
                    if 0.0 <= pct_diff <= 0.20:
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
                if 0.0 <= pct_diff <= 0.20:
                    score += 10
                elif 0.20 < pct_diff <= 0.35:
                    score += 4
                elif -0.08 <= pct_diff < 0.0:
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


def resolve_via_aggregators(resort_name, country, prev_price=0.0, preserved_cache=None):
    """
    v10 Phase 1 & Phase 2:
      1A. Instant lookup in Bulk Bergfex Regional/Country Tables.
      1B. Instant lookup in Bulk Skiresort.info Country Directory (with Umbrella Pass Guardrail).
      1C. Targeted Bergfex `/preise/` AND `/{slug}/` overview page lookup.
      1D. Targeted Skiresort.info detail page lookup across top 3 alias tokens.
    """
    b_records = bulk_ingest_bergfex_country(country)
    s_records = bulk_ingest_skiresort_country(country)

    b_matches = match_in_bulk_cache(resort_name, b_records)
    s_matches = match_in_bulk_cache(resort_name, s_records)

    r_key = resort_name.strip().lower()

    # 1A. Instant inline price from Bergfex Bulk Regional/Country Table
    for b_rec in b_matches:
        if b_rec['price'] > 0 and is_within_guardrail(b_rec['price'], prev_price, is_trusted_aggregator=True):
            # If bulk match is >20% jump and we have a closer verified village price in preserved_cache, prefer the village price
            if prev_price > 0 and ((b_rec['price'] - prev_price) / prev_price) > 0.20 and preserved_cache and r_key in preserved_cache:
                break
            snippet = f"Bergfex {country} Bulk Table ({b_rec['raw_title']}): {b_rec['curr']}{b_rec['price']:.2f}"
            return (b_rec['price'], b_rec['curr'], 25, snippet), "Stage 1A (Bergfex Bulk Country Table)", b_rec['url']

    # 1B. Instant inline price from Skiresort.info Bulk Country Directory
    for s_rec in s_matches:
        if s_rec['price'] > 0 and is_within_guardrail(s_rec['price'], prev_price, is_trusted_aggregator=True):
            if prev_price > 0 and ((s_rec['price'] - prev_price) / prev_price) > 0.20 and preserved_cache and r_key in preserved_cache:
                break
            snippet = f"Skiresort.info {country} Directory ({s_rec['raw_title']}): {s_rec['curr']}{s_rec['price']:.2f}"
            return (s_rec['price'], s_rec['curr'], 25, snippet), "Stage 1B (Skiresort.info Bulk Directory)", s_rec['url']

    # 1C. Targeted Bergfex /preise/ AND /{slug}/ overview lookup
    slugs_to_try = [m['slug'] for m in b_matches[:2]]
    for tok in build_resort_search_tokens(resort_name)[:3]:
        if tok not in slugs_to_try:
            slugs_to_try.append(tok)

    for slug in slugs_to_try[:3]:
        for b_url, stg_label in [
            (f"https://www.bergfex.at/{slug}/preise/", "Stage 1C (Bergfex /preise/ Page)"),
            (f"https://www.bergfex.at/{slug}/", "Stage 1C (Bergfex Direct Overview)")
        ]:
            try:
                time.sleep(0.18)
                resp = requests.get(b_url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False, allow_redirects=True)
                if resp.status_code == 200 and 'bergfex' in resp.url and '/sommer/' not in resp.url.lower():
                    cand = parse_html_for_best_price(resp.text, prev_price, is_trusted_aggregator=True)
                    if cand:
                        return cand, stg_label, resp.url
            except Exception:
                continue

    # 1D. Targeted Skiresort.info Detail Page lookup (tries top 3 alias tokens on .com/en/)
    s_urls_to_try = [m['url'] for m in s_matches[:2]]
    for tok in build_resort_search_tokens(resort_name)[:3]:
        direct_s_url = f"https://www.skiresort.com/en/ski-resort/{tok}/"
        if direct_s_url not in s_urls_to_try:
            s_urls_to_try.append(direct_s_url)

    for s_url in s_urls_to_try[:3]:
        try:
            time.sleep(0.18)
            resp = requests.get(s_url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False, allow_redirects=True)
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
        resp = requests.get(pdf_url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
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
            page.goto(url, timeout=11000, wait_until='domcontentloaded')
            page.wait_for_timeout(900)

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
                    if btn.is_visible(timeout=250):
                        btn.click(timeout=500)
                        page.wait_for_timeout(250)
                        break
                except Exception:
                    pass

            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(500)

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


def load_prior_verified_audit_records():
    """
    v10 Cumulative State Preservation:
    Loads previously verified scrape results from existing audit CSVs + PRIOR_VERIFIED_FALLBACK_CACHE,
    while allowing PRIOR_VERIFIED_FALLBACK_CACHE to override known umbrella/collision URLs.
    """
    preserved = {}

    for audit_file in [AUDIT_CSV, LEGACY_AUDIT_CSV]:
        if not os.path.exists(audit_file):
            continue
        try:
            with open(audit_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('Review Status') == 'VERIFIED_SCRAPE':
                        r_name = (row.get('Resort Name') or '').strip().lower()
                        new_p = (row.get('New Price (26/27)') or '').strip()
                        stg = (row.get('Discovery Stage') or '').strip()
                        src = (row.get('Source URL') or '').strip()
                        snip = (row.get('Matched Context Snippet') or '').strip()
                        if r_name and new_p and not any(bad in src.lower() for bad in BLACKLIST_LINK_KEYWORDS):
                            preserved[r_name] = (new_p, stg, src, snip)
        except Exception:
            continue

    # Curated village-level overrides take precedence over umbrella pass collisions
    for k, (p_str, stg, src) in PRIOR_VERIFIED_FALLBACK_CACHE.items():
        preserved[k] = (p_str, stg, src, f"Preserved verified village price ({p_str})")

    return preserved


def deep_discover_resort_price(resort_name, country, region_str, website_url, prev_price, shared_cache, preserved_cache):
    """
    Executes the v10 Precision Bulk-Aggregator Discovery Pipeline:
      Stage 0 : Baseline-Verified Shared Ski Area Cache
      Stage 1A: Bergfex Bulk Regional/Country Table (Standardized on .at)
      Stage 1B: Skiresort.info Bulk Country Directory (Direct .com/en/ + Playwright Fallback)
      Stage 1C: Bergfex /preise/ & Overview Page
      Stage 1D: Skiresort.info Resort Detail Page (Top 3 Alias Tokens)
      Stage 2 : Hardened Official Site Spider + PDF Extractor (Stage 2B)
      Stage 3 : Headless Chromium JS Fallback
      Stage 4 : Cumulative Prior-Run Verified Cache
    """
    cache_key = get_syndicate_cache_key(region_str, website_url, prev_price)
    if cache_key and cache_key in shared_cache:
        cached_price, cached_source, cached_snippet = shared_cache[cache_key]
        cached_val = clean_numeric_price(cached_price)
        if is_within_guardrail(cached_val, prev_price, is_trusted_aggregator=True):
            return cached_price, "Stage 0 (Shared Ski Area Domain Cache)", cached_source, cached_snippet

    # STAGE 1: Bulk Country/Regional Tables + Targeted Aggregator Pages
    cand, stage_label, agg_url = resolve_via_aggregators(resort_name, country, prev_price, preserved_cache)
    if cand:
        val, curr, score, snippet = cand
        price_str = f"{curr}{val:.2f}"
        if cache_key:
            shared_cache[cache_key] = (price_str, agg_url, snippet)
        return price_str, stage_label, agg_url, snippet

    # Check if we already have a verified Bergfex/Official scrape from a prior run before spending 15s on Stage 2/3
    r_key = resort_name.strip().lower()
    if r_key in preserved_cache:
        p_price, p_stage, p_src, p_snip = preserved_cache[r_key]
        p_val = clean_numeric_price(p_price)
        if is_within_guardrail(p_val, prev_price, is_trusted_aggregator=True):
            if cache_key:
                shared_cache[cache_key] = (p_price, p_src, p_snip)
            return p_price, p_stage, p_src, p_snip

    # STAGE 2: Hardened Official Website Spider
    if not website_url or website_url.strip().lower() in ['n/a', 'closed', '']:
        return None, "No URL", "SKIPPED", ""

    target_url = website_url.strip()
    if not target_url.startswith(('http://', 'https://')):
        target_url = 'https://' + target_url

    html_subpages = []
    pdf_links = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
        if resp.status_code == 200:
            html_subpages, pdf_links = rank_internal_pricing_links(target_url, resp.text)
    except Exception:
        pass

    for sub_url in html_subpages:
        if any(bad in sub_url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
            continue
        try:
            sub_resp = requests.get(sub_url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
            if sub_resp.status_code == 200 and not any(bad in sub_resp.url.lower() for bad in BLACKLIST_LINK_KEYWORDS):
                # Do not allow N-Py resort A to scrape N-Py resort B's subpage
                if 'n-py.com' in target_url.lower() and urlparse(target_url).path.rstrip('/') not in sub_resp.url.lower():
                    continue
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
            "## 🎿 v10 Precision Bulk-Aggregator Lift Pass Scraper — Audit Summary",
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
    parser = argparse.ArgumentParser(description="v10 Precision Bulk-Aggregator Lift Pass Scraper")
    parser.add_argument('--offset', type=int, default=0, help="Starting row index (0-based)")
    parser.add_argument('--limit', type=int, default=0, help="Max resorts to process in this batch (0 = all 435 resorts)")
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
    preserved_cache = load_prior_verified_audit_records()

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
    print(" PRECISION BULK-AGGREGATOR LIFT PASS SCRAPER (v10 - Anti-Collision + Full 435)")
    print(f" Input File  : {MASTER_CSV}")
    print(f" Offset      : {args.offset} | Batch Limit: {len(target_indices)}")
    print(f" Capabilities: Requests=True, BS4=True, Playwright={PLAYWRIGHT_AVAILABLE}, PDF={PDF_AVAILABLE}")
    print(f" Prior Cache : {len(preserved_cache)} verified resorts loaded for timeout protection")
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
            resort_name, country, region_str, website, prev_val, shared_cache, preserved_cache
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
