import argparse
import csv
import os
import re
import sys
import time
import unicodedata

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

MASTER_CSV = 'Ski resorts - Master.csv'
OUTPUT_CSV = 'Ski resorts - Master with Characteristics.csv'
AUDIT_CSV = 'resort_characteristics_audit.csv'
REQ_TIMEOUT = (4.0, 6.0)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-GB,en;q=0.9,de;q=0.8'
}

# ==============================================================================
# CURATED EUROPEAN SKI RESORT KNOWLEDGE BASE (ALL 435 RESORTS)
# ==============================================================================

# 1. Resorts with a verified Half-Pipe / Superpipe
HALF_PIPE_RESORTS = {
    'laax', 'avoriaz', 'tignes', 'livigno', 'crans-montana', 'zermatt',
    'saas-fee', 'stubai glacier', 'kitzsteinhorn - kaprun', 'mayrhofen',
    'les deux alpes', 'davos klosters', 'kühtai', 'ischgl', 'st. moritz',
    'grindelwald-wengen', 'flachau', 'serfaus-fiss-ladis', 'alpe d\'huez',
    'val thorens', 'les arcs', 'la plagne', 'chamonix', 'courchevel',
    'méribel', 'font-romeu', 'sierra nevada', 'baqueira-beret', 'tryvann',
    'hemsedal', 'trysil', 'åre', 'ruka', 'levi', 'špindlerův mlýn',
    'feldberg', 'oberstdorf', 'sölden', 'silvretta montafon', 'damüls-mellau'
}

# 2. Resorts with Major Snowparks / Terrain Parks
SNOWPARK_RESORTS = HALF_PIPE_RESORTS | {
    'les gets', 'morzine', 'châtel', 'flaine', 'samoëns', 'les menuires',
    'val d\'isère', 'serre chevalier', 'vars', 'risoul 1850', 'isola 2000',
    'les 7 laux', 'la clusaz', 'le grand bornand', 'chamrousse', 'les orres',
    'montgenèvre', 'valmorel', 'grand tourmalet', 'saint-lary-soulan',
    'cauterets', 'pyrenees', 'grandvalira', 'vallnord', 'formigal', 'cerler',
    'la molina', 'masella', 'saalbach', 'hinterglemm', 'leogang', 'fieberbrunn',
    'zell am ziller', 'gerlos', 'königsleiten', 'zillertal arena',
    'hochzillertal-hochfügen', 'schladming', 'reiteralm', 'obertauern',
    'nassfeld', 'bad gastein', 'hochkönig', 'steinplatte-waidring',
    'kitzbühel', 'westendorf', 'ellmau', 'söll', 'st. anton am arlberg',
    'lech zürs', 'axamer lizum', 'hochoetz', 'hochzeiger', 'nauders',
    'kaunertal glacier', 'pitztal glacier', 'hintertux glacier', 'kreischberg',
    'turracher höhe', 'gerlitzen', 'katschberg-aineck', 'diedamskopf',
    'brandnertal', 'golm', 'gargellen', 'alpbach', 'kronplatz', 'alta badia',
    'val gardena', 'obereggen', 'alpe lusia / san pellegrino', 'val di fassa',
    'canazei', 'madonna di campiglio', 'passo tonale', 'bormio', 'bardonecchia',
    'sestriere', 'sauze d\'oulx', 'cervinia', 'pila', 'prato nevoso',
    'mondolè ski (prato nevoso)', '3 zinnen dolomites', 'carezza', 'plose',
    'schöneben-haideralm', 'verbier', 'nendaz', 'veysonnaz', 'thyon',
    'adelboden-lenk', 'lenk', 'arosa', 'flumserberg', 'hoch-ybrig',
    'engelberg', 'andermatt', 'melchsee-frutt', 'scuol', 'savognin',
    'villars-gryon', 'leysin', 'les diablerets', 'val d\'anniviers',
    'grimentz', 'brauneck', 'spitzingsee-tegernsee', 'sudelfeld', 'winterberg',
    'großer arber', 'klínovec', 'pec pod sněžkou', 'jasná', 'donovaly',
    'białka tatrzańska', 'szczyrk', 'vogel', 'krvavec', 'kranjska gora',
    'jahorina', 'kopaonik', 'bansko', 'borovets', 'pamporovo', 'gudauri',
    'bukovel', 'geilo', 'hafjell', 'kvitfjell', 'myrkdalen', 'sälen',
    'idre fjäll', 'branäs', 'vemdalen', 'stöten', 'ylläs'
}

# 3. Glacier Skiing Resorts
GLACIER_RESORTS = {
    'hintertux glacier', 'stubai glacier', 'sölden', 'pitztal glacier',
    'kaunertal glacier', 'kitzsteinhorn - kaprun', 'mölltal glacier',
    'dachstein krippenstein', 'schladming', 'weisssee gletscherwelt',
    'tignes', 'val d\'isère', 'les deux alpes', 'la grave', 'chamonix',
    'val thorens', 'les arcs', 'zermatt', 'saas-fee', 'engelberg',
    'crans-montana', 'diavolezza', 'st. moritz', 'andermatt', 'glacier 3000',
    'les diablerets', 'cervinia', 'passo tonale', 'schnalstal',
    'arabba / marmolada', 'sulden am ortler', 'bormio', 'folgefonna', 'stranda'
}

# 4. Famous Lively Après-Ski Hubs
LIVELY_APRES_RESORTS = {
    'ischgl', 'st. anton am arlberg', 'lech zürs', 'sölden', 'saalbach',
    'hinterglemm', 'mayrhofen', 'flachau', 'wagrain', 'schladming',
    'obertauern', 'kitzbühel', 'kirchberg', 'westendorf', 'söll', 'ellmau',
    'gerlos', 'zell am ziller', 'zillertal arena', 'bad gastein',
    'serfaus-fiss-ladis', 'nassfeld', 'zell am see', 'kaprun',
    'kitzsteinhorn - kaprun', 'hochzillertal-hochfügen', 'fügen - spieljoch',
    'val d\'isère', 'tignes', 'val thorens', 'méribel', 'courchevel',
    'les deux alpes', 'alpe d\'huez', 'chamonix', 'avoriaz', 'morzine',
    'les gets', 'les arcs', 'la clusaz', 'serre chevalier', 'vars',
    'risoul 1850', 'grandvalira', 'verbier', 'zermatt', 'davos klosters',
    'laax', 'st. moritz', 'grindelwald-wengen', 'crans-montana', 'engelberg',
    'arosa', 'saas-fee', 'livigno', 'madonna di campiglio', 'selva',
    'val gardena', 'canazei', 'val di fassa', 'alta badia', 'cortina d\'ampezzo',
    'cervinia', 'sauze d\'oulx', 'sestriere', 'bormio', 'passo tonale',
    'kronplatz', 'ischgl', 'winterberg', 'garmisch-partenkirchen',
    'bansko', 'borovets', 'špindlerův mlýn', 'jasná', 'zakopane',
    'białka tatrzańska', 'kranjska gora', 'baqueira-beret', 'formigal',
    'sierra nevada', 'åre', 'hemsedal', 'trysil', 'levi', 'ruka',
    'gudauri', 'bukovel', 'kopaonik', 'jahorina', 'poiana brasov'
}

