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
# STRICT, SENSE-CHECKED EUROPEAN SKI RESORT KNOWLEDGE BASE (v3)
# ==============================================================================

# 1. Resorts with a Verified Winter Half-Pipe / Superpipe (~22 resorts)
HALF_PIPE_RESORTS = {
    'laax', 'crans-montana', 'saas-fee', 'zermatt', 'davos klosters',
    'st. moritz', 'grindelwald-wengen', 'leysin', 'avoriaz', 'tignes',
    'les deux alpes', 'châtel', 'livigno', 'bardonecchia',
    'madonna di campiglio', 'monte bondone', 'kitzsteinhorn - kaprun',
    'mayrhofen', 'kühtai', 'kreischberg', 'diedamskopf', 'sierra nevada',
    'grandvalira', 'špindlerův mlýn', 'ruka'
}

# 2. Resorts with Major Dedicated Snowparks / Terrain Parks (~135 resorts)
SNOWPARK_RESORTS = HALF_PIPE_RESORTS | {
    'les gets', 'morzine', 'flaine', 'samoëns', 'les menuires',
    'val thorens', 'méribel', 'courchevel', 'les arcs', 'la plagne',
    'val d\'isère', 'alpe d\'huez', 'chamonix', 'serre chevalier', 'vars',
    'risoul 1850', 'isola 2000', 'les 7 laux', 'la clusaz',
    'le grand bornand', 'chamrousse', 'les orres', 'montgenèvre',
    'valmorel', 'grand tourmalet', 'saint-lary-soulan', 'cauterets',
    'vallnord', 'baqueira-beret', 'formigal', 'cerler', 'la molina',
    'masella', 'saalbach', 'hinterglemm', 'leogang', 'fieberbrunn',
    'zell am ziller', 'gerlos', 'königsleiten', 'zillertal arena',
    'hochzillertal-hochfügen', 'schladming', 'reiteralm', 'flachau',
    'wagrain', 'zauchensee', 'obertauern', 'nassfeld', 'bad gastein',
    'hochkönig', 'steinplatte-waidring', 'kitzbühel', 'westendorf',
    'ellmau', 'söll', 'st. anton am arlberg', 'lech zürs', 'ischgl',
    'sölden', 'serfaus-fiss-ladis', 'axamer lizum', 'hochoetz',
    'hochzeiger', 'nauders', 'stubai glacier', 'kaunertal glacier',
    'pitztal glacier', 'hintertux glacier', 'turracher höhe', 'gerlitzen',
    'katschberg-aineck', 'damüls-mellau', 'silvretta montafon',
    'brandnertal', 'golm', 'alpbach', 'kronplatz', 'alta badia',
    'val gardena', 'obereggen', 'alpe lusia / san pellegrino',
    'val di fassa', 'canazei', 'passo tonale', 'bormio', 'sestriere',
    'sauze d\'oulx', 'cervinia', 'pila', 'mondolè ski (prato nevoso)',
    '3 zinnen dolomites', 'carezza', 'plose', 'schöneben-haideralm',
    'verbier', 'nendaz', 'veysonnaz', 'thyon', 'adelboden-lenk', 'lenk',
    'arosa', 'flumserberg', 'hoch-ybrig', 'engelberg', 'andermatt',
    'melchsee-frutt', 'scuol', 'savognin', 'villars-gryon',
    'val d\'anniviers', 'feldberg', 'oberstdorf', 'brauneck',
    'spitzingsee-tegernsee', 'sudelfeld', 'winterberg', 'großer arber',
    'klínovec', 'pec pod sněžkou', 'jasná', 'donovaly',
    'białka tatrzańska', 'szczyrk', 'vogel', 'krvavec', 'kranjska gora',
    'jahorina', 'kopaonik', 'bansko', 'borovets', 'pamporovo', 'gudauri',
    'bukovel', 'geilo', 'hafjell', 'kvitfjell', 'myrkdalen', 'hemsedal',
    'trysil', 'åre', 'sälen', 'idre fjäll', 'branäs', 'vemdalen',
    'stöten', 'levi', 'ylläs'
}

# 3. True Operational Alpine Glacier Ski Resorts (Requires Top Elevation >= 2650m)
GLACIER_RESORTS = {
    'hintertux glacier', 'stubai glacier', 'sölden', 'pitztal glacier',
    'kaunertal glacier', 'kitzsteinhorn - kaprun', 'mölltal glacier',
    'weisssee gletscherwelt', 'tignes', 'val d\'isère', 'les deux alpes',
    'la grave', 'chamonix', 'alpe d\'huez', 'zermatt', 'saas-fee',
    'engelberg', 'cervinia', 'passo tonale', 'schnalstal',
    'arabba / marmolada'
}

# 4. Famous Lively Après-Ski Hubs (~75 resorts)
LIVELY_APRES_RESORTS = {
    'ischgl', 'st. anton am arlberg', 'lech zürs', 'sölden', 'saalbach',
    'hinterglemm', 'mayrhofen', 'flachau', 'wagrain', 'schladming',
    'obertauern', 'kitzbühel', 'westendorf', 'söll', 'ellmau', 'gerlos',
    'zell am ziller', 'zillertal arena', 'bad gastein',
    'serfaus-fiss-ladis', 'nassfeld', 'zell am see',
    'kitzsteinhorn - kaprun', 'hochzillertal-hochfügen',
    'fügen - spieljoch', 'val d\'isère', 'tignes', 'val thorens',
    'méribel', 'courchevel', 'les deux alpes', 'alpe d\'huez', 'chamonix',
    'avoriaz', 'morzine', 'les gets', 'les arcs', 'la clusaz',
    'serre chevalier', 'vars', 'risoul 1850', 'grandvalira', 'verbier',
    'zermatt', 'davos klosters', 'laax', 'st. moritz',
    'grindelwald-wengen', 'crans-montana', 'engelberg', 'arosa',
    'saas-fee', 'livigno', 'madonna di campiglio', 'val gardena',
    'canazei', 'val di fassa', 'alta badia', 'cortina d\'ampezzo',
    'cervinia', 'sauze d\'oulx', 'sestriere', 'bormio', 'passo tonale',
    'kronplatz', 'winterberg', 'garmisch-partenkirchen', 'bansko',
    'borovets', 'špindlerův mlýn', 'jasná', 'zakopane',
    'białka tatrzańska', 'kranjska gora', 'baqueira-beret', 'formigal',
    'sierra nevada', 'åre', 'hemsedal', 'trysil', 'levi', 'ruka',
    'gudauri', 'bukovel', 'kopaonik', 'jahorina', 'poiana brasov'
}

