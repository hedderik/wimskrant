#!/usr/bin/env python3
"""WimsKrant — rustige leesweergave van krantenartikelen met geheugensteun in de kantlijn.

Start:   python3 server.py        en open dan http://localhost:8765

De server toont de voorpagina van NRC of Trouw, haalt een gekozen artikel op en zet er
een begrippenlijst naast. Die lijst maakt Claude (ANTHROPIC_API_KEY): wat het
artikel zelf vertelt over personen en begrippen komt later in de kantlijn terug.
De lezer kan ook zelf op een woord klikken; dan zoekt Claude de uitleg op.
"""

import hashlib
import hmac
import html
import http.cookiejar
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Instellingen uit .env (bijv. ANTHROPIC_API_KEY=...) gelden als ze nog niet in de omgeving staan.
if (ROOT / ".env").is_file():
    for regel in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        naam, _, waarde = regel.strip().partition("=")
        if naam and not naam.startswith("#") and waarde:
            os.environ.setdefault(naam.removeprefix("export ").strip(), waarde.strip().strip("'\""))

STATIC = ROOT / "static"
# Wat de server bewaart (uitleg per artikel, sleutel voor de toegangscookies). Bij hosting is dit
# een blijvende schijf, bijv. WIMSKRANT_DATA=/pad/naar/map; anders deze map.
DATA = Path(os.environ.get("WIMSKRANT_DATA") or ROOT)
BEGRIPPEN = DATA / "begrippen"
PORT = int(os.environ.get("PORT", "8765"))
# 0.0.0.0 = ook bereikbaar voor andere apparaten op het thuisnetwerk (bijv. een iPad)
HOST = os.environ.get("WIMSKRANT_HOST", "127.0.0.1")
MODEL = os.environ.get("WIMSKRANT_MODEL", "claude-sonnet-5-5")
MODEL_SNEL = os.environ.get("WIMSKRANT_MODEL_SNEL", "claude-sonnet-5-5")
VERSIE = 2  # versie van de begrippenlijst; oudere lijsten worden opnieuw gemaakt
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130 Safari/537.36")

# Blokken met deze tekst horen niet bij het artikel zelf.
STOP_ZINNEN = ("voorkeursbron", "Bij Google kan je nu zelf", "journalistieke principes",
               "cadeaulink", "Deel dit artikel")

# Soorten stukken op de voorpagina die niet als gewoon artikel te lezen zijn.
GEEN_ARTIKEL = {"podcast", "liveblog", "nieuwsbrief", "inbeeld", "slowblog", "blogbericht", "blogberichtplus"}
GEEN_SECTIE = {"Mijn nieuws", "Nieuwste afleveringen NRC-podcasts", "Podcasts"}

# Rubrieken voor het snelmenu: slug -> naam. Bij NRC is dat de pagina nrc.nl/index/<slug>/,
# bij Trouw de RSS-feed trouw.nl/<slug>/rss.xml.
RUBRIEKEN = {
    "nrc": {
        "binnenland": "Binnenland", "buitenland": "Buitenland", "den-haag": "Politiek",
        "economie": "Economie", "opinie": "Opinie", "cultuur": "Cultuur", "boeken": "Boeken",
        "wetenschap": "Wetenschap", "tech-en-media": "Tech en media", "recht": "Recht",
        "sport": "Sport", "leven": "Leven",
    },
    "trouw": {
        "binnenland": "Binnenland", "buitenland": "Buitenland", "politiek": "Politiek",
        "verdieping": "Verdieping", "duurzaamheid-economie": "Duurzaamheid", "wetenschap": "Wetenschap",
        "gezondheid": "Gezondheid", "onderwijs": "Onderwijs", "religie-filosofie": "Zingeving",
        "opinie": "Opinie", "cultuur-media": "Cultuur en media", "boeken": "Boeken", "sport": "Sport",
    },
}

# Toegangscodes: WIMSKRANT_CODES=wim:code1,anna:code2. Zonder codes is de app voor iedereen open
# (prima thuis op de Mac, niet op internet). Een goede code onthoudt de browser een jaar.
CODES = {}
for _i, _deel in enumerate(filter(None, (x.strip() for x in os.environ.get("WIMSKRANT_CODES", "").split(",")))):
    _naam, _, _code = _deel.rpartition(":")
    CODES[_naam.strip() or f"code{_i + 1}"] = _code.strip()
TOEGANG_DAGEN = 365
VRIJ = {"/toegang", "/api/toegang", "/style.css", "/manifest.webmanifest", "/icoon-180.png", "/icoon-512.png"}
POGINGEN = {}             # ip -> tijden van foute pogingen
POGING_SLOT = threading.Lock()
LOKAAL = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}  # een proxy op deze computer zelf
PROXY_GEMELD = False