# 5. Ski-in / Ski-out Convenience Resorts
SKI_IN_SKI_OUT_RESORTS = {
    'avoriaz', 'val thorens', 'les menuires', 'st martin de belleville',
    'méribel', 'courchevel', 'la plagne', 'les arcs', 'peisey-vallandry',
    'tignes', 'val d\'isère', 'flaine', 'les carroz', 'alpe d\'huez',
    'les deux alpes', 'isola 2000', 'auron', 'valmorel', 'saint-françois-longchamp',
    'la rosière', 'les saisies', 'les sybelles', 'orcières merlette',
    'superbesse', 'le lioran', 'les orres', 'risoul 1850', 'vars',
    'montgenèvre', 'valfréjus', 'val cenis', 'bonneval-sur-arc', 'sainte-foy',
    'les karellis', 'praloup', 'val d\'allos (la foux)', 'valberg',
    'peyragudes', 'piau engaly', 'grand tourmalet', 'la pierre saint-martin',
    'gourette', 'obertauern', 'kühtai', 'hochgurgl', 'obergurgl-hochgurgl',
    'damüls-mellau', 'faschina-fontanella', 'warth-schröcken', 'lech zürs',
    'gargellen', 'silvretta montafon', 'serfaus-fiss-ladis', 'hochfügen',
    'hochzillertal-hochfügen', 'königsleiten', 'hochkrimml', 'zauchensee',
    'tauplitz', 'turracher höhe', 'katschberg-aineck', 'nassfeld',
    'bad kleinkirchheim', 'gerlitzen', 'hochrindl', 'klippitztörl',
    'weinebene', 'lermoos', 'berwang', 'alpbach', 'auffach', 'saalbach',
    'hinterglemm', 'leogang', 'fieberbrunn', 'ladis', 'cervinia',
    'sestriere', 'sansicario', 'claviere', 'pragelato', 'la thuile',
    'pila', 'livigno', 'passo tonale', 'folgarida - marilleva',
    'madonna di campiglio', 'arabba / marmolada', 'alta badia', 'val gardena',
    'canazei', 'obereggen', 'carezza', 'alpe di siusi', 'monte bondone',
    'prato nevoso', 'mondolè ski (prato nevoso)', 'abetone', 'roccaraso',
    'aletsch arena', 'mürren', 'wengen', 'grindelwald-wengen', 'zermatt',
    'saas-fee', 'arosa', 'lenzerheide', 'laax', 'davos klosters',
    'andermatt', 'melchsee-frutt', 'stoos', 'hoch-ybrig', 'flumserberg',
    'braunwald', 'crans-montana', 'anzère', 'nendaz', 'veysonnaz', 'thyon',
    'verbier', 'val d\'anniviers', 'grimentz', 'villars-gryon', 'samnaun',
    'malbun', 'grandvalira', 'ordino arcalis', 'vallnord', 'baqueira-beret',
    'sierra nevada', 'formigal', 'cerler', 'astún', 'candanchú', 'boí taüll',
    'trysil', 'hemsedal', 'hafjell', 'kvitfjell', 'myrkdalen', 'geilo',
    'sälen', 'idre fjäll', 'åre', 'branäs', 'vemdalen', 'stöten',
    'levi', 'ruka', 'ylläs', 'jasná', 'poiana brasov', 'kopaonik',
    'jahorina', 'gudauri', 'bukovel'
}

# 6. Challenging Runs & Steep Terrain
CHALLENGING_RESORTS = {
    'st. anton am arlberg', 'lech zürs', 'warth-schröcken', 'ischgl',
    'sölden', 'kitzbühel', 'mayrhofen', 'hintertux glacier', 'stubai glacier',
    'pitztal glacier', 'kaunertal glacier', 'kitzsteinhorn - kaprun',
    'obergurgl-hochgurgl', 'serfaus-fiss-ladis', 'silvretta montafon',
    'gargellen', 'golm', 'sonnenkopf', 'damüls-mellau', 'diedamskopf',
    'schladming', 'reiteralm', 'zauchensee', 'bad gastein', 'obertauern',
    'saalbach', 'hinterglemm', 'fieberbrunn', 'leogang', 'zell am see',
    'nassfeld', 'bad kleinkirchheim', 'grossglockner resort kals-matrei',
    'heiligenblut', 'ankogel-mallnitz', 'mölltal glacier', 'lienz',
    'dachstein krippenstein', 'axamer lizum', 'nordkette', 'st. jakob im defereggental',
    'hochzillertal-hochfügen', 'zillertal arena', 'gerlos', 'alpbach',
    'wildkogel', 'tauplitz', 'chamonix', 'la grave', 'val d\'isère',
    'tignes', 'val thorens', 'les menuires', 'méribel', 'courchevel',
    'les arcs', 'la plagne', 'peisey-vallandry', 'alpe d\'huez',
    'les deux alpes', 'serre chevalier', 'avoriaz', 'flaine', 'la clusaz',
    'le grand bornand', 'megève', 'les contamines-montjoie', 'sainte-foy',
    'bonneval-sur-arc', 'valmorel', 'les 7 laux', 'chamrousse', 'isola 2000',
    'auron', 'vars', 'risoul 1850', 'montgenèvre', 'grand tourmalet',
    'pic du midi', 'gourette', 'cauterets', 'luz-ardiden', 'piau engaly',
    'saint-lary-soulan', 'verbier', 'zermatt', 'saas-fee', 'engelberg',
    'andermatt', 'mürren', 'grindelwald-wengen', 'laax', 'davos klosters',
    'st. moritz', 'arosa', 'crans-montana', 'nendaz', 'veysonnaz', 'thyon',
    'val d\'anniviers', 'diablerets', 'les diablerets', 'adelboden-lenk',
    'flumserberg', 'pizol', 'scuol', 'splügen-tambo', 'disentis 3000',
    'lauchernalp', 'arolla', 'arabba / marmolada', 'cortina d\'ampezzo',
    'val gardena', 'alta badia', 'kronplatz', '3 zinnen dolomites',
    'madonna di campiglio', 'passo tonale', 'bormio', 'santa caterina valfurva',
    'livigno', 'madesimo', 'cervinia', 'courmayeur', 'la thuile',
    'monterosa ski', 'sestriere', 'sauze d\'oulx', 'bardonecchia',
    'limone piemonte', 'pila', 'ravascletto-zoncolan', 'tarvisio',
    'sella nevea', 'schnalstal', 'sulden am ortler', 'klausberg',
    'speikboden', 'ratschings-jaufen', 'gitschberg jochtal', 'plose',
    'campo imperatore', 'roccaraso', 'garmisch-partenkirchen', 'oberstdorf',
    'jenner', 'kampenwand', 'feldberg', 'grandvalira', 'ordino arcalis',
    'baqueira-beret', 'boí taüll', 'cerler', 'formigal', 'astún',
    'candanchú', 'sierra nevada', 'la molina', 'masella', 'jasná',
    'tatranská lomnica', 'zakopane', 'szczyrk', 'krvavec', 'kanin',
    'vogel', 'bansko', 'borovets', 'kopaonik', 'jahorina', 'bjelasnica',
    'gudauri', 'bukovel', 'åre', 'riksgränsen', 'hemsedal', 'narvik',
    'strandafjellet', 'myrkdalen', 'kvitfjell', 'tryvann', 'glencoe',
    'nevis range'
}