# 5. True Doorstep Ski-in / Ski-out Resorts (~80 purpose-built stations & car-free slope villages)
SKI_IN_SKI_OUT_RESORTS = {
    'avoriaz', 'val thorens', 'les menuires', 'st martin de belleville',
    'méribel', 'courchevel', 'la plagne', 'les arcs', 'peisey-vallandry',
    'tignes', 'val d\'isère', 'flaine', 'alpe d\'huez', 'les deux alpes',
    'isola 2000', 'valmorel', 'saint-françois-longchamp', 'la rosière',
    'les saisies', 'les sybelles', 'orcières merlette', 'superbesse',
    'le lioran', 'les orres', 'risoul 1850', 'vars', 'montgenèvre',
    'valfréjus', 'val cenis', 'sainte-foy', 'les karellis', 'praloup',
    'val d\'allos (la foux)', 'peyragudes', 'piau engaly',
    'la pierre saint-martin', 'gourette', 'obertauern', 'kühtai',
    'obergurgl-hochgurgl', 'damüls-mellau', 'faschina-fontanella',
    'warth-schröcken', 'lech zürs', 'königsleiten', 'hochkrimml',
    'zauchensee', 'tauplitz', 'turracher höhe', 'katschberg-aineck',
    'nassfeld', 'hochrindl', 'klippitztörl', 'weinebene', 'cervinia',
    'sestriere', 'sansicario', 'claviere', 'pragelato', 'passo tonale',
    'folgarida - marilleva', 'monte bondone', 'mondolè ski (prato nevoso)',
    'aletsch arena', 'mürren', 'melchsee-frutt', 'stoos', 'flumserberg',
    'anzère', 'thyon', 'baqueira-beret', 'sierra nevada', 'trysil',
    'hemsedal', 'hafjell', 'kvitfjell', 'myrkdalen', 'sälen',
    'idre fjäll', 'branäs', 'vemdalen', 'stöten', 'levi', 'ruka',
    'gudauri', 'kopaonik', 'jahorina'
}

# 6. Genuine Challenging Runs & Steep Black Terrain (~115 resorts)
CHALLENGING_RESORTS = {
    'st. anton am arlberg', 'lech zürs', 'warth-schröcken', 'ischgl',
    'sölden', 'kitzbühel', 'mayrhofen', 'hintertux glacier',
    'stubai glacier', 'pitztal glacier', 'kaunertal glacier',
    'kitzsteinhorn - kaprun', 'obergurgl-hochgurgl', 'serfaus-fiss-ladis',
    'silvretta montafon', 'gargellen', 'golm', 'sonnenkopf',
    'damüls-mellau', 'diedamskopf', 'schladming', 'reiteralm',
    'zauchensee', 'bad gastein', 'obertauern', 'saalbach', 'hinterglemm',
    'fieberbrunn', 'zell am see', 'nassfeld', 'bad kleinkirchheim',
    'grossglockner resort kals-matrei', 'heiligenblut',
    'ankogel-mallnitz', 'mölltal glacier', 'dachstein krippenstein',
    'axamer lizum', 'nordkette', 'hochzillertal-hochfügen', 'chamonix',
    'la grave', 'val d\'isère', 'tignes', 'val thorens', 'les menuires',
    'méribel', 'courchevel', 'les arcs', 'la plagne', 'peisey-vallandry',
    'alpe d\'huez', 'les deux alpes', 'serre chevalier', 'avoriaz',
    'flaine', 'la clusaz', 'le grand bornand', 'les contamines-montjoie',
    'sainte-foy', 'bonneval-sur-arc', 'les 7 laux', 'chamrousse',
    'isola 2000', 'grand tourmalet', 'gourette', 'cauterets',
    'luz-ardiden', 'piau engaly', 'saint-lary-soulan', 'verbier',
    'zermatt', 'saas-fee', 'engelberg', 'andermatt', 'mürren',
    'grindelwald-wengen', 'laax', 'davos klosters', 'st. moritz',
    'crans-montana', 'nendaz', 'veysonnaz', 'thyon', 'val d\'anniviers',
    'flumserberg', 'pizol', 'scuol', 'splügen-tambo', 'disentis 3000',
    'lauchernalp', 'arolla', 'arabba / marmolada', 'cortina d\'ampezzo',
    'val gardena', 'madonna di campiglio', 'passo tonale', 'bormio',
    'santa caterina valfurva', 'livigno', 'madesimo', 'courmayeur',
    'la thuile', 'monterosa ski', 'sestriere', 'sauze d\'oulx',
    'bardonecchia', 'limone piemonte', 'ravascletto-zoncolan', 'tarvisio',
    'sella nevea', 'schnalstal', 'sulden am ortler', 'campo imperatore',
    'roccaraso', 'garmisch-partenkirchen', 'oberstdorf', 'jenner',
    'kampenwand', 'feldberg', 'grandvalira', 'ordino arcalis',
    'baqueira-beret', 'boí taüll', 'cerler', 'formigal', 'astún',
    'candanchú', 'sierra nevada', 'jasná', 'tatranská lomnica',
    'zakopane', 'szczyrk', 'krvavec', 'kanin', 'bansko', 'borovets',
    'gudauri', 'bukovel', 'åre', 'riksgränsen', 'hemsedal', 'narvik',
    'strandafjellet', 'myrkdalen', 'kvitfjell', 'glencoe', 'nevis range'
}