ARTIKELEN = {}            # sleutel -> artikel (geheugen, zodat we niet steeds opnieuw ophalen)
SLOTEN = {}               # sleutel -> lock, zodat een begrippenlijst maar één keer tegelijk wordt gemaakt
SLOT = threading.Lock()
VOORPAGINA = {}          # krant -> {"tijd", "data"}

# Trouw: secties uit de RSS-feed, in deze volgorde. Cartoons en recepten zijn geen leesstukken.
TROUW_SECTIES = {
    "binnenland": "Binnenland", "buitenland": "Buitenland", "politiek": "Politiek",
    "verdieping": "Verdieping", "duurzaamheid-economie": "Duurzaamheid en economie",
    "wetenschap": "Wetenschap", "gezondheid": "Gezondheid", "zorg": "Zorg", "onderwijs": "Onderwijs",
    "religie-filosofie": "Religie en filosofie", "opinie": "Opinie", "columnisten": "Columns",
    "cultuur-media": "Cultuur en media", "boeken": "Boeken", "recensies": "Recensies",
    "sport": "Sport", "achterpagina": "Achterpagina",
}
TROUW_NIET = {"cartoons", "recepten", "puzzels", "podcasts"}

# Trouw (DPG Media) zet elke pagina achter een cookiekeuze; de server kiest daar alleen
# functionele cookies (zie haal_op) en bewaart die in deze cookiepot.
DPG_POT = http.cookiejar.CookieJar()
DPG = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(DPG_POT))


def trouw_inloggen():
    """Met TROUW_COOKIE (de cookie uit je eigen ingelogde browser) haalt de server Trouw op als abonnee.
    De cookies gaan in DPG_POT, zodat ze samen met de cookiekeuze worden meegestuurd."""
    for deel in os.environ.get("TROUW_COOKIE", "").split(";"):
        naam, _, waarde = deel.strip().partition("=")
        if naam and waarde:
            DPG_POT.set_cookie(http.cookiejar.Cookie(
                0, naam, waarde, None, False, ".trouw.nl", True, True, "/", True, True,
                None, False, None, None, {}))


trouw_inloggen()


def krant_van(url):
    netloc = urllib.parse.urlparse(url).netloc
    return "trouw" if netloc.endswith("trouw.nl") else "nrc" if netloc.endswith("nrc.nl") else ""


def artikel_sleutel(url):
    """Vaste sleutel per artikel, los van tracking- en cadeauparameters."""
    p = urllib.parse.urlparse(url)
    m = re.search(r"-a(\d{6,})$", p.path.rstrip("/"))
    if "nrc.nl" in p.netloc and m:
        return "nrc-" + m.group(1)
    m = re.search(r"~(b[0-9a-f]{6,})$", p.path.rstrip("/"))
    if "trouw.nl" in p.netloc and m:
        return "trouw-" + m.group(1)
    return hashlib.sha1((p.netloc + p.path).encode()).hexdigest()[:16]


def haal_op(url):
    headers = {"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9"}
    # Met een NRC-cookie (uit je eigen ingelogde browser) haalt de server artikelen op als abonnee.
    if os.environ.get("NRC_COOKIE") and urllib.parse.urlparse(url).netloc.endswith("nrc.nl"):
        headers["Cookie"] = os.environ["NRC_COOKIE"]
    if krant_van(url) == "trouw":
        return haal_op_dpg(url, headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")


def haal_op_dpg(url, headers):
    """Trouw: komen we bij de cookiekeuze van DPG uit, dan gaan we verder via de link die de
    keuzepagina zelf volgt na 'alleen functionele cookies'. Die keuze blijft in DPG_POT bewaard."""
    for _ in range(2):
        with DPG.open(urllib.request.Request(url, headers=headers), timeout=20) as r:
            doc = r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
            if "myprivacy.dpgmedia.nl" not in r.geturl():
                return doc
        m = re.search(r"decodeURIComponent\('([^']+)'\)", doc)
        if not m:
            break
        url = urllib.parse.unquote(m.group(1))
    raise urllib.error.URLError("Trouw vraagt om een cookiekeuze die de server niet kon geven")


def schoon(t):
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def zonder_tags(t):
    return schoon(re.sub(r"<[^>]+>", " ", t))


def meta(doc, prop):
    m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(prop), doc)
    return schoon(m.group(1)) if m else ""


# ---------- Voorpagina ----------