# 7. Renowned Freeride & Off-Piste Meccas
FREERIDE_RESORTS = {
    'st. anton am arlberg', 'lech zürs', 'warth-schröcken', 'sonnenkopf',
    'ischgl', 'kappl', 'galtür', 'sölden', 'obergurgl-hochgurgl',
    'fieberbrunn', 'saalbach', 'hinterglemm', 'leogang', 'kitzbühel',
    'dachstein krippenstein', 'tauplitz', 'loser - altaussee', 'planneralm',
    'hohentauern', 'sportgastein', 'bad gastein', 'zauchensee', 'obertauern',
    'kitzsteinhorn - kaprun', 'weißsee gletscherwelt', 'weisssee gletscherwelt',
    'grossglockner resort kals-matrei', 'heiligenblut', 'ankogel-mallnitz',
    'mölltal glacier', 'st. jakob im defereggental', 'hintertux glacier',
    'stubai glacier', 'pitztal glacier', 'kaunertal glacier', 'kühtai',
    'axamer lizum', 'nordkette', 'hochfügen', 'hochzillertal-hochfügen',
    'mayrhofen', 'alpbach', 'auffach', 'damüls-mellau', 'diedamskopf',
    'gargellen', 'silvretta montafon', 'chamonix', 'la grave', 'val d\'isère',
    'tignes', 'val thorens', 'les arcs', 'peisey-vallandry', 'la plagne',
    'sainte-foy', 'la rosière', 'les deux alpes', 'alpe d\'huez',
    'serre chevalier', 'montgenèvre', 'la clusaz', 'flaine', 'avoriaz',
    'les 7 laux', 'pralognan-la-vanoise', 'bonneval-sur-arc', 'isola 2000',
    'vars', 'grand tourmalet', 'piau engaly', 'cauterets', 'luz-ardiden',
    'verbier', 'nendaz', 'veysonnaz', 'thyon', 'zermatt', 'saas-fee',
    'engelberg', 'andermatt', 'disentis 3000', 'laax', 'davos klosters',
    'st. moritz', 'arosa', 'splügen-tambo', 'lauchernalp', 'arolla',
    'val d\'anniviers', 'grimentz', 'mürren', 'les diablerets', 'pizol',
    'elm', 'scuol', 'bivio', 'vals 3000', 'monterosa ski', 'courmayeur',
    'la thuile', 'cervinia', 'arabba / marmolada', 'cortina d\'ampezzo',
    'selva', 'val gardena', 'alta badia', 'canazei', 'val di fassa',
    'passo tonale', 'livigno', 'bormio', 'santa caterina valfurva',
    'madesimo', 'sestriere', 'sauze d\'oulx', 'pragelato', 'limone piemonte',
    'sella nevea', 'kanin', 'sulden am ortler', 'schnalstal',
    'campo imperatore', 'etna nord - piano provenzana', 'ordino arcalis',
    'grandvalira', 'baqueira-beret', 'boí taüll', 'cerler', 'formigal',
    'astún', 'candanchú', 'riksgränsen', 'narvik', 'strandafjellet',
    'myrkdalen', 'hemsedal', 'åre', 'gudauri', 'bansko', 'jasná',
    'zakopane', 'tetnuldi', 'glencoe', 'nevis range'
}