# 7. Renowned Freeride & Off-Piste Terrain (~115 resorts)
FREERIDE_RESORTS = {
    'st. anton am arlberg', 'lech zürs', 'warth-schröcken', 'sonnenkopf',
    'ischgl', 'kappl', 'galtür', 'sölden', 'obergurgl-hochgurgl',
    'fieberbrunn', 'saalbach', 'hinterglemm', 'leogang',
    'dachstein krippenstein', 'tauplitz', 'loser - altaussee',
    'bad gastein', 'zauchensee', 'obertauern', 'kitzsteinhorn - kaprun',
    'weisssee gletscherwelt', 'grossglockner resort kals-matrei',
    'heiligenblut', 'ankogel-mallnitz', 'mölltal glacier',
    'hintertux glacier', 'stubai glacier', 'pitztal glacier',
    'kaunertal glacier', 'kühtai', 'axamer lizum', 'nordkette',
    'hochzillertal-hochfügen', 'mayrhofen', 'damüls-mellau',
    'diedamskopf', 'gargellen', 'silvretta montafon', 'chamonix',
    'la grave', 'val d\'isère', 'tignes', 'val thorens', 'les arcs',
    'peisey-vallandry', 'la plagne', 'sainte-foy', 'la rosière',
    'les deux alpes', 'alpe d\'huez', 'serre chevalier', 'montgenèvre',
    'la clusaz', 'flaine', 'avoriaz', 'les 7 laux',
    'pralognan-la-vanoise', 'bonneval-sur-arc', 'grand tourmalet',
    'piau engaly', 'cauterets', 'luz-ardiden', 'verbier', 'nendaz',
    'veysonnaz', 'thyon', 'zermatt', 'saas-fee', 'engelberg', 'andermatt',
    'disentis 3000', 'laax', 'davos klosters', 'st. moritz', 'arosa',
    'splügen-tambo', 'lauchernalp', 'arolla', 'val d\'anniviers',
    'mürren', 'pizol', 'elm', 'scuol', 'vals 3000', 'monterosa ski',
    'courmayeur', 'la thuile', 'cervinia', 'arabba / marmolada',
    'cortina d\'ampezzo', 'val gardena', 'canazei', 'passo tonale',
    'livigno', 'bormio', 'madesimo', 'sestriere', 'sauze d\'oulx',
    'pragelato', 'sella nevea', 'kanin', 'sulden am ortler', 'schnalstal',
    'campo imperatore', 'etna nord - piano provenzana', 'ordino arcalis',
    'grandvalira', 'baqueira-beret', 'boí taüll', 'cerler', 'formigal',
    'astún', 'candanchú', 'riksgränsen', 'narvik', 'strandafjellet',
    'myrkdalen', 'hemsedal', 'åre', 'gudauri', 'bansko', 'jasná',
    'zakopane', 'glencoe', 'nevis range'
}

# 8. Steep / Advanced / Party-Focused Resorts Excluded From "Good for Beginners"
NOT_BEGINNER_RESORTS = {
    'la grave', 'chamonix', 'st. anton am arlberg', 'lech zürs', 'ischgl',
    'sölden', 'mayrhofen', 'kitzbühel', 'obergurgl-hochgurgl',
    'hintertux glacier', 'pitztal glacier', 'kaunertal glacier',
    'dachstein krippenstein', 'ankogel-mallnitz', 'mölltal glacier',
    'weisssee gletscherwelt', 'nordkette', 'heiligenblut',
    'grossglockner resort kals-matrei', 'axamer lizum', 'fieberbrunn',
    'bad gastein', 'zauchensee', 'val d\'isère', 'tignes', 'val thorens',
    'les deux alpes', 'alpe d\'huez', 'les arcs', 'serre chevalier',
    'la clusaz', 'les 7 laux', 'grand tourmalet', 'piau engaly', 'verbier',
    'zermatt', 'saas-fee', 'engelberg', 'andermatt', 'mürren',
    'disentis 3000', 'laax', 'davos klosters', 'st. moritz',
    'lauchernalp', 'arolla', 'vals 3000', 'val d\'anniviers',
    'splügen-tambo', 'pizol', 'cortina d\'ampezzo', 'arabba / marmolada',
    'bormio', 'santa caterina valfurva', 'la thuile', 'courmayeur',
    'monterosa ski', 'madesimo', 'sestriere', 'sauze d\'oulx',
    'sella nevea', 'kanin', 'campo imperatore', 'sulden am ortler',
    'schnalstal', 'jenner', 'kampenwand', 'garmisch-partenkirchen',
    'oberstdorf', 'ordino arcalis', 'boí taüll', 'astún', 'candanchú',
    'cerler', 'jasná', 'zakopane', 'szczyrk', 'krvavec', 'bansko',
    'gudauri', 'åre', 'riksgränsen', 'narvik', 'strandafjellet',
    'myrkdalen', 'kvitfjell', 'glencoe', 'nevis range'
}