def lees_voorpagina(krant, rubriek=""):
    """Artikelen van de voorpagina of een rubriek van de gekozen krant, per sectie; tien minuten bewaard."""
    oud = VOORPAGINA.get((krant, rubriek))
    if oud and time.time() - oud["tijd"] < 600:
        return oud["data"]
    if krant == "trouw":
        data = voorpagina_trouw(rubriek)
    else:
        data = voorpagina_nrc(f"https://www.nrc.nl/index/{rubriek}/" if rubriek else "https://www.nrc.nl/")
    VOORPAGINA[(krant, rubriek)] = {"tijd": time.time(), "data": data}
    return data


def voorpagina_trouw(rubriek=""):
    """Trouw: de RSS-feed met de nieuwste stukken (van één rubriek), gegroepeerd per sectie."""
    root = ET.fromstring(haal_op(f"https://www.trouw.nl/{rubriek}/rss.xml" if rubriek else "https://www.trouw.nl/rss.xml"))
    per_sectie, gezien = {}, set()
    urls = [(i.findtext("link") or "").strip() for i in root.iter("item")]
    for item in root.iter("item"):
        url = (item.findtext("link") or "").strip()
        p = urllib.parse.urlparse(url).path.strip("/").split("/")
        if not url or url in gezien or p[0] in TROUW_NIET:
            continue
        gezien.add(url)
        beeld = next((m.get("url") for m in item if m.tag.endswith("}content") and m.get("url")), "")
        per_sectie.setdefault(p[0], []).append({
            "url": url,
            "titel": schoon(item.findtext("title") or ""),
            "vlag": "",
            "teaser": zonder_tags(item.findtext("description") or ""),
            "beeld": re.sub(r"/feature-crop/\d+/\d+/", "/width/640/", beeld),
        })
    if rubriek:  # één lijst, nieuwste eerst, zoals de feed ze geeft
        return [{"naam": RUBRIEKEN["trouw"][rubriek],
                 "artikelen": sorted((a for lijst in per_sectie.values() for a in lijst),
                                     key=lambda a: urls.index(a["url"]))}]
    volgorde = list(TROUW_SECTIES) + sorted(set(per_sectie) - set(TROUW_SECTIES))
    return [{"naam": TROUW_SECTIES.get(s, s.replace("-", " ").capitalize()), "artikelen": per_sectie[s]}
            for s in volgorde if s in per_sectie]


def voorpagina_nrc(adres):
    """Artikelen van nrc.nl (voorpagina of een indexpagina), per sectie, in de volgorde van de pagina."""
    doc = haal_op(adres)
    stukken = [(m.start(), "sectie", zonder_tags(m.group(1)))
               for m in re.finditer(r'<h2 class="dmt-segment-header__heading">(.*?)</h2>', doc, re.S)]
    stukken += [(m.start(), "artikel", m) for m in re.finditer(
        r'<article\s[^>]*data-article-id="(\d+)"[^>]*data-article-type="([^"]*)".*?</article>', doc, re.S)]
    stukken.sort(key=lambda x: x[0])

    secties, gezien, huidig = [], set(), {"naam": "Voorpagina", "artikelen": []}
    for _, soort, x in stukken:
        if soort == "sectie":
            if huidig["artikelen"]:
                secties.append(huidig)
            huidig = {"naam": x, "artikelen": []}
            continue
        nr, type_, blok = x.group(1), x.group(2), x.group(0)
        if nr in gezien or type_ in GEEN_ARTIKEL or huidig["naam"] in GEEN_SECTIE:
            continue
        href = re.search(r'<a href="(/[^"#]+-a\d+)"', blok)
        kop = re.search(r'class="[^"]*dmt-item-headline[^"]*"[^>]*>(.*?)</h3>', blok, re.S)
        if not href or not kop:
            continue
        gezien.add(nr)
        vlag = re.search(r'dmt-item-flag[^"]*"\s*>(.*?)</p>', blok, re.S)
        teaser = re.search(r'class="dmt-item-teaser"[^>]*>(.*?)</p>', blok, re.S)
        img = re.search(r'srcset="[^"]*?(https://\S+?)\s+640w', blok) or re.search(r'<img[^>]+src="(https://[^"]+)"', blok)
        huidig["artikelen"].append({
            "url": "https://www.nrc.nl" + href.group(1),
            "titel": zonder_tags(kop.group(1)),
            "vlag": zonder_tags(vlag.group(1)) if vlag else "",
            "teaser": zonder_tags(teaser.group(1)) if teaser else "",
            "beeld": img.group(1) if img else "",
        })
    if huidig["artikelen"]:
        secties.append(huidig)
    return secties


# ---------- Artikel ----------