# 8. Charming Traditional Alpine Villages
CHARMING_VILLAGE_RESORTS = {
    'alpbach', 'auffach', 'niederau', 'ellmau', 'ellmau - going', 'söll',
    'westendorf', 'brixen im thale', 'hopfgarten', 'kitzbühel', 'st. johann in tirol',
    'fieberbrunn', 'filzmoos', 'maria alm', 'hochkönig', 'wagrain',
    'radstadt-altenmarkt', 'schladming', 'ramsau', 'tauplitz', 'altaussee',
    'loser - altaussee', 'hallstatt', 'dachstein krippenstein', 'gosau',
    'dachstein west', 'annaberg', 'werfenweng', 'rauris', 'zell am see',
    'bad gastein', 'bad kleinkirchheim', 'heiligenblut', 'lienz',
    'kals', 'grossglockner resort kals-matrei', 'st. jakob im defereggental',
    'sillian-hochpustertal', 'obsteig', 'seefeld - rosshütte', 'lermoos',
    'ehrwald', 'berwang', 'st. anton am arlberg', 'lech zürs', 'warth-schröcken',
    'galtür', 'ischgl', 'kappl', 'see', 'serfaus-fiss-ladis', 'nauders',
    'mayrhofen', 'zell am ziller', 'gerlos', 'damüls-mellau', 'schoppernau',
    'diedamskopf', 'brandnertal', 'gargellen', 'schruns', 'silvretta montafon',
    'turracher höhe', 'weissensee', 'megève', 'chamonix', 'les contamines-montjoie',
    'la clusaz', 'le grand bornand', 'samoëns', 'les gets', 'morzine',
    'châtel', 'bonneval-sur-arc', 'val d\'isère', 'st martin de belleville',
    'valloire', 'val cenis', 'pralognan-la-vanoise', 'aussois', 'sainte-foy',
    'serre chevalier', 'montgenèvre', 'cauterets', 'saint-lary-soulan',
    'le mont-dore', 'la bresse-hohneck', 'zermatt', 'saas-fee', 'grächen',
    'wengen', 'mürren', 'grindelwald-wengen', 'adelboden-lenk', 'lenk',
    'gstaad', 'kandersteg', 'meiringen-hasliberg', 'engelberg', 'andermatt',
    'klosters', 'davos klosters', 'st. moritz', 'pontresina', 'scuol',
    'arosa', 'brigels (breil)', 'disentis 3000', 'savognin', 'splügen-tambo',
    'elm', 'braunwald', 'val d\'anniviers', 'grimentz', 'zinal', 'arolla',
    'champex-lac', 'Verbier', 'villars-gryon', 'les diablerets', 'champery',
    'morgins', 'samnaun', 'malbun', 'cortina d\'ampezzo', 'val gardena',
    'alta badia', 'canazei', 'val di fassa', 'arabba / marmolada',
    '3 zinnen dolomites', 'kronplatz', 'madonna di campiglio', 'bormio',
    'livigno', 'courmayeur', 'la thuile', 'gressoney', 'alagna',
    'monterosa ski', 'bardonecchia', 'sauze d\'oulx', 'limone piemonte',
    'madesimo', 'aprica', 'santa caterina valfurva', 'cavalese',
    'val di fiemme', 'obereggen', 'carezza', 'meran 2000', 'ratschings-jaufen',
    'vipiteno', 'monte cavallo - vipiteno', 'klausberg', 'speikboden',
    'gitschberg jochtal', 'plose', 'reinswald', 'sulden am ortler',
    'schnalstal', 'schwemmalm', 'ladurns', 'alleghe', 'civetta',
    'san martino di castrozza', 'folgaria - fiorentini', 'andalo - paganella',
    'tarvisio', 'ravascletto-zoncolan', 'roccaraso', 'garmisch-partenkirchen',
    'oberstdorf', 'reit im winkl', 'lenggries', 'brauneck', 'bayrischzell',
    'sudelfeld', 'spitzingsee-tegernsee', 'balderschwang', 'oberjoch',
    'berchtesgaden', 'jenner', 'baqueira-beret', 'cerler', 'sallent de gallego',
    'formigal', 'panticosa', 'zakopane', 'białka tatrzańska', 'kranjska gora',
    'bohinj', 'vogel', 'špindlerův mlýn', 'pec pod sněžkou', 'harrachov',
    'tatranská lomnica', 'bansko', 'sinaia', 'åre', 'geilo', 'hemsedal'
}

# 9. Tree-Lined Runs (Great Bad-Weather Visibility)
TREE_SKIING_RESORTS = {
    'schladming', 'reiteralm', 'fageralm', 'flachau', 'wagrain', 'st. johann',
    'radstadt-altenmarkt', 'zauchensee', 'filzmoos', 'hochkönig', 'bad gastein',
    'grossarl', 'werfenweng', 'annaberg', 'dachstein west', 'tauplitz',
    'loser - altaussee', 'riesneralm', 'galsterberg', 'kreischberg', 'lachtal',
    'grebenzen', 'turracher höhe', 'bad kleinkirchheim', 'gerlitzen',
    'nassfeld', 'katschberg-aineck', 'dreiländereck', 'simonhöhe', 'hochrindl',
    'klippitztörl', 'koralpe', 'weinebene', 'petzen', 'kitzbühel',
    'st. johann in tirol', 'steinplatte-waidring', 'st. ulrich-buchensteinwand',
    'fieberbrunn', 'saalbach', 'hinterglemm', 'leogang', 'zell am see',
    'ellmau', 'ellmau - going', 'söll', 'scheffau', 'westendorf',
    'brixen im thale', 'hopfgarten', 'alpbach', 'auffach', 'niederau',
    'zahmer kaiser', 'hochkössen', 'achensee - christlum', 'rofan',
    'kellerjoch', 'fügen - spieljoch', 'hochzillertal-hochfügen',
    'zell am ziller', 'mayrhofen', 'patscherkofel', 'muttereralm',
    'ranggerköpfl', 'schlick 2000', 'bergeralm', 'seefeld - rosshütte',
    'lermoos', 'fendels', 'serfaus-fiss-ladis', 'nauders', 'hochoetz',
    'hochzeiger', 'brandnertal', 'golm', 'montafon', 'silvretta montafon',
    'kleinwalsertal (kanzelwand - ifen)', 'diedamskopf', 'laterns - gapfohl',
    'hinterstoder', 'wurzeralm', 'kasberg', 'feuerkogel', 'hochficht',
    'hochkar', 'lackenhof am ötscher', 'gemeindealpe', 'semmering (stuhleck)',
    'semmering (hirschenkogel)', 'mönichkirchen-mariensee', 'serre chevalier',
    'montgenèvre', 'vars', 'risoul 1850', 'puy-saint-vincent', 'les orres',
    'praloup', 'val d\'allos (la foux)', 'auron', 'isola 2000', 'valberg',
    'megève', 'les contamines-montjoie', 'chamonix', 'les houches',
    'st-gervais', 'la clusaz', 'le grand bornand', 'les gets', 'morzine',
    'châtel', 'abondance', 'samoëns', 'les carroz', 'praz de lys - sommand',
    'les saisies', 'notre-dame-de-bellecombe', 'courchevel', 'méribel',
    'la Tania', 'peisey-vallandry', 'les arcs', 'la plagne', 'champagny',
    'sainte-foy', 'la rosière', 'valmorel', 'val cenis', 'aussois',
    'valfréjus', 'valloire', 'pralognan-la-vanoise', 'les 7 laux',
    'chamrousse', 'villard-de-lans', 'les rousses', 'métabief', 'lélex-crozet',
    'la bresse-hohneck', 'gérardmer', 'le lioran', 'le mont-dore', 'superbesse',
    'font-romeu', 'les angles', 'ax 3 domaines', 'guzet-neige', 'saint-lary-soulan',
    'peyragudes', 'cauterets', 'gourette', 'la pierre saint-martin',
    'madonna di campiglio', 'folgarida - marilleva', 'pinzolo', 'passo tonale',
    'ponte di legno', 'andalo - paganella', 'folgaria - fiorentini',
    'monte bondone', 'kronplatz', '3 zinnen dolomites', 'alta badia',
    'val gardena', 'obereggen', 'val di fiemme', 'carezza', 'alpe lusia / san Pellegrino',
    'canazei', 'val di fassa', 'civetta', 'cortina d\'ampezzo',
    'san martino di castrozza', 'gitschberg jochtal', 'plose', 'meran 2000',
    'reinswald', 'ratschings-jaufen', 'monte cavallo - vipiteno', 'ladurns',
    'klausberg', 'speikboden', 'schöneben-haideralm', 'watles', 'schwemmalm',
    'bormio', 'santa caterina valfurva', 'aprica', 'madesimo', 'pila',
    'courmayeur', 'la thuile', 'sauze d\'oulx', 'sestriere', 'bardonecchia',
    'pragelato', 'claviere', 'sansicario', 'limone piemonte',
    'mondolè ski (prato nevoso)', 'tarvisio', 'ravascletto-zoncolan',
    'abetone', 'roccaraso', 'camigliatello silano', 'garmisch-partenkirchen',
    'brauneck', 'spitzingsee-tegernsee', 'sudelfeld', 'oberstdorf', 'oberjoch',
    'balderschwang', 'reit im winkl', 'hocheck', 'kampenwand', 'jenner',
    'großer arber', 'feldberg', 'winterberg', 'oberwiesenthal', 'villars-gryon',
    'leysin', 'les diablerets', 'champery', 'morgins', 'crans-montana',
    'anzère', 'nendaz', 'veysonnaz', 'thyon', 'val d\'anniviers', 'grimentz',
    'zermatt', 'grächen', 'lauchernalp', 'gstaad', 'adelboden-lenk', 'lenk',
    'grindelwald-wengen', 'meiringen-hasliberg', 'sörenberg', 'melchsee-frutt',
    'engelberg', 'klewenalp-stockhütte', 'stoos', 'mythenregion', 'hoch-ybrig',
    'flumserberg', 'pizol', 'toggenburg', 'braunwald', 'elm', 'laax',
    'brigels (breil)', 'obersaxen', 'savognin', 'lenzerheide', 'arosa',
    'davos klosters', 'st. moritz', 'scuol', 'samnaun', 'airolo-pesciüm',
    'grandvalira', 'vallnord', 'baqueira-beret', 'formigal', 'panticosa',
    'cerler', 'la molina', 'masella', 'alto campoo', 'valgrande-pajares',
    'puerto de navacerrada', 'valdesquí', 'manzaneda', 'krvavec', 'rogla',
    'mariborsko pohorje', 'kranjska gora', 'cerkno', 'vogel', 'jasná',
    'donovaly', 'tatranská lomnica', 'kubínska hoľa', 'vrátna',
    'špindlerův mlýn', 'klínovec', 'pec pod sněžkou', 'harrachov', 'lipno',
    'zakopane', 'białka tatrzańska', 'szczyrk', 'bansko', 'borovets',
    'pamporovo', 'kopaonik', 'jahorina', 'bjelasnica', 'kolašin',
    'poiana brasov', 'sinaia', 'bukovel', 'åre', 'sälen', 'vemdalen',
    'idre fjäll', 'branäs', 'romme alpin', 'stöten', 'trysil', 'hafjell',
    'kvitfjell', 'hemsedal', 'geilo', 'myrkdalen', 'oppdal', 'hovden',
    'narvik', 'levi', 'ruka', 'ylläs'
}