# 9. Busy / Commuter / Major Resorts Excluded From "Quiet Slopes"
BUSY_RESORTS_NOT_QUIET = LIVELY_APRES_RESORTS | {
    'winterberg', 'feldberg', 'brauneck', 'spitzingsee-tegernsee',
    'sudelfeld', 'oberstdorf', 'garmisch-partenkirchen', 'großer arber',
    'oberwiesenthal', 'semmering (hirschenkogel)', 'semmering (stuhleck)',
    'hinterstoder', 'wurzeralm', 'kasberg', 'feuerkogel', 'hochkar',
    'hochficht', 'nassfeld', 'gerlitzen', 'bad kleinkirchheim',
    'turracher höhe', 'kreischberg', 'katschberg-aineck', 'obertauern',
    'roccaraso', 'campo felice', 'abetone', 'mondolè ski (prato nevoso)',
    'bardonecchia', 'pila', 'monte bondone', 'andalo - paganella',
    'folgaria - fiorentini', 'kronplatz', 'obereggen', 'carezza',
    'klínovec', 'pec pod sněžkou', 'harrachov', 'lipno',
    'tatranská lomnica', 'donovaly', 'szczyrk', 'krvavec', 'rogla',
    'mariborsko pohorje', 'pamporovo', 'sinaia', 'la molina', 'masella',
    'puerto de navacerrada', 'valdesquí', 'astún', 'candanchú', 'cerler',
    'flumserberg', 'hoch-ybrig', 'mythenregion', 'stoos', 'sörenberg',
    'melchsee-frutt', 'adelboden-lenk', 'lenk', 'gstaad', 'villars-gryon',
    'leysin', 'pizol', 'toggenburg', 'romme alpin', 'branäs',
    'idre fjäll', 'sälen', 'vemdalen', 'hafjell', 'kvitfjell', 'geilo',
    'oppdal'
}

# 10. Charming Traditional Alpine Villages (~140 resorts)
CHARMING_VILLAGE_RESORTS = {
    'alpbach', 'auffach', 'niederau', 'ellmau', 'ellmau - going', 'söll',
    'westendorf', 'brixen im thale', 'hopfgarten', 'kitzbühel',
    'st. johann in tirol', 'fieberbrunn', 'filzmoos', 'hochkönig',
    'wagrain', 'radstadt-altenmarkt', 'schladming', 'tauplitz',
    'loser - altaussee', 'dachstein west', 'annaberg', 'werfenweng',
    'rauris', 'zell am see', 'bad gastein', 'bad kleinkirchheim',
    'heiligenblut', 'lienz', 'grossglockner resort kals-matrei',
    'st. jakob im defereggental', 'sillian-hochpustertal',
    'seefeld - rosshütte', 'lermoos', 'st. anton am arlberg', 'lech zürs',
    'warth-schröcken', 'galtür', 'ischgl', 'kappl', 'see',
    'serfaus-fiss-ladis', 'nauders', 'mayrhofen', 'zell am ziller',
    'gerlos', 'damüls-mellau', 'diedamskopf', 'brandnertal', 'gargellen',
    'silvretta montafon', 'weissensee', 'megève', 'chamonix',
    'les contamines-montjoie', 'la clusaz', 'le grand bornand', 'samoëns',
    'les gets', 'morzine', 'châtel', 'bonneval-sur-arc', 'val d\'isère',
    'st martin de belleville', 'valloire', 'val cenis',
    'pralognan-la-vanoise', 'aussois', 'sainte-foy', 'serre chevalier',
    'montgenèvre', 'cauterets', 'saint-lary-soulan', 'le mont-dore',
    'zermatt', 'saas-fee', 'mürren', 'grindelwald-wengen',
    'adelboden-lenk', 'lenk', 'gstaad', 'kandersteg',
    'meiringen-hasliberg', 'engelberg', 'andermatt', 'davos klosters',
    'st. moritz', 'scuol', 'arosa', 'brigels (breil)', 'disentis 3000',
    'savognin', 'splügen-tambo', 'elm', 'val d\'anniviers', 'arolla',
    'champex-lac', 'verbier', 'villars-gryon', 'malbun',
    'cortina d\'ampezzo', 'val gardena', 'alta badia', 'canazei',
    'val di fassa', 'arabba / marmolada', '3 zinnen dolomites',
    'madonna di campiglio', 'bormio', 'livigno', 'courmayeur',
    'la thuile', 'monterosa ski', 'bardonecchia', 'sauze d\'oulx',
    'limone piemonte', 'madesimo', 'santa caterina valfurva',
    'val di fiemme', 'obereggen', 'carezza', 'meran 2000',
    'monte cavallo - vipiteno', 'gitschberg jochtal', 'plose',
    'reinswald', 'sulden am ortler', 'ladurns', 'civetta',
    'san martino di castrozza', 'andalo - paganella',
    'ravascletto-zoncolan', 'garmisch-partenkirchen', 'oberstdorf',
    'reit im winkl', 'brauneck', 'sudelfeld', 'spitzingsee-tegernsee',
    'balderschwang', 'oberjoch', 'baqueira-beret', 'cerler', 'formigal',
    'panticosa', 'zakopane', 'białka tatrzańska', 'kranjska gora',
    'špindlerův mlýn', 'pec pod sněžkou', 'bansko', 'sinaia', 'åre',
    'geilo'
}