class BlokParser(HTMLParser):
    """Zet de artikel-HTML om in een lijst blokken: alinea's, tussenkoppen en foto's."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blokken = []
        self.huidig = None     # ("p" | "h2" | "h3", [tekst])
        self.in_figuur = 0
        self.in_bijschrift = None  # tekst uit <figcaption> (Trouw)
        self.in_overslaan = 0  # citaten, scripts, stijlen

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "blockquote", "noscript", "button"):
            self.in_overslaan += 1
        elif tag == "figure":
            self.in_figuur += 1
            self.blokken.append({"soort": "foto", "src": "", "bijschrift": schoon(a.get("data-description", ""))})
        elif tag == "img" and self.in_figuur and self.blokken and self.blokken[-1]["soort"] == "foto":
            groot = re.search(r"(https://\S+)\s+1080w", a.get("srcset") or "")
            src = groot.group(1) if groot else a.get("src") or ""
            if not src.startswith("http"):
                src = a.get("data-src") or ""
            if src and not self.blokken[-1]["src"]:
                self.blokken[-1]["src"] = src
        elif tag in ("p", "h2", "h3") and not self.in_figuur and not self.in_overslaan:
            self.huidig = (tag, [])
        elif tag == "figcaption" and self.in_figuur:
            self.in_bijschrift = []
        elif tag == "cite" and self.in_bijschrift is not None:
            self.in_overslaan += 1  # fotograaf
        elif tag == "br" and self.huidig:
            self.huidig[1].append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "blockquote", "noscript", "button") or (
                tag == "cite" and self.in_bijschrift is not None):
            self.in_overslaan = max(0, self.in_overslaan - 1)
        elif tag == "figcaption" and self.in_bijschrift is not None:
            tekst = re.sub(r"\s*\bBron\s*$", "", schoon(" ".join(self.in_bijschrift)))
            if self.blokken and self.blokken[-1]["soort"] == "foto" and not self.blokken[-1]["bijschrift"]:
                self.blokken[-1]["bijschrift"] = tekst
            self.in_bijschrift = None
        elif tag == "figure":
            self.in_figuur = max(0, self.in_figuur - 1)
        elif self.huidig and tag == self.huidig[0]:
            tekst = schoon("".join(self.huidig[1]))
            if tekst:
                self.blokken.append({"soort": "kop" if tag != "p" else "alinea", "tekst": tekst})
            self.huidig = None

    def handle_data(self, data):
        if self.in_bijschrift is not None and not self.in_overslaan:
            self.in_bijschrift.append(data)
        elif self.huidig and not self.in_overslaan:
            self.huidig[1].append(data)


def lees_artikel(url):
    doc = haal_op(url)

    def eerste(patroon):
        m = re.search(patroon, doc, re.S)
        return zonder_tags(m.group(1)) if m else ""

    titel = (eerste(r'class="dmt-header__headline[^"]*"[^>]*>(.*?)</h1>')
             or eerste(r'data-test-id="article-title"[^>]*>(.*?)</h1>') or meta(doc, "og:title"))
    intro = (eerste(r'class="dmt-header__abstract[^"]*"[^>]*>(.*?)</(?:p|div)>')
             or eerste(r'data-test-id="header-intro"[^>]*>(.*?)</p>') or meta(doc, "og:description"))
    # NRC zet een rubriekwoord voor de intro ("Armoede ..."); dat halen we eruit.
    rubriek = eerste(r'class="dmt-header__abstract.{0,200}?<span class="keyword">(.*?)</span>')
    if rubriek and intro.startswith(rubriek):
        intro = intro[len(rubriek):].strip()
    auteurs = list(dict.fromkeys(schoon(a) for a in re.findall(r'class="dmt-byline__author-name[^"]*"[^>]*>([^<]+)<', doc)
                                 + re.findall(r'data-test-id="article-author"[^>]*>.*?<a[^>]*>([^<]+)</a>', doc)))
    datum = (re.findall(r'<time[^>]*datetime="(\d{4}-\d\d-\d\d)', doc, re.I) or [meta(doc, "article:published_time")[:10]])[0]

    # Het stuk HTML met de artikeltekst zoeken (NRC, Trouw, anders <article>, anders alles).
    i = doc.find("vorm__article-content dmt-grid-center-s")
    k = doc.find('data-article-element-index="0"')
    if i >= 0:
        j = doc.find("vorm__article-footer", i)
        stuk = doc[i:j if j > 0 else len(doc)]
    elif k >= 0:
        j = doc.find('data-test-id="editorial-tips"', k)
        stuk = doc[k - 40:j if j > 0 else len(doc)]
    else:
        m = re.search(r"<article\b.*?</article>", doc, re.S)
        stuk = m.group(0) if m else doc

    p = BlokParser()
    p.feed(stuk)
    blokken = []
    nr = 2  # 0 = titel, 1 = intro; daarna telt elk tekstblok door
    for b in p.blokken:
        tekst = b.get("tekst", b.get("bijschrift", ""))
        if any(z in tekst for z in STOP_ZINNEN):
            if b["soort"] == "kop":
                break
            continue
        if b["soort"] == "foto":
            if not b["src"]:
                continue
        else:
            b["nr"] = nr
            nr += 1
        blokken.append(b)

    art = {
        "url": url,
        "sleutel": artikel_sleutel(url),
        "bron": urllib.parse.urlparse(url).netloc.replace("www.", ""),
        "krant": krant_van(url),
        "titel": titel,
        "intro": intro,
        "auteurs": auteurs,
        "datum": datum,
        "beeld": meta(doc, "og:image"),
        "blokken": blokken,
    }
    ARTIKELEN[art["sleutel"]] = art
    return art


def artikel_uit_geheugen(url):
    return ARTIKELEN.get(artikel_sleutel(url)) or lees_artikel(url)


def artikel_als_tekst(art, tot=None):
    """Artikeltekst met bloknummers, zodat Claude kan zeggen waar iets staat."""
    regels = [f"[0] {art['titel']}", f"[1] {art['intro']}"]
    for b in art["blokken"]:
        if "nr" not in b or (tot is not None and b["nr"] > tot):
            continue
        regels.append(f"[{b['nr']}] " + ("## " if b["soort"] == "kop" else "") + b["tekst"])
    return "\n\n".join(regels)


# ---------- Claude ----------

def vraag_claude(opdracht, model, max_tokens):
    sleutel = os.environ.get("ANTHROPIC_API_KEY")
    if not sleutel:
        return None
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": opdracht}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": sleutel,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=300) as r:
        antwoord = json.load(r)
    tekst = "".join(c.get("text", "") for c in antwoord.get("content", []) if c.get("type") == "text")
    m = re.search(r"\{.*\}", tekst, re.S)
    return json.loads(m.group(0)) if m else None


OPDRACHT_BEGRIPPEN = """Je helpt een lezer met (beginnende) dementie een krantenartikel te volgen.