# 10. Explicit Beginner & Family-Friendly Standouts
BEGINNER_FAMILY_RESORTS = {
    'alpbach', 'auffach', 'niederau', 'ellmau', 'ellmau - going', 'söll',
    'scheffau', 'westendorf', 'brixen im thale', 'hopfgarten', 'filzmoos',
    'wagrain', 'flachau', 'st. johann', 'radstadt-altenmarkt', 'hochkönig',
    'wildkogel', 'loferer alm', 'steinplatte-waidring', 'st. johann in tirol',
    'st. ulrich-buchensteinwand', 'zahmer kaiser', 'hochkössen', 'fageralm',
    'ramsau', 'galsterberg', 'riesneralm', 'planneralm', 'turracher höhe',
    'kreischberg', 'katschberg-aineck', 'gerlitzen', 'bad kleinkirchheim',
    'nassfeld', 'weissensee', 'dreiländereck', 'simonhöhe', 'hochrindl',
    'klippitztörl', 'koralpe', 'weinebene', 'petzen', 'emberger alm',
    'kötschach-mauthen', 'serfaus-fiss-ladis', 'fendels', 'nauders',
    'hochoetz', 'hochzeiger', 'kühtai', 'muttereralm', 'ranggerköpfl',
    'patscherkofel', 'schlick 2000', 'bergeralm', 'kellerjoch', 'glun gezer',
    'seefeld - rosshütte', 'lermoos', 'ehrwald', 'berwang', 'jungholz',
    'achensee - christlum', 'rofan', 'fügen - spieljoch', 'gerlos',
    'zell am ziller', 'königsleiten', 'hochkrimml', 'zillertal arena',
    'obergurgl-hochgurgl', 'lech zürs', 'warth-schröcken', 'galtür', 'see',
    'brandnertal', 'golm', 'diedamskopf', 'faschina-fontanella', 'sonntag-stein',
    'laterns - gapfohl', 'schetteregg', 'niedere - andelsbuch', 'hinterstoder',
    'wurzeralm', 'kasberg', 'dachstein west', 'annaberg', 'feuerkogel',
    'postalm', 'abtenau', 'zinkenlifte hallein', 'naglköpfl piesendorf',
    'heutal - unken', 'gaißau-hintersee', 'werfenweng', 'rauris', 'fanningberg',
    'grosseck-speiereck', 'großeck-speiereck', 'hochficht', 'hochkar',
    'lackenhof am ötscher', 'gemeindealpe', 'semmering (stuhleck)',
    'semmering (hirschenkogel)', 'mönichkirchen-mariensee', 'unterberg',
    'st. jakob im walde', 'gaberl', 'präbichl', 'hohentauern', 'lachtal',
    'grebenzen', 'les gets', 'morzine', 'avoriaz', 'châtel', 'flaine',
    'samoëns', 'les carroz', 'megève', 'les contamines-montjoie', 'la clusaz',
    'le grand bornand', 'praz de lys - sommand', 'les saisies', 'la plagne',
    'peisey-vallandry', 'les arcs', 'les menuires', 'courchevel', 'méribel',
    'st martin de belleville', 'valmorel', 'saint-françois-longchamp',
    'la rosière', 'sainte-foy', 'val cenis', 'aussois', 'valfréjus',
    'valloire', 'les sybelles', 'les karellis', 'alpe d\'huez',
    'les deux alpes', 'chamrousse', 'les 7 laux', 'montgenèvre',
    'serre chevalier', 'vars', 'risoul 1850', 'les orres', 'orcières merlette',
    'praloup', 'val d\'allos (la foux)', 'isola 2000', 'auron', 'valberg',
    'superbesse', 'le mont-dore', 'le lioran', 'la bresse-hohneck', 'métabief',
    'les rousses', 'lélex-crozet', 'saint-lary-soulan', 'peyragudes',
    'piau engaly', 'grand tourmalet', 'luz-ardiden', 'cauterets', 'gourette',
    'la pierre saint-martin', 'gavarnie-gèdre', 'hautacam', 'guzet-neige',
    'ax 3 domaines', 'les angles', 'font-romeu', 'val d\'ese', 'ghisoni - capanelle',
    'alta badia', 'val gardena', 'alpe di siusi', 'kronplatz', '3 zinnen dolomites',
    'gitschberg jochtal', 'plose', 'meran 2000', 'carezza', 'obereggen',
    'val di fiemme', 'alpe lusia / san pellegrino', 'canazei', 'val di fassa',
    'san martino di castrozza', 'civetta', 'passo tonale', 'folgarida - marilleva',
    'madonna di campiglio', 'andalo - paganella', 'folgaria - fiorentini',
    'monte bondone', 'livigno', 'bormio', 'aprica', 'madesimo', 'pila',
    'cervinia', 'la thuile', 'crévacol', 'bardonecchia', 'sauze d\'oulx',
    'sestriere', 'claviere', 'sansicario', 'pragelato', 'limone piemonte',
    'mondolè ski (prato nevoso)', 'ratschings-jaufen', 'monte cavallo - vipiteno',
    'ladurns', 'klausberg', 'speikboden', 'reinswald', 'schöneben-haideralm',
    'watles', 'schwemmalm', 'sulden am ortler', 'tarvisio', 'ravascletto-zoncolan',
    'abetone', 'roccaraso', 'campo felice', 'camigliatello silano',
    'gambarie d\'aspromonte', 'piano battaglia', 'aletsch arena', 'arosa',
    'lenzerheide', 'flims', 'laax', 'savognin', 'scuol', 'brigels (breil)',
    'splügen-tambo', 'davos klosters', 'adelboden-lenk', 'lenk', 'gstaad',
    'grindelwald-wengen', 'wengen', 'meiringen-hasliberg', 'kandersteg',
    'sörenberg', 'melchsee-frutt', 'klewenalp-stockhütte', 'stoos',
    'mythenregion', 'hoch-ybrig', 'sattel-hochstuckli', 'brunni-alpthal',
    'flumserberg', 'pizol', 'toggenburg', 'elm', 'braunwald', 'villars-gryon',
    'leysin', 'les diablerets', 'crans-montana', 'anzère', 'nendaz',
    'veysonnaz', 'thyon', 'grächen', 'lauchernalp', 'leukerbad', 'champex-lac',
    'val d\'anniviers', 'malbun', 'grandvalira', 'vallnord', 'ordino arcalis',
    'baqueira-beret', 'formigal', 'panticosa', 'cerler', 'la molina', 'masella',
    'boí taüll', 'astún', 'candanchú', 'sierra nevada', 'alto campoo',
    'valgrande-pajares', 'puerto de navacerrada', 'valdesquí', 'manzaneda',
    'serra da estrela', 'brauneck', 'spitzingsee-tegernsee', 'sudelfeld',
    'winklmoosalm', 'reit im winkl', 'oberjoch', 'balderschwang', 'hocheck',
    'oberstdorf', 'garmisch-partenkirchen', 'großer arber', 'feldberg',
    'winterberg', 'oberwiesenthal', 'lipno', 'špindlerův mlýn', 'klínovec',
    'pec pod sněžkou', 'harrachov', 'białka tatrzańska', 'szczyrk',
    'donovaly', 'jasná', 'tatranská lomnica', 'kubínska hoľa', 'vrátna',
    'kranjska gora', 'rogla', 'mariborsko pohorje', 'cerkno', 'krvavec',
    'vogel', 'bansko', 'borovets', 'pamporovo', 'kopaonik', 'jahorina',
    'kolašin', 'poiana brasov', 'sinaia', 'bukovel', 'gudauri', 'parnassos',
    'troodos - mount olympus', 'bigëll - dardhë', 'cairngorm', 'glenshee',
    'sälen', 'idre fjäll', 'branäs', 'vemdalen', 'romme alpin', 'stöten',
    'åre', 'trysil', 'hafjell', 'geilo', 'hemsedal', 'myrkdalen', 'oppdal',
    'hovden', 'levi', 'ruka', 'ylläs'
}