# 11. Verified Tree-Lined Runs (~175 genuinely forested resorts)
TREE_SKIING_RESORTS = {
    'schladming', 'reiteralm', 'fageralm', 'flachau', 'wagrain',
    'st. johann', 'radstadt-altenmarkt', 'filzmoos', 'hochkönig',
    'bad gastein', 'grossarl', 'werfenweng', 'annaberg', 'dachstein west',
    'tauplitz', 'loser - altaussee', 'riesneralm', 'galsterberg',
    'kreischberg', 'lachtal', 'grebenzen', 'turracher höhe',
    'bad kleinkirchheim', 'gerlitzen', 'nassfeld', 'katschberg-aineck',
    'dreiländereck', 'simonhöhe', 'hochrindl', 'klippitztörl',
    'kitzbühel', 'st. johann in tirol', 'steinplatte-waidring',
    'st. ulrich-buchensteinwand', 'fieberbrunn', 'saalbach',
    'hinterglemm', 'leogang', 'zell am see', 'ellmau', 'ellmau - going',
    'söll', 'westendorf', 'brixen im thale', 'hopfgarten', 'alpbach',
    'auffach', 'niederau', 'zahmer kaiser', 'hochkössen',
    'achensee - christlum', 'fügen - spieljoch', 'patscherkofel',
    'muttereralm', 'ranggerköpfl', 'schlick 2000', 'bergeralm',
    'seefeld - rosshütte', 'lermoos', 'fendels', 'nauders', 'hochoetz',
    'hochzeiger', 'brandnertal', 'golm',
    'kleinwalsertal (kanzelwand - ifen)', 'diedamskopf',
    'laterns - gapfohl', 'hinterstoder', 'wurzeralm', 'kasberg',
    'hochficht', 'hochkar', 'lackenhof am ötscher', 'gemeindealpe',
    'semmering (stuhleck)', 'semmering (hirschenkogel)',
    'mönichkirchen-mariensee', 'serre chevalier', 'montgenèvre', 'vars',
    'risoul 1850', 'les orres', 'praloup', 'val d\'allos (la foux)',
    'auron', 'valberg', 'megève', 'les contamines-montjoie', 'la clusaz',
    'le grand bornand', 'les gets', 'morzine', 'châtel', 'samoëns',
    'les carroz', 'praz de lys - sommand', 'les saisies', 'courchevel',
    'méribel', 'peisey-vallandry', 'sainte-foy', 'la rosière', 'valmorel',
    'val cenis', 'aussois', 'valfréjus', 'pralognan-la-vanoise',
    'les 7 laux', 'chamrousse', 'les rousses', 'métabief', 'lélex-crozet',
    'la bresse-hohneck', 'le lioran', 'le mont-dore', 'les angles',
    'ax 3 domaines', 'guzet-neige', 'saint-lary-soulan', 'cauterets',
    'gourette', 'la pierre saint-martin', 'madonna di campiglio',
    'folgarida - marilleva', 'andalo - paganella',
    'folgaria - fiorentini', 'monte bondone', 'kronplatz',
    '3 zinnen dolomites', 'alta badia', 'val gardena', 'obereggen',
    'val di fiemme', 'carezza', 'alpe lusia / san pellegrino', 'civetta',
    'san martino di castrozza', 'gitschberg jochtal', 'plose',
    'meran 2000', 'reinswald', 'ratschings-jaufen',
    'monte cavallo - vipiteno', 'ladurns', 'klausberg', 'speikboden',
    'schöneben-haideralm', 'watles', 'schwemmalm',
    'santa caterina valfurva', 'aprica', 'pila', 'courmayeur',
    'la thuile', 'sauze d\'oulx', 'bardonecchia', 'pragelato', 'claviere',
    'sansicario', 'limone piemonte', 'tarvisio', 'ravascletto-zoncolan',
    'abetone', 'camigliatello silano', 'garmisch-partenkirchen',
    'brauneck', 'spitzingsee-tegernsee', 'sudelfeld', 'oberstdorf',
    'oberjoch', 'balderschwang', 'reit im winkl', 'hocheck',
    'großer arber', 'feldberg', 'winterberg', 'oberwiesenthal',
    'villars-gryon', 'leysin', 'crans-montana', 'anzère', 'nendaz',
    'veysonnaz', 'gstaad', 'adelboden-lenk', 'lenk',
    'meiringen-hasliberg', 'sörenberg', 'klewenalp-stockhütte',
    'mythenregion', 'hoch-ybrig', 'flumserberg', 'toggenburg', 'savognin',
    'vallnord', 'baqueira-beret', 'formigal', 'panticosa', 'la molina',
    'masella', 'krvavec', 'rogla', 'mariborsko pohorje', 'kranjska gora',
    'cerkno', 'jasná', 'donovaly', 'tatranská lomnica', 'kubínska hoľa',
    'vrátna', 'špindlerův mlýn', 'klínovec', 'pec pod sněžkou',
    'harrachov', 'lipno', 'zakopane', 'białka tatrzańska', 'szczyrk',
    'bansko', 'borovets', 'pamporovo', 'kopaonik', 'jahorina',
    'poiana brasov', 'sinaia', 'bukovel', 'åre', 'sälen', 'vemdalen',
    'idre fjäll', 'branäs', 'romme alpin', 'stöten', 'trysil', 'hafjell',
    'kvitfjell', 'hemsedal', 'geilo', 'myrkdalen', 'oppdal', 'hovden',
    'levi', 'ruka', 'ylläs'
}

# 12. Verified Rail-to-Slopes Resorts (Direct Train Station in Resort or Base Gondola Link)
RAIL_TO_SLOPES_RESORTS = {
    'st. anton am arlberg', 'zell am see', 'bad gastein', 'schladming',
    'hopfgarten', 'brixen im thale', 'fügen - spieljoch',
    'zell am ziller', 'mayrhofen', 'st. johann in tirol', 'fieberbrunn',
    'kitzbühel', 'leogang', 'seefeld - rosshütte', 'lermoos',
    'semmering (hirschenkogel)', 'semmering (stuhleck)',
    'ankogel-mallnitz', 'lienz', 'st. johann', 'radstadt-altenmarkt',
    'achensee - christlum', 'rofan', 'kellerjoch', 'chamonix', 'les arcs',
    'peisey-vallandry', 'les contamines-montjoie', 'serre chevalier',
    'ax 3 domaines', 'le mont-dore', 'le lioran', 'les rousses',
    'zermatt', 'saas-fee', 'grindelwald-wengen', 'mürren', 'lenk',
    'gstaad', 'kandersteg', 'meiringen-hasliberg', 'engelberg',
    'andermatt', 'disentis 3000', 'davos klosters', 'arosa', 'st. moritz',
    'scuol', 'flumserberg', 'pizol', 'villars-gryon', 'leysin',
    'crans-montana', 'nendaz', 'verbier', 'aletsch arena', 'lauchernalp',
    'champex-lac', 'sattel-hochstuckli', 'stoos', 'klewenalp-stockhütte',
    'kronplatz', '3 zinnen dolomites', 'monte cavallo - vipiteno',
    'bardonecchia', 'limone piemonte', 'ravascletto-zoncolan', 'tarvisio',
    'garmisch-partenkirchen', 'oberstdorf', 'brauneck',
    'spitzingsee-tegernsee', 'sudelfeld', 'winterberg', 'feldberg',
    'oberwiesenthal', 'la molina', 'masella', 'puerto de navacerrada',
    'valdesquí', 'zakopane', 'tatranská lomnica', 'harrachov',
    'špindlerův mlýn', 'sinaia', 'åre', 'riksgränsen', 'geilo', 'oppdal',
    'narvik', 'kvitfjell'
}