Belangrijk over deze lezer:
- Algemene kennis is er nog: wie Trump is, wat een dollar of een gemeente is, waar Parijs ligt.
  Leg zulke dingen dus NIET uit.
- Wat de lezer wel snel vergeet, is wat EERDER IN HET ARTIKEL stond: wie een persoon is die
  het artikel voorstelt, wat een afkorting betekent die het artikel uitlegt, wat er met iemand
  gebeurde. Als dat later in het artikel terugkomt, moet het in de kantlijn herhaald worden.

Het artikel staat hieronder; elk blok heeft een nummer tussen [ ].

Maak een lijst van alle personen, organisaties, plaatsen, begrippen, afkortingen en gebeurtenissen
die in het artikel MEER DAN EENS voorkomen (ook als ze anders genoemd worden, bijv. eerst
"Vivian Manriquez" en later "Manriquez" of "de moeder"), plus moeilijke vaktermen.
Noteer voor elk wat het artikel er zelf over vertelt, per bloknummer.

Schrijf in eenvoudig Nederlands, korte zinnen, zonder nieuwe moeilijke woorden.

Geef ALLEEN geldige JSON terug, in deze vorm:
{
  "samenvatting": ["3 tot 5 heel korte zinnen: waar gaat dit artikel over?"],
  "termen": [
    {
      "term": "naam of woord",
      "vormen": ["alle schrijfwijzen zoals ze LETTERLIJK in de tekst staan, ook achternaam alleen, meervoud, samenstellingen"],
      "soort": "persoon" | "plaats" | "organisatie" | "begrip",
      "rol": "wie of wat dit is in dit artikel, in hooguit 8 woorden, alleen met wat bij de eerste vermelding bekend is",
      "feiten": [{"nr": 3, "feit": "korte zin: wat het artikel in blok 3 hierover vertelt"}],
      "uitleg": "ALLEEN invullen voor vaktermen, Engelse woorden of afkortingen die een gewone krantenlezer niet kent en die het artikel zelf niet uitlegt; anders leeg laten",
      "kort": "geheugensteuntje van hooguit 8 woorden bij die uitleg; anders leeg"
    }
  ]
}

Feiten: alleen wat in het artikel staat, het belangrijkste per blok, hooguit één feit per blok.
Neem alleen feiten op die later nog helpen om het verhaal te volgen.
"""

OPDRACHT_UITLEG = """Je helpt een lezer met (beginnende) dementie een krantenartikel te volgen.
De lezer klikte op "{woord}" in blok [{nr}] en wil weten wat dat betekent.

Hieronder staat het artikel tot en met blok [{nr}].