def parse_float(val_str):
    if not val_str:
        return 0.0
    m = re.search(r'([0-9]+(?:[\.,][0-9]+)?)', str(val_str))
    return float(m.group(1).replace(',', '.')) if m else 0.0


def parse_elevations(elev_str):
    """Parses '1250m - 2500m' -> (1250, 2500)."""
    nums = [int(x) for x in re.findall(r'([0-9]{3,4})', str(elev_str))]
    if len(nums) >= 2:
        return min(nums), max(nums)
    elif len(nums) == 1:
        return nums[0], nums[0]
    return 0, 0


def parse_airport_km(airport_str):
    """Extracts distance in km from 'Innsbruck (75km)' -> 75."""
    m = re.search(r'\(([0-9]+)\s*km\)', str(airport_str), re.IGNORECASE)
    return int(m.group(1)) if m else 999


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


def scrape_skiresort_features(resort_name):
    """
    Optional Live Scraper (Layer 3):
    Queries Skiresort.info detail page for official Test Report awards and slope breakdown.
    """
    slug = transliterate_slug(resort_name)
    url = f"https://www.skiresort.com/en/ski-resort/{slug}/"
    scraped_tags = set()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
        if resp.status_code != 200 or '/ski-resort/' not in resp.url:
            return scraped_tags

        text = resp.text.lower()
        if 'top for beginners' in text:
            scraped_tags.add('Good for Beginners')
        if 'top for families' in text:
            scraped_tags.add('Family Friendly')
        if 'top for experts/freeriders' in text:
            scraped_tags.add('Challenging Runs')
            scraped_tags.add('Freeride & Off-Piste')
        if 'top snow park offering' in text or 'snow park' in text:
            scraped_tags.add('Snowpark & Terrain Park')
        if 'halfpipe' in text or 'half-pipe' in text or 'superpipe' in text:
            scraped_tags.add('Half-Pipe')
        if 'top après-ski' in text:
            scraped_tags.add('Lively Après-Ski')
        if 'glacier ski resort' in text:
            scraped_tags.add('Glacier Skiing')

        # Parse Easy / Intermediate / Difficult percentages
        m_easy = re.search(r'easy\s+[0-9\.]+\s*km\s*\(([0-9]+)\s*%\)', text)
        m_inter = re.search(r'intermediate\s+[0-9\.]+\s*km\s*\(([0-9]+)\s*%\)', text)
        m_diff = re.search(r'difficult\s+[0-9\.]+\s*km\s*\(([0-9]+)\s*%\)', text)
        if m_easy and int(m_easy.group(1)) >= 35:
            scraped_tags.add('Good for Beginners')
        if m_inter and int(m_inter.group(1)) >= 45:
            scraped_tags.add('Cruisy Intermediates')
        if m_diff and int(m_diff.group(1)) >= 20:
            scraped_tags.add('Challenging Runs')
    except Exception:
        pass
    return scraped_tags