# 13. Genuinely Ideal Beginner Resorts (~135 resorts with gentle progression & wide nursery/blue runs)
GOOD_FOR_BEGINNERS_RESORTS = {
    'alpbach', 'auffach', 'niederau', 'ellmau', 'ellmau - going', 'söll',
    'westendorf', 'brixen im thale', 'hopfgarten', 'filzmoos', 'wagrain',
    'flachau', 'st. johann', 'radstadt-altenmarkt', 'hochkönig',
    'wildkogel', 'loferer alm', 'steinplatte-waidring',
    'st. johann in tirol', 'st. ulrich-buchensteinwand', 'zahmer kaiser',
    'hochkössen', 'fageralm', 'galsterberg', 'riesneralm', 'planneralm',
    'turracher höhe', 'kreischberg', 'katschberg-aineck', 'gerlitzen',
    'weissensee', 'dreiländereck', 'simonhöhe', 'hochrindl',
    'klippitztörl', 'koralpe', 'weinebene', 'petzen', 'emberger alm',
    'kötschach-mauthen', 'serfaus-fiss-ladis', 'fendels', 'nauders',
    'hochoetz', 'hochzeiger', 'muttereralm', 'ranggerköpfl',
    'patscherkofel', 'schlick 2000', 'bergeralm', 'kellerjoch',
    'glun gezer', 'seefeld - rosshütte', 'lermoos', 'jungholz',
    'achensee - christlum', 'rofan', 'fügen - spieljoch', 'gerlos',
    'zell am ziller', 'königsleiten', 'hochkrimml', 'zillertal arena',
    'galtür', 'see', 'brandnertal', 'faschina-fontanella',
    'sonntag-stein', 'laterns - gapfohl', 'schetteregg',
    'niedere - andelsbuch', 'hinterstoder', 'wurzeralm', 'kasberg',
    'dachstein west', 'annaberg', 'postalm', 'abtenau',
    'zinkenlifte hallein', 'naglköpfl piesendorf', 'heutal - unken',
    'gaißau-hintersee', 'werfenweng', 'rauris', 'fanningberg',
    'großeck-speiereck', 'hochficht', 'lackenhof am ötscher',
    'gemeindealpe', 'mönichkirchen-mariensee', 'unterberg',
    'st. jakob im walde', 'gaberl', 'präbichl', 'hohentauern', 'lachtal',
    'grebenzen', 'les gets', 'morzine', 'avoriaz', 'châtel', 'flaine',
    'samoëns', 'les carroz', 'megève', 'les contamines-montjoie',
    'le grand bornand', 'praz de lys - sommand', 'les saisies',
    'la plagne', 'peisey-vallandry', 'les menuires', 'courchevel',
    'méribel', 'st martin de belleville', 'valmorel',
    'saint-françois-longchamp', 'la rosière', 'val cenis', 'aussois',
    'valfréjus', 'valloire', 'les sybelles', 'les karellis', 'chamrousse',
    'montgenèvre', 'vars', 'risoul 1850', 'les orres',
    'orcières merlette', 'praloup', 'val d\'allos (la foux)',
    'isola 2000', 'auron', 'valberg', 'superbesse', 'le mont-dore',
    'le lioran', 'la bresse-hohneck', 'métabief', 'les rousses',
    'lélex-crozet', 'saint-lary-soulan', 'peyragudes', 'luz-ardiden',
    'gourette', 'la pierre saint-martin', 'gavarnie-gèdre', 'hautacam',
    'guzet-neige', 'ax 3 domaines', 'les angles', 'alta badia',
    'val gardena', 'kronplatz', '3 zinnen dolomites',
    'gitschberg jochtal', 'plose', 'meran 2000', 'carezza', 'obereggen',
    'val di fiemme', 'alpe lusia / san pellegrino', 'canazei',
    'val di fassa', 'san martino di castrozza', 'civetta', 'passo tonale',
    'folgarida - marilleva', 'andalo - paganella',
    'folgaria - fiorentini', 'monte bondone', 'livigno', 'aprica', 'pila',
    'cervinia', 'crévacol', 'bardonecchia', 'claviere', 'sansicario',
    'pragelato', 'mondolè ski (prato nevoso)', 'ratschings-jaufen',
    'monte cavallo - vipiteno', 'ladurns', 'klausberg', 'speikboden',
    'reinswald', 'schöneben-haideralm', 'watles', 'schwemmalm',
    'ravascletto-zoncolan', 'abetone', 'campo felice',
    'camigliatello silano', 'gambarie d\'aspromonte', 'piano battaglia',
    'aletsch arena', 'arosa', 'savognin', 'scuol', 'brigels (breil)',
    'adelboden-lenk', 'lenk', 'gstaad', 'meiringen-hasliberg',
    'kandersteg', 'sörenberg', 'melchsee-frutt', 'klewenalp-stockhütte',
    'stoos', 'mythenregion', 'hoch-ybrig', 'sattel-hochstuckli',
    'brunni-alpthal', 'flumserberg', 'toggenburg', 'elm', 'villars-gryon',
    'leysin', 'crans-montana', 'anzère', 'leukerbad', 'champex-lac',
    'malbun', 'grandvalira', 'vallnord', 'baqueira-beret', 'formigal',
    'panticosa', 'la molina', 'masella', 'alto campoo',
    'valgrande-pajares', 'puerto de navacerrada', 'valdesquí',
    'manzaneda', 'serra da estrela', 'brauneck', 'spitzingsee-tegernsee',
    'sudelfeld', 'reit im winkl', 'oberjoch', 'balderschwang', 'hocheck',
    'großer arber', 'feldberg', 'winterberg', 'oberwiesenthal', 'lipno',
    'špindlerův mlýn', 'klínovec', 'pec pod sněžkou', 'harrachov',
    'białka tatrzańska', 'donovaly', 'kubínska hoľa', 'kranjska gora',
    'rogla', 'mariborsko pohorje', 'cerkno', 'pamporovo', 'kopaonik',
    'kolašin', 'poiana brasov', 'bukovel', 'parnassos',
    'troodos - mount olympus', 'bigëll - dardhë', 'cairngorm', 'glenshee',
    'sälen', 'idre fjäll', 'branäs', 'vemdalen', 'romme alpin', 'stöten',
    'trysil', 'hafjell', 'geilo', 'hemsedal', 'oppdal', 'hovden', 'levi',
    'ruka', 'ylläs'
}