Regels:
- Heeft het artikel EERDER iets verteld over "{woord}" (wie het is, wat het betekent, wat er gebeurde),
  begin dan met dat te herhalen: "Eerder in dit artikel: ...".
- Heeft het artikel er eerder NIETS over gezegd, zeg dat dan ook niet; leg het gewoon uit
  zoals het in deze zin bedoeld is.
- Eenvoudig Nederlands, hooguit 3 korte zinnen, zonder nieuwe moeilijke woorden.
- Zeg niet dat de lezer dementie heeft en spreek de lezer niet aan.

Geef ALLEEN JSON terug: {{"woord": "het woord of de woorden zoals bedoeld", "uitleg": "..."}}

ARTIKEL:

{tekst}
"""


def begrippen_pad(sleutel):
    return BEGRIPPEN / (sleutel + ".json")


def bewaarde_begrippen(sleutel):
    pad = begrippen_pad(sleutel)
    if pad.exists():
        lijst = json.loads(pad.read_text(encoding="utf-8"))
        if lijst.get("versie", 1) >= VERSIE or not os.environ.get("ANTHROPIC_API_KEY"):
            return lijst
    return None


def maak_begrippen(art):
    with SLOT:
        slot = SLOTEN.setdefault(art["sleutel"], threading.Lock())
    with slot:  # een tweede aanvraag wacht en krijgt dan de bewaarde lijst
        lijst = bewaarde_begrippen(art["sleutel"])
        if lijst:
            return lijst
        lijst = vraag_claude(OPDRACHT_BEGRIPPEN + "\n\nARTIKEL:\n\n" + artikel_als_tekst(art), MODEL, 32000)
        if lijst:
            lijst["versie"] = VERSIE
            BEGRIPPEN.mkdir(parents=True, exist_ok=True)
            begrippen_pad(art["sleutel"]).write_text(json.dumps(lijst, ensure_ascii=False, indent=2), encoding="utf-8")
        return lijst


def zoek_uitleg(url, woord, nr):
    art = artikel_uit_geheugen(url)
    pad = BEGRIPPEN / (art["sleutel"] + "-opgezocht.json")
    opgezocht = json.loads(pad.read_text(encoding="utf-8")) if pad.exists() else {}
    sleutel = f"{woord.lower()}|{nr}"
    if sleutel in opgezocht:
        return opgezocht[sleutel]
    antwoord = vraag_claude(OPDRACHT_UITLEG.format(woord=woord, nr=nr, tekst=artikel_als_tekst(art, tot=nr)),
                            MODEL_SNEL, 1000)
    if antwoord and antwoord.get("uitleg"):
        with SLOT:
            opgezocht = json.loads(pad.read_text(encoding="utf-8")) if pad.exists() else {}
            opgezocht[sleutel] = antwoord
            BEGRIPPEN.mkdir(parents=True, exist_ok=True)
            pad.write_text(json.dumps(opgezocht, ensure_ascii=False, indent=2), encoding="utf-8")
    return antwoord


# ---------- Webserver ----------

# ---------- Toegang ----------

def geheim():
    """Sleutel waarmee de server zijn toegangscookies ondertekent; staat in .geheim (niet delen)."""
    pad = DATA / ".geheim"
    if not pad.is_file():
        DATA.mkdir(parents=True, exist_ok=True)
        pad.write_text(secrets.token_hex(32))
        pad.chmod(0o600)
    return pad.read_text().strip().encode()


def handtekening(naam, tot):
    # De code zelf telt mee: haal je een code weg of verander je hem, dan vervalt de cookie.
    return hmac.new(geheim(), f"{naam}|{tot}|{CODES.get(naam, '')}".encode(), hashlib.sha256).hexdigest()


def maak_toegang(naam):
    tot = int(time.time()) + TOEGANG_DAGEN * 86400
    return f"{urllib.parse.quote(naam)}|{tot}|{handtekening(naam, tot)}"


def geldige_toegang(waarde):
    try:
        naam, tot, sig = waarde.split("|")
        naam, tot = urllib.parse.unquote(naam), int(tot)
    except ValueError:
        return None
    if naam in CODES and tot > time.time() and hmac.compare_digest(sig, handtekening(naam, tot)):
        return naam
    return None


def code_van(code):
    """Naam bij een ingevulde code, of None. Vergelijkt in vaste tijd."""
    gevonden = None
    for naam, juist in CODES.items():
        if hmac.compare_digest(code.strip().encode(), juist.encode()):
            gevonden = naam
    return gevonden


def te_veel_pogingen(ip, fout=False):
    """Hooguit 5 foute pogingen per adres en 30 in totaal per kwartier."""
    nu = time.time()
    with POGING_SLOT:
        for k in list(POGINGEN):
            POGINGEN[k] = [t for t in POGINGEN[k] if nu - t < 900]
            if not POGINGEN[k]:
                del POGINGEN[k]
        if fout:
            POGINGEN.setdefault(ip, []).append(nu)
        return len(POGINGEN.get(ip, [])) >= 5 or sum(map(len, POGINGEN.values())) >= 30


class Handler(BaseHTTPRequestHandler):
    def stuur(self, code, data, soort="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", soort)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def doorsturen(self, adres, cookie=None):
        self.send_response(303)
        self.send_header("Location", adres)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def via_proxy(self):
        # Alleen een proxy op deze computer zelf (zoals Tailscale Funnel) mag X-Forwarded-* zetten;
        # van andere apparaten in het netwerk worden die regels genegeerd, zodat niemand ze kan vervalsen.
        global PROXY_GEMELD
        if self.client_address[0] not in LOKAAL:
            return False
        if not PROXY_GEMELD:
            PROXY_GEMELD = True
            print("Proxy gezien: echt bezoekersadres "
                  + ("doorgegeven" if self.headers.get("X-Forwarded-For") else "ONTBREEKT")
                  + ", https " + ("doorgegeven" if self.headers.get("X-Forwarded-Proto") else "ONTBREEKT")
                  + " (headers: " + ", ".join(sorted(self.headers.keys())) + ")",
                  flush=True)
        return True

    def via_https(self):
        return self.via_proxy() and self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def ip(self):
        # Achter de proxy staat het echte adres in X-Forwarded-For (het laatste is door de proxy zelf gezet).
        if self.via_proxy() and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[-1].strip()
        return self.client_address[0]

    def heeft_toegang(self, pad):
        if not CODES or pad in VRIJ:
            return True
        koekjes = {}
        for deel in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = deel.strip().partition("=")
            koekjes[k] = v
        return bool(geldige_toegang(koekjes.get("wk_toegang", "")))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/toegang":
            return self.stuur(404, {"fout": "Niet gevonden."})
        lengte = min(int(self.headers.get("Content-Length") or 0), 4000)
        velden = urllib.parse.parse_qs(self.rfile.read(lengte).decode("utf-8", "replace"))
        terug = (velden.get("terug") or ["/"])[0]
        if not terug.startswith("/") or terug.startswith("//"):
            terug = "/"
        verder = urllib.parse.quote(terug, safe="")
        if te_veel_pogingen(self.ip()):
            return self.doorsturen(f"/toegang?fout=wacht&terug={verder}")
        naam = code_van((velden.get("code") or [""])[0])
        if not naam:
            te_veel_pogingen(self.ip(), fout=True)
            return self.doorsturen(f"/toegang?fout=code&terug={verder}")
        print(f"[wimskrant] toegang voor {naam}", file=sys.stderr)
        cookie = (f"wk_toegang={maak_toegang(naam)}; Max-Age={TOEGANG_DAGEN * 86400}; Path=/; "
                  "HttpOnly; SameSite=Lax" + ("; Secure" if self.via_https() else ""))
        self.doorsturen(terug, cookie)

    def url_param(self, q):
        url = (q.get("url") or [""])[0].strip()
        return url if url.startswith(("http://", "https://")) else None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if not self.heeft_toegang(u.path):
            if u.path.startswith("/api/"):
                return self.stuur(401, {"fout": "Vul eerst uw toegangscode in."})
            terug = urllib.parse.quote(u.path + ("?" + u.query if u.query else ""), safe="")
            return self.doorsturen("/toegang?terug=" + terug)

        if u.path == "/api/voorpagina":
            krant = (q.get("krant") or ["nrc"])[0]
            rubriek = (q.get("rubriek") or [""])[0]
            if krant not in RUBRIEKEN or (rubriek and rubriek not in RUBRIEKEN[krant]):
                return self.stuur(400, {"fout": "Onbekende krant of rubriek."})
            rubrieken = [{"slug": k, "naam": v} for k, v in RUBRIEKEN[krant].items()]
            try:
                return self.stuur(200, {"secties": lees_voorpagina(krant, rubriek), "rubrieken": rubrieken})
            except (urllib.error.URLError, TimeoutError, ET.ParseError) as e:
                return self.stuur(502, {"fout": f"De voorpagina kon niet worden opgehaald ({e})."})

        if u.path == "/api/artikel":
            url = self.url_param(q)
            if not url:
                return self.stuur(400, {"fout": "Dat lijkt geen geldige internetlink."})
            try:
                art = lees_artikel(url)
            except (urllib.error.URLError, TimeoutError) as e:
                return self.stuur(502, {"fout": f"Het artikel kon niet worden opgehaald ({e})."})
            if not art["blokken"]:
                return self.stuur(422, {"fout": "Er is geen artikeltekst gevonden op deze pagina. "
                                                 "Is het een link naar één artikel?"})
            lijst = bewaarde_begrippen(art["sleutel"])
            kan_maken = bool(os.environ.get("ANTHROPIC_API_KEY"))
            return self.stuur(200, dict(art, begrippen=lijst, kan_uitleggen=kan_maken))

        if u.path == "/api/begrippen":
            url = self.url_param(q)
            if not url:
                return self.stuur(400, {"fout": "Geen link."})
            try:
                lijst = maak_begrippen(artikel_uit_geheugen(url))
            except Exception as e:
                print("Begrippenlijst mislukt:", e, file=sys.stderr)
                return self.stuur(500, {"fout": "Het maken van de uitleg is mislukt."})
            return self.stuur(200 if lijst else 503, lijst or {"fout": "Geen ANTHROPIC_API_KEY ingesteld."})

        if u.path == "/api/uitleg":
            url = self.url_param(q)
            woord = (q.get("woord") or [""])[0].strip()[:80]
            nr = int((q.get("nr") or ["0"])[0] or 0)
            if not url or not woord:
                return self.stuur(400, {"fout": "Geen woord."})
            try:
                antwoord = zoek_uitleg(url, woord, nr)
            except Exception as e:
                print("Uitleg mislukt:", e, file=sys.stderr)
                antwoord = None
            if not antwoord:
                return self.stuur(503, {"fout": "Er kon nu geen uitleg worden gevonden."})
            return self.stuur(200, antwoord)

        if u.path == "/toegang":
            return self.stuur(200, (STATIC / "toegang.html").read_bytes(), "text/html; charset=utf-8")
        pagina = u.path in ("/", "/lees") or u.path.strip("/").split("/")[0] in RUBRIEKEN
        bestand = "index.html" if pagina else u.path.lstrip("/")
        pad = (STATIC / bestand).resolve()
        if STATIC not in pad.parents or not pad.is_file():
            return self.stuur(404, b"Niet gevonden", "text/plain; charset=utf-8")
        soorten = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
                   ".png": "image/png", ".webmanifest": "application/manifest+json"}
        soort = soorten.get(pad.suffix, "application/octet-stream")
        self.stuur(200, pad.read_bytes(), soort + ("; charset=utf-8" if soort.startswith("text") else ""))

    def log_message(self, fmt, *args):
        sys.stderr.write("[wimskrant] " + fmt % args + "\n")


def netwerkadres():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 1))  # er wordt niets verstuurd; zo vinden we het eigen adres
            return s.getsockname()[0]
    except OSError:
        return None


def controleer_kranten():
    """Kijkt bij het starten of NRC en Trouw vanaf dit adres te lezen zijn (handig bij hosting)."""
    for krant, naam in (("nrc", "NRC"), ("trouw", "Trouw")):
        try:
            secties = lees_voorpagina(krant)
            eerste = secties[0]["artikelen"][0]
            alineas = sum(1 for b in lees_artikel(eerste["url"])["blokken"] if b["soort"] == "alinea")
            aantal = sum(len(s["artikelen"]) for s in secties)
            oordeel = "werkt" if alineas >= 3 else "LET OP: weinig artikeltekst"
            print(f"Controle {naam}: {oordeel} ({aantal} artikelen; eerste artikel {alineas} alinea's)", flush=True)
        except urllib.error.HTTPError as e:
            uitleg = " (de krant weigert dit serveradres)" if e.code in (401, 403, 429) else ""
            print(f"Controle {naam}: LUKT NIET (HTTP {e.code}{uitleg}; {e.url})", flush=True)
        except Exception as e:
            print(f"Controle {naam}: LUKT NIET ({e})", flush=True)
    if os.environ.get("TROUW_COOKIE"):
        print("Trouw wordt opgehaald met TROUW_COOKIE (ingelogd).", flush=True)


if __name__ == "__main__":
    print(f"WimsKrant draait op http://localhost:{PORT}")
    if CODES:
        print(f"Toegang alleen met een code ({len(CODES)}: {', '.join(CODES)}).")
    elif HOST != "127.0.0.1":
        print("Let op: geen WIMSKRANT_CODES ingesteld, dus iedereen die het adres kent kan erbij.")
    print(f"Gegevens worden bewaard in {DATA}")
    threading.Thread(target=controleer_kranten, daemon=True).start()
    if HOST == "0.0.0.0" and netwerkadres():
        print(f"Op een iPad in hetzelfde wifi-netwerk: http://{netwerkadres()}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