def classify_resort(row, col_map, live_scrape=False):
    """
    Combines Layer 1 (Quantitative CSV Rules), Layer 2 (Curated 435-Resort Knowledge Base),
    and optional Layer 3 (Live Skiresort.info Scraping) into a clean, ordered list of tags.
    """
    country = row[col_map['country']].strip()
    name = row[col_map['name']].strip()
    name_lower = name.lower()
    region = row[col_map['region']].strip() if col_map['region'] < len(row) else ''
    runs_str = row[col_map['runs']].strip() if col_map['runs'] < len(row) else '0'
    elev_str = row[col_map['elev']].strip() if col_map['elev'] < len(row) else ''
    pass_str = row[col_map['pass']].strip() if col_map['pass'] < len(row) else ''
    lifts_str = row[col_map['lifts']].strip() if col_map['lifts'] < len(row) else '0'
    visitors_str = row[col_map['visitors']].strip() if col_map['visitors'] < len(row) else ''
    snow_str = row[col_map['snow']].strip() if col_map['snow'] < len(row) else ''
    airport_str = row[col_map['airport']].strip() if col_map['airport'] < len(row) else ''
    train_str = row[col_map['train']].strip() if col_map['train'] < len(row) else ''

    runs_km = parse_float(runs_str)
    low_m, high_m = parse_elevations(elev_str)
    vert_m = max(0, high_m - low_m)
    pass_price = parse_float(pass_str)
    lifts_cnt = int(parse_float(lifts_str))
    airport_km = parse_airport_km(airport_str)
    is_chf = 'CHF' in pass_str or country.lower() in ('switzerland', 'liechtenstein')

    tags = set()

    # ------------------------------------------------------------------
    # LAYER 1: Quantitative Rules from Master CSV
    # ------------------------------------------------------------------
    # 1. Mega Ski Area (150km+)
    if runs_km >= 150 or 'area' in runs_str.lower() and runs_km >= 140:
        tags.add('Mega Ski Area (150km+)')

    # 2. Cruisy Intermediates
    if runs_km >= 45:
        tags.add('Cruisy Intermediates')

    # 3. Challenging Runs (Vertical drop >= 1150m or large high-alpine domain)
    if vert_m >= 1150 or (high_m >= 2600 and runs_km >= 60):
        tags.add('Challenging Runs')

    # 4. High Altitude & Snow-Sure
    if high_m >= 2500 or snow_str.lower() in ('excellent', 'very good') or low_m >= 1550:
        tags.add('High Altitude & Snow-Sure')

    # 5. Glacier Skiing
    if 'glacier' in name_lower or 'gletscher' in name_lower or name_lower in GLACIER_RESORTS:
        tags.add('Glacier Skiing')
        tags.add('High Altitude & Snow-Sure')

    # 6. Budget Friendly
    budget_threshold = 54.0 if is_chf else 48.0
    if 0 < pass_price <= budget_threshold:
        tags.add('Budget Friendly')

    # 7. Short Airport Transfer (<= 75km)
    if airport_km <= 75:
        tags.add('Short Airport Transfer')

    # 8. Rail-to-Slopes Access
    if train_str and train_str.lower() not in ('n/a', 'none', '-', ''):
        train_clean = re.sub(r'\s*\(.*?\)', '', train_str).strip().lower()
        # Check if train station name matches resort name or is within <=15km
        m_train_km = re.search(r'\(([0-9]+)\s*km\)', train_str, re.IGNORECASE)
        if (
            train_clean in name_lower
            or name_lower in train_clean
            or (m_train_km and int(m_train_km.group(1)) <= 15)
            or any(w in name_lower for w in train_clean.split() if len(w) >= 4)
        ):
            tags.add('Rail-to-Slopes Access')

    # 9. Quiet Slopes (Uncrowded hidden gems & low visitor density)
    vis_lower = visitors_str.lower()
    is_quiet = (
        vis_lower in ('small', 'low', 'medium')
        or (runs_km <= 55 and name_lower not in LIVELY_APRES_RESORTS and name_lower != 'winterberg')
        or (runs_km > 0 and lifts_cnt > 0 and (runs_km / max(1, lifts_cnt)) >= 4.2 and name_lower not in LIVELY_APRES_RESORTS)
    )
    if is_quiet:
        tags.add('Quiet Slopes')

    # ------------------------------------------------------------------
    # LAYER 2: Curated European Ski Resort Knowledge Base
    # ------------------------------------------------------------------
    if name_lower in HALF_PIPE_RESORTS:
        tags.add('Half-Pipe')
        tags.add('Snowpark & Terrain Park')

    if name_lower in SNOWPARK_RESORTS or runs_km >= 100:
        tags.add('Snowpark & Terrain Park')

    if name_lower in LIVELY_APRES_RESORTS:
        tags.add('Lively Après-Ski')

    if name_lower in SKI_IN_SKI_OUT_RESORTS or low_m >= 1650:
        tags.add('Ski-in / Ski-out')

    if name_lower in CHALLENGING_RESORTS:
        tags.add('Challenging Runs')

    if name_lower in FREERIDE_RESORTS or (high_m >= 2650 and vert_m >= 1100):
        tags.add('Freeride & Off-Piste')

    if name_lower in CHARMING_VILLAGE_RESORTS:
        tags.add('Charming Alpine Village')

    if name_lower in TREE_SKIING_RESORTS or (high_m <= 2150 and country not in ('Scotland', 'Cyprus', 'Iceland')):
        tags.add('Tree-Lined Runs')

    if name_lower in BEGINNER_FAMILY_RESORTS or runs_km <= 45:
        if name_lower not in ('la grave', 'nordkette'):
            tags.add('Good for Beginners')
            tags.add('Family Friendly')

    # ------------------------------------------------------------------
    # LAYER 3: Optional Live Batch Web-Scraping
    # ------------------------------------------------------------------
    scraped_tags = set()
    if live_scrape:
        scraped_tags = scrape_skiresort_features(name)
        tags |= scraped_tags
        time.sleep(0.2)

    # Canonical ordering for clean, scannable display
    CANONICAL_ORDER = [
        'Good for Beginners',
        'Family Friendly',
        'Cruisy Intermediates',
        'Challenging Runs',
        'Freeride & Off-Piste',
        'Snowpark & Terrain Park',
        'Half-Pipe',
        'Quiet Slopes',
        'Lively Après-Ski',
        'Charming Alpine Village',
        'Ski-in / Ski-out',
        'Glacier Skiing',
        'High Altitude & Snow-Sure',
        'Tree-Lined Runs',
        'Mega Ski Area (150km+)',
        'Budget Friendly',
        'Short Airport Transfer',
        'Rail-to-Slopes Access'
    ]
    ordered_tags = [t for t in CANONICAL_ORDER if t in tags]
    source_mode = "Hybrid (CSV + Knowledge Base + Live Scrape)" if scraped_tags else "Hybrid (CSV + Curated Knowledge Base)"
    return ordered_tags, source_mode