# 14. Dedicated Family-Friendly Resorts (Includes Beginner Resorts + Larger Family Infrastructure Hubs)
FAMILY_FRIENDLY_RESORTS = GOOD_FOR_BEGINNERS_RESORTS | {
    'stubai glacier', 'obergurgl-hochgurgl', 'lech zürs',
    'warth-schröcken', 'golm', 'diedamskopf', 'schladming', 'reiteralm',
    'bad kleinkirchheim', 'nassfeld', 'zell am see', 'saalbach',
    'hinterglemm', 'leogang', 'semmering (stuhleck)', 'les arcs',
    'la clusaz', 'sainte-foy', 'cauterets', 'grand tourmalet',
    'madonna di campiglio', 'limone piemonte', 'roccaraso',
    'grindelwald-wengen', 'nendaz', 'veysonnaz', 'thyon', 'davos klosters',
    'laax', 'sierra nevada', 'tatranská lomnica', 'jasná', 'borovets',
    'jahorina', 'myrkdalen', 'åre'
}

# Southern / Mediterranean / Non-Alpine Hills Excluded From "High Altitude & Snow-Sure" Unless "Excellent"
SOUTHERN_VARIABLE_HILLS = {
    'ghisoni - capanelle', 'val d\'ese', 'etna nord - piano provenzana',
    'etna sud - nicolosi', 'gambarie d\'aspromonte', 'piano battaglia',
    'camigliatello silano', 'campo felice', 'campo imperatore',
    'parnassos', 'troodos - mount olympus', 'bigëll - dardhë',
    'valdesquí', 'puerto de navacerrada', 'manzaneda', 'serra da estrela'
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
    Optional Live Scraper (Layer 3 - v2 DOM-Scoped):
    Uses BeautifulSoup to inspect ONLY the resort's main content area (stripping
    out global navigation menus so 'Glacier ski resorts' and 'Snow parks' menu links
    never trigger false positives).
    """
    slug = transliterate_slug(resort_name)
    url = f"https://www.skiresort.com/en/ski-resort/{slug}/"
    scraped_tags = set()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQ_TIMEOUT, verify=False)
        if resp.status_code != 200 or '/ski-resort/' not in resp.url:
            return scraped_tags

        soup = BeautifulSoup(resp.text, 'html.parser')
        # Remove global nav, header, footer, and sidebars so menu links never pollute matching
        for nav_el in soup.find_all(['nav', 'header', 'footer', 'aside']):
            nav_el.decompose()

        body_text = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)).lower()

        # Only match explicit Skiresort.info Test Report award badges ('Top ... »')
        if 'top for beginners »' in body_text:
            scraped_tags.add('Good for Beginners')
        if 'top for families »' in body_text:
            scraped_tags.add('Family Friendly')
        if 'top for experts/freeriders »' in body_text:
            scraped_tags.add('Challenging Runs')
            scraped_tags.add('Freeride & Off-Piste')
        if 'top snow park offering »' in body_text:
            scraped_tags.add('Snowpark & Terrain Park')
        if 'top après-ski »' in body_text:
            scraped_tags.add('Lively Après-Ski')

        # Parse Easy / Intermediate / Difficult slope breakdown percentages
        m_easy = re.search(r'easy\s+[0-9\.]+\s*km\s*\(([0-9]+)\s*%\)', body_text)
        m_inter = re.search(r'intermediate\s+[0-9\.]+\s*km\s*\(([0-9]+)\s*%\)', body_text)
        m_diff = re.search(r'difficult\s+[0-9\.]+\s*km\s*\(([0-9]+)\s*%\)', body_text)
        if m_easy and int(m_easy.group(1)) >= 40:
            scraped_tags.add('Good for Beginners')
        if m_inter and int(m_inter.group(1)) >= 48:
            scraped_tags.add('Cruisy Intermediates')
        if m_diff and int(m_diff.group(1)) >= 22:
            scraped_tags.add('Challenging Runs')
    except Exception:
        pass
    return scraped_tags


def classify_resort(row, col_map, live_scrape=False):
    """
    v3 Audited Classification Engine:
    Enforces strict altitude, snow-reliability, piste-length, and mutual-exclusion
    guardrails so low/variable hills never get Glacier/High-Altitude tags and
    expert mountains never get Beginner/Cruisy tags.
    """
    country = row[col_map['country']].strip()
    name = row[col_map['name']].strip()
    name_lower = name.lower()
    runs_str = row[col_map['runs']].strip() if col_map['runs'] < len(row) else '0'
    elev_str = row[col_map['elev']].strip() if col_map['elev'] < len(row) else ''
    pass_str = row[col_map['pass']].strip() if col_map['pass'] < len(row) else ''
    lifts_str = row[col_map['lifts']].strip() if col_map['lifts'] < len(row) else '0'
    visitors_str = row[col_map['visitors']].strip() if col_map['visitors'] < len(row) else ''
    snow_str = row[col_map['snow']].strip() if col_map['snow'] < len(row) else ''
    airport_str = row[col_map['airport']].strip() if col_map['airport'] < len(row) else ''

    runs_km = parse_float(runs_str)
    low_m, high_m = parse_elevations(elev_str)
    pass_price = parse_float(pass_str)
    airport_km = parse_airport_km(airport_str)
    snow_lower = snow_str.lower()
    is_chf = 'CHF' in pass_str or country.lower() in ('switzerland', 'liechtenstein')

    tags = set()

    # ------------------------------------------------------------------
    # 1. Mega Ski Area (150km+)
    # ------------------------------------------------------------------
    if runs_km >= 150 or ('area' in runs_str.lower() and runs_km >= 140):
        tags.add('Mega Ski Area (150km+)')

    # ------------------------------------------------------------------
    # 2. Glacier Skiing (STRICT GATE: Must be in GLACIER_RESORTS and high_m >= 2650m)
    # ------------------------------------------------------------------
    if name_lower in GLACIER_RESORTS and high_m >= 2650:
        tags.add('Glacier Skiing')
        tags.add('High Altitude & Snow-Sure')

    # ------------------------------------------------------------------
    # 3. High Altitude & Snow-Sure
    #    STRICT GATE: Never assign if Snow Reliability is 'Variable'/'Poor'
    #    or if it is a Southern/Mediterranean micro-hill.
    # ------------------------------------------------------------------
    if snow_lower not in ('variable', 'poor', 'low', '') and name_lower not in SOUTHERN_VARIABLE_HILLS:
        if (
            high_m >= 2650
            or (high_m >= 2400 and snow_lower in ('excellent', 'very good'))
            or (low_m >= 1600 and high_m >= 2300 and snow_lower in ('excellent', 'very good'))
        ):
            tags.add('High Altitude & Snow-Sure')

    # ------------------------------------------------------------------
    # 4. Cruisy Intermediates (Requires >= 45km of runs and excludes steep expert-only mountains)
    # ------------------------------------------------------------------
    if runs_km >= 45 and name_lower not in (
        'la grave', 'chamonix', 'st. anton am arlberg', 'verbier',
        'andermatt', 'engelberg', 'mürren', 'disentis 3000',
        'dachstein krippenstein', 'ankogel-mallnitz', 'nordkette'
    ):
        tags.add('Cruisy Intermediates')

    # ------------------------------------------------------------------
    # 5. Challenging Runs & Freeride
    # ------------------------------------------------------------------
    if name_lower in CHALLENGING_RESORTS:
        tags.add('Challenging Runs')

    if name_lower in FREERIDE_RESORTS:
        tags.add('Freeride & Off-Piste')

    # ------------------------------------------------------------------
    # 6. Half-Pipe & Snowpark
    # ------------------------------------------------------------------
    if name_lower in HALF_PIPE_RESORTS:
        tags.add('Half-Pipe')
        tags.add('Snowpark & Terrain Park')
    elif name_lower in SNOWPARK_RESORTS:
        tags.add('Snowpark & Terrain Park')

    # ------------------------------------------------------------------
    # 7. Lively Après-Ski vs. Quiet Slopes (Mutually Exclusive!)
    # ------------------------------------------------------------------
    if name_lower in LIVELY_APRES_RESORTS:
        tags.add('Lively Après-Ski')
    else:
        vis_lower = visitors_str.lower()
        if (
            name_lower not in BUSY_RESORTS_NOT_QUIET
            and runs_km <= 75
            and vis_lower not in ('large', 'high', 'very large')
        ):
            tags.add('Quiet Slopes')

    # ------------------------------------------------------------------
    # 8. Charming Alpine Village & Tree-Lined Runs
    # ------------------------------------------------------------------
    if name_lower in CHARMING_VILLAGE_RESORTS:
        tags.add('Charming Alpine Village')

    if name_lower in TREE_SKIING_RESORTS:
        tags.add('Tree-Lined Runs')

    # ------------------------------------------------------------------
    # 9. Ski-in / Ski-out (Strictly Verified Doorstep Stations & Villages)
    # ------------------------------------------------------------------
    if name_lower in SKI_IN_SKI_OUT_RESORTS:
        tags.add('Ski-in / Ski-out')

    # ------------------------------------------------------------------
    # 10. Good for Beginners & Family Friendly (Decoupled & Guardrailed)
    # ------------------------------------------------------------------
    if name_lower in GOOD_FOR_BEGINNERS_RESORTS and name_lower not in NOT_BEGINNER_RESORTS:
        tags.add('Good for Beginners')

    if name_lower in FAMILY_FRIENDLY_RESORTS and name_lower not in ('la grave', 'dachstein krippenstein'):
        tags.add('Family Friendly')

    # ------------------------------------------------------------------
    # 11. Budget Friendly, Short Airport Transfer & Rail-to-Slopes
    # ------------------------------------------------------------------
    budget_threshold = 55.0 if is_chf else 50.0
    if 0 < pass_price <= budget_threshold:
        tags.add('Budget Friendly')

    if airport_km <= 75:
        tags.add('Short Airport Transfer')

    if name_lower in RAIL_TO_SLOPES_RESORTS:
        tags.add('Rail-to-Slopes Access')

    # ------------------------------------------------------------------
    # Optional Layer 3: DOM-Scoped Live Skiresort.info Scraping
    # ------------------------------------------------------------------
    scraped_tags = set()
    if live_scrape:
        scraped_tags = scrape_skiresort_features(name)
        for st in scraped_tags:
            if st == 'Good for Beginners' and name_lower in NOT_BEGINNER_RESORTS:
                continue
            tags.add(st)
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
    source_mode = "Hybrid v3 (Audited Guardrails + Live DOM)" if scraped_tags else "Hybrid v3 (Audited & Sense-Checked)"
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