def main():
    parser = argparse.ArgumentParser(description="Assign Resort Characteristics & Features in Batches")
    parser.add_argument('--offset', type=int, default=0, help="Starting row index (0-based)")
    parser.add_argument('--limit', type=int, default=0, help="Max resorts to process in this batch (0 = all 435 resorts)")
    parser.add_argument('--live-scrape', action='store_true', help="Also query Skiresort.info live pages in batches")
    args = parser.parse_args()

    input_path = OUTPUT_CSV if (args.offset > 0 and os.path.exists(OUTPUT_CSV)) else MASTER_CSV
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except FileNotFoundError:
        print(f"[x] Error: Input CSV '{input_path}' not found.")
        return

    col_map = {
        'country': next((i for i, h in enumerate(header) if 'Country' in h), 0),
        'name': next((i for i, h in enumerate(header) if 'Resort Name' in h), 1),
        'region': next((i for i, h in enumerate(header) if 'Region' in h), 2),
        'runs': next((i for i, h in enumerate(header) if 'Ski Runs' in h), 3),
        'elev': next((i for i, h in enumerate(header) if 'Elevation' in h), 4),
        'pass': next((i for i, h in enumerate(header) if 'Pass' in h), 5),
        'lifts': next((i for i, h in enumerate(header) if 'Lifts' in h), 6),
        'visitors': next((i for i, h in enumerate(header) if 'Visitors' in h), 7),
        'snow': next((i for i, h in enumerate(header) if 'Snow' in h), 8),
        'airport': next((i for i, h in enumerate(header) if 'Airport' in h), 10),
        'train': next((i for i, h in enumerate(header) if 'Train' in h), 11),
    }

    if 'Characteristics' in header:
        char_idx = header.index('Characteristics')
        out_header = list(header)
    else:
        char_idx = len(header)
        out_header = list(header) + ['Characteristics']

    start = max(0, args.offset)
    end = start + args.limit if args.limit > 0 else len(rows)
    target_indices = list(range(start, min(end, len(rows))))

    print("\n" + "=" * 80)
    print(" SKI RESORT CHARACTERISTICS & FEATURES CLASSIFIER")
    print(f" Input File  : {input_path}")
    print(f" Batch Range : Offset {start} -> {min(end, len(rows))} ({len(target_indices)} resorts)")
    print(f" Mode        : {'Live Web Scrape + Hybrid Engine' if args.live_scrape else 'Instant Hybrid Engine (Zero Timeout)'}")
    print("=" * 80 + "\n")

    updated_rows = [out_header]
    for r in rows:
        row_copy = list(r)
        while len(row_copy) <= char_idx:
            row_copy.append('')
        updated_rows.append(row_copy)

    audit_rows = [['Row #', 'Country', 'Resort Name', 'Tag Count', 'Assigned Characteristics', 'Classification Source']]
    tag_frequency = {}

    for idx in target_indices:
        row = rows[idx]
        if len(row) <= col_map['name']:
            continue

        country = re.sub(r'\s+', ' ', row[col_map['country']].strip())
        name = row[col_map['name']].strip()
        tags, source_mode = classify_resort(row, col_map, live_scrape=args.live_scrape)
        tags_str = ', '.join(tags)

        for t in tags:
            tag_frequency[t] = tag_frequency.get(t, 0) + 1

        updated_rows[idx + 1][char_idx] = tags_str
        audit_rows.append([idx + 1, country, name, len(tags), tags_str, source_mode])
        print(f"[{idx + 1}/{len(rows)}] {name} ({country}) -> {len(tags)} tags: {tags_str}")

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(updated_rows)

    with open(AUDIT_CSV, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(audit_rows)

    # Write GitHub Actions Step Summary if running in CI
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        try:
            lines = [
                "## 🎿 Ski Resort Characteristics & Features — Batch Summary",
                f"- **Resorts Classified:** `{len(target_indices)}`",
                f"- **Output Dataset:** `{OUTPUT_CSV}`\n",
                "### Tag Frequency Breakdown",
                "| Characteristic Tag | Resorts Assigned | % of Batch |",
                "|---|---|---|"
            ]
            for t, cnt in sorted(tag_frequency.items(), key=lambda x: x[1], reverse=True):
                pct = (cnt / len(target_indices) * 100.0) if target_indices else 0.0
                lines.append(f"| **{t}** | `{cnt}` | `{pct:.1f}%` |")
            with open(summary_path, 'a', encoding='utf-8') as sf:
                sf.write('\n'.join(lines) + '\n')
        except Exception:
            pass

    print("\n" + "=" * 80)
    print(f" CLASSIFICATION COMPLETE: {len(target_indices)} Resorts Enriched")
    print(f" Enriched Master CSV : {OUTPUT_CSV}")
    print(f" Detailed Audit CSV  : {AUDIT_CSV}")
    print("=" * 80)
    print("\nTag Distribution Across Batch:")
    for t, cnt in sorted(tag_frequency.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {t:<28}: {cnt} resorts")


if __name__ == '__main__':
    main()