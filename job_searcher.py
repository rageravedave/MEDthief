"""
Job Searcher — kombiniert mehrere Quellen:
  1. Pflegia          — pflegia.de via jobposition-sitemaps + Apollo SSR
  2. Indeed/LinkedIn  — python-jobspy (erfordert Python 3.11)
  3. Pflegejobs       — pflegejobs.de HTML-Scraping (Pflege-spezifisch)
  4. Medi-Karriere    — medi-karriere.de HTML-Scraping
  5. Kliniken.de      — kliniken.de HTML-Scraping
  6. Gesundheit.jobs  — gesundheit.jobs HTML-Scraping
"""

import re
import math
import json as _json
import subprocess
import time
import urllib.parse
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── In-Memory Cache (TTL 5 Minuten) ─────────────────────────────────────────
_SEARCH_CACHE: dict = {}
_CACHE_TTL = 300  # seconds

# ── Persistenter Domain-Cache ────────────────────────────────────────────────
# Firma → Domain-URL. Domains ändern sich selten → persistent auf Disk,
# damit wir nach dem App-Neustart nicht alle Domains erneut raten müssen.
import os
_DOMAIN_CACHE_PATH = os.path.expanduser("~/.medthief_domain_cache.json")
_DOMAIN_CACHE_TTL = 30 * 24 * 3600  # 30 Tage
_DOMAIN_CACHE: dict = {}  # key: company_lower → {"url": str, "ts": float}
_DOMAIN_CACHE_DIRTY = False


def _load_domain_cache() -> None:
    """Lädt den Domain-Cache vom Disk beim Import."""
    global _DOMAIN_CACHE
    try:
        if os.path.exists(_DOMAIN_CACHE_PATH):
            with open(_DOMAIN_CACHE_PATH, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if isinstance(data, dict):
                now = time.time()
                _DOMAIN_CACHE = {
                    k: v for k, v in data.items()
                    if isinstance(v, dict) and (now - v.get("ts", 0)) < _DOMAIN_CACHE_TTL
                }
    except Exception:
        _DOMAIN_CACHE = {}


def _save_domain_cache() -> None:
    """Schreibt den Domain-Cache atomar zurück auf Disk."""
    global _DOMAIN_CACHE_DIRTY
    if not _DOMAIN_CACHE_DIRTY:
        return
    try:
        tmp = _DOMAIN_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(_DOMAIN_CACHE, f, ensure_ascii=False)
        os.replace(tmp, _DOMAIN_CACHE_PATH)
        _DOMAIN_CACHE_DIRTY = False
    except Exception:
        pass


def _domain_cache_get(company: str) -> Optional[str]:
    entry = _DOMAIN_CACHE.get(company.lower().strip())
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > _DOMAIN_CACHE_TTL:
        return None
    return entry.get("url", "")  # "" = Negativ-Cache


def _domain_cache_set(company: str, url: str) -> None:
    global _DOMAIN_CACHE_DIRTY
    if not company:
        return
    _DOMAIN_CACHE[company.lower().strip()] = {"url": url or "", "ts": time.time()}
    _DOMAIN_CACHE_DIRTY = True


import requests
from bs4 import BeautifulSoup

import contact_extractor

# Domain-Cache einmal beim Import laden
_load_domain_cache()

# python-jobspy benötigt Python 3.11 — wird als Subprocess aufgerufen
PYTHON311 = "/usr/local/bin/python3.11"


def _extract_city(address: str) -> str:
    """Extrahiert Stadtname aus Adresse — überspringt Straße, PLZ, 'Deutschland'."""
    parts = [p.strip() for p in address.split(",")]
    for part in parts:
        if re.match(r"^\d{4,5}$", part):          # PLZ
            continue
        if re.search(r"\d", part):                 # Straße mit Hausnummer
            continue
        if part.lower() in ("deutschland", "germany", "de", ""):
            continue
        if len(part) > 2:
            return part
    # Fallback: erstes Segment
    return parts[0].strip() if parts else address

# ── Geocoding ────────────────────────────────────────────────────────────────
# Lokaler Fallback für ~80 wichtigste deutsche Städte (Nominatim-Rate-Limit)
_CITY_COORDS = {
    "berlin": (52.520, 13.405), "hamburg": (53.551, 9.994),
    "münchen": (48.137, 11.576), "köln": (50.938, 6.960),
    "frankfurt": (50.111, 8.682), "frankfurt am main": (50.111, 8.682),
    "stuttgart": (48.776, 9.183), "düsseldorf": (51.228, 6.773),
    "leipzig": (51.340, 12.375), "dortmund": (51.514, 7.468),
    "essen": (51.457, 7.012), "bremen": (53.080, 8.808),
    "dresden": (51.051, 13.738), "hannover": (52.376, 9.738),
    "nürnberg": (49.454, 11.078), "duisburg": (51.435, 6.763),
    "bochum": (51.482, 7.216), "wuppertal": (51.256, 7.150),
    "bielefeld": (52.022, 8.532), "bonn": (50.737, 7.099),
    "münster": (51.961, 7.626), "mannheim": (49.488, 8.467),
    "karlsruhe": (49.007, 8.404), "augsburg": (48.366, 10.898),
    "wiesbaden": (50.083, 8.240), "mönchengladbach": (51.185, 6.442),
    "gelsenkirchen": (51.518, 7.086), "aachen": (50.776, 6.084),
    "braunschweig": (52.269, 10.521), "chemnitz": (50.828, 12.921),
    "kiel": (54.323, 10.123), "halle": (51.497, 11.969),
    "magdeburg": (52.131, 11.640), "freiburg": (47.999, 7.842),
    "krefeld": (51.339, 6.586), "mainz": (50.000, 8.271),
    "lübeck": (53.870, 10.687), "erfurt": (50.985, 11.030),
    "oberhausen": (51.470, 6.851), "rostock": (54.089, 12.141),
    "kassel": (51.316, 9.497), "hagen": (51.361, 7.474),
    "potsdam": (52.396, 13.058), "saarbrücken": (49.235, 6.997),
    "hamm": (51.678, 7.816), "oldenburg": (53.142, 8.214),
    "mülheim": (51.433, 6.879), "osnabrück": (52.280, 8.043),
    "darmstadt": (49.872, 8.651), "heidelberg": (49.410, 8.692),
    "regensburg": (49.015, 12.098), "paderborn": (51.719, 8.757),
    "würzburg": (49.792, 9.953), "göttingen": (51.533, 9.935),
    "wolfsburg": (52.424, 10.787), "recklinghausen": (51.614, 7.197),
    "heilbronn": (49.142, 9.220), "ingolstadt": (48.764, 11.425),
    "ulm": (48.402, 9.988), "pforzheim": (48.892, 8.699),
    "offenbach": (50.101, 8.763), "bottrop": (51.522, 6.929),
    "trier": (49.757, 6.641), "bremerhaven": (53.540, 8.581),
    "würzburg": (49.792, 9.953), "siegen": (50.874, 8.024),
    "jena": (50.927, 11.586), "hildesheim": (52.151, 9.951),
    "cottbus": (51.757, 14.331), "schwerin": (53.636, 11.401),
    "salzgitter": (52.154, 10.332), "gera": (50.878, 12.084),
    "troisdorf": (50.816, 7.155), "sankt augustin": (50.775, 7.190),
    "siegburg": (50.800, 7.209), "bad honnef": (50.643, 7.228),
    "königswinter": (50.673, 7.183), "hennef": (50.776, 7.284),
    "lohmar": (50.834, 7.210), "niederkassel": (50.808, 7.034),
    "much": (50.904, 7.403), "neunkirchen-seelscheid": (50.830, 7.330),
    "windeck": (50.794, 7.559), "eitorf": (50.770, 7.454),
    "meckenheim": (50.626, 7.027), "rheinbach": (50.625, 6.951),
    "bad godesberg": (50.685, 7.152), "beuel": (50.743, 7.127),
    "swisttal": (50.659, 6.907), "alfter": (50.733, 7.010),
    "wachtberg": (50.626, 7.120), "remagen": (50.573, 7.229),
    "sinzig": (50.545, 7.245), "bad neuenahr": (50.545, 7.114),
    "linz am rhein": (50.568, 7.283), "unkel": (50.592, 7.218),
}
_geo_cache: Dict[str, tuple] = {}

def geocode(city: str) -> tuple:
    if not city:
        return (None, None)
    if city in _geo_cache:
        return _geo_cache[city]

    # 1. Lokaler Fallback für bekannte Städte
    city_key = city.lower().strip()
    # Probiere verschiedene Normalisierungen
    for candidate in [city_key, city_key.split(',')[0].strip(),
                      re.sub(r'^\d{4,5}\s*', '', city_key).strip()]:
        if candidate in _CITY_COORDS:
            result = _CITY_COORDS[candidate]
            _geo_cache[city] = result
            return result

    # 2. Nominatim API (mit Rate-Limit-Schutz)
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{city}, Germany", "format": "json", "limit": 1},
            headers={"User-Agent": "CVJobMatcher/1.0 (medwing-internal)"},
            timeout=5,
        )
        if resp.status_code == 429:
            # Rate-limited — nur lokale Daten verwenden
            _geo_cache[city] = (None, None)
            return (None, None)
        data = resp.json()
        if data:
            result = (float(data[0]["lat"]), float(data[0]["lon"]))
            _geo_cache[city] = result
            return result
    except Exception:
        pass
    _geo_cache[city] = (None, None)
    return (None, None)


def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "de-DE,de;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s


# Einrichtungsart → konkreter Suchbegriff-Modifier
# Reihenfolge: spezifischste Keywords zuerst
FACILITY_MODIFIERS = [
    # Intensivpflege VOR "ambulant"/"pflegedienst" — sonst wird Intensivpflegedienst zu "ambulant"
    (["intensivpflege", "beatmung", "außerklinisch", "1:1"],    "Intensivpflege"),
    (["ambulant", "sozialstation", "pflegedienst", "häuslich"], "ambulant"),
    (["psychiatrie"],                                            "Psychiatrie"),
    (["rehabilitat", "reha"],                                   "Reha"),
    (["kinderklinik", "pädiatrie", "neonatologie"],             "Pädiatrie"),
    (["altenpflege", "pflegeheim", "seniorenheim", "senioren"], "Altenpflege"),
    (["krankenhaus", "klinik", "hospital", "klinikum"],         "Klinik"),
]

# ── Verwandte / ähnliche Fachabteilungen (Fallback bei 0 Treffern) ──────────
# Keys sind case-insensitive lookup. Wert = Liste ähnlicher / oft austauschbar
# gesuchter Fächer.
_SIMILAR_FACHABTEILUNGEN: Dict[str, List[str]] = {
    "kardiologie":        ["Innere Medizin", "Herzkatheter", "IMC"],
    "nephrologie":        ["Dialyse", "Innere Medizin"],
    "dialyse":            ["Nephrologie", "Innere Medizin"],
    "onkologie":          ["Hämatologie", "Palliativ", "Innere Medizin"],
    "hämatologie":        ["Onkologie", "Innere Medizin"],
    "gastroenterologie":  ["Innere Medizin", "Endoskopie"],
    "pneumologie":        ["Innere Medizin", "Beatmung"],
    "endokrinologie":     ["Innere Medizin", "Diabetologie"],
    "rheumatologie":      ["Innere Medizin", "Orthopädie"],
    "geriatrie":          ["Innere Medizin", "Rehabilitation", "Gerontopsychiatrie"],
    "gerontopsychiatrie": ["Psychiatrie", "Geriatrie", "Demenz"],
    "psychiatrie":        ["Psychosomatik", "Gerontopsychiatrie"],
    "psychosomatik":      ["Psychiatrie", "Psychotherapie"],
    "neurologie":         ["Stroke Unit", "Neurochirurgie", "Neuro-Reha"],
    "pädiatrie":          ["Kinderklinik", "Neonatologie", "Kinderheilkunde"],
    "neonatologie":       ["Pädiatrie", "Kinderintensiv"],
    "notaufnahme":        ["Intensivstation", "ZNA", "Rettungsdienst"],
    "intensivstation":    ["IMC", "Notaufnahme", "Anästhesie"],
    "intensivmedizin":    ["IMC", "Notaufnahme", "Anästhesie"],
    "intensivpflege":     ["IMC", "Anästhesie", "Beatmung"],
    "anästhesie":         ["Intensivstation", "OP", "Aufwachraum"],
    "op":                 ["OTA", "Zentral-OP", "Anästhesie"],
    "orthopädie":         ["Unfallchirurgie", "Rehabilitation"],
    "unfallchirurgie":    ["Orthopädie", "Allgemeinchirurgie", "Notaufnahme"],
    "viszeralchirurgie":  ["Allgemeinchirurgie", "Innere Medizin"],
    "gefäßchirurgie":     ["Allgemeinchirurgie", "Angiologie"],
    "herzchirurgie":      ["Kardiologie", "Intensivstation"],
    "gynäkologie":        ["Geburtshilfe", "Frauenheilkunde", "Senologie"],
    "geburtshilfe":       ["Gynäkologie", "Kreißsaal", "Wochenstation"],
    "urologie":           ["Nephrologie", "Allgemeinchirurgie"],
    "dermatologie":       ["Allergologie", "Innere Medizin"],
    "hno":                ["MKG", "Allgemeinchirurgie"],
    "augenheilkunde":     ["Ophthalmologie"],
    "rehabilitation":     ["Geriatrie", "Neurorehabilitation", "Orthopädie"],
    "palliativmedizin":   ["Onkologie", "Hospiz", "Geriatrie"],
    "demenz":             ["Gerontopsychiatrie", "Altenpflege"],
    "wohnbereich":        ["Altenpflege", "Gerontopsychiatrie"],
}

# Titel → Suchterm-Aliase (Stellenanzeigen nutzen oft ältere / andere Begriffe)
# Alle Aliase werden parallel gesucht und zusammengeführt.
TITLE_SEARCH_ALIASES: Dict[str, List[str]] = {
    # ── Altenpflege / allgemeine Pflege ──────────────────────────────────────
    "Pflegefachkraft": [
        "Pflegefachkraft", "Altenpfleger", "Gesundheits- und Krankenpfleger"],
    "Altenpfleger": [
        "Altenpfleger", "Pflegefachkraft"],
    "Altenpfleger Fachweiterbildung": [
        "Altenpfleger Fachweiterbildung", "Pflegefachkraft Fachweiterbildung"],
    "Pflegefachkraft Fachweiterbildung": [
        "Pflegefachkraft Fachweiterbildung", "Fachkrankenpfleger"],
    "Gesundheits- und Krankenpfleger": [
        "Gesundheits- und Krankenpfleger", "Krankenpfleger", "Pflegefachkraft",
        "Examinierter Krankenpfleger"],
    "Gesundheits- und Krankenpfleger Fachweiterbildung": [
        "Gesundheits- und Krankenpfleger Fachweiterbildung", "Fachkrankenpfleger"],
    # ── Kinderkrankenpflege ──────────────────────────────────────────────────
    "Kinderkrankenpfleger": [
        "Kinderkrankenpfleger", "Kinderkrankenschwester", "Pädiatrische Pflege"],
    "Kinderkrankenpfleger Fachweiterbildung": [
        "Kinderkrankenpfleger Fachweiterbildung", "Kinderkrankenpfleger"],
    # ── Spezial-Krankenpflege ────────────────────────────────────────────────
    "Fachkrankenpfleger Psychiatrie": [
        "Fachkrankenpfleger Psychiatrie", "Pflegefachkraft Psychiatrie"],
    "Intensivpfleger": [
        "Intensivpfleger", "Pflegefachkraft Intensiv", "ICU Pflege",
        "Intensivpflege", "Fachkrankenpfleger Intensiv",
        "Gesundheits- und Krankenpfleger Intensiv"],
    # ── Pflegehilfe ──────────────────────────────────────────────────────────
    "Pflegehelfer": [
        "Pflegehelfer", "Pflegehilfskraft", "Pflegeassistenz"],
    "Krankenpflegehelfer": [
        "Krankenpflegehelfer", "Pflegehelfer"],
    "Pflegeassistenz": [
        "Pflegeassistenz", "Gesundheits- und Pflegeassistenz", "Pflegehelfer"],
    "Pflegefachassistenz": [
        "Pflegefachassistenz", "Pflegeassistenz"],
    # ── Pflege-Leitung ───────────────────────────────────────────────────────
    "Pflegedienstleitung": [
        "Pflegedienstleitung", "PDL"],
    "Stationsleitung": [
        "Stationsleitung", "Bereichsleitung Pflege"],
    "Wohnbereichsleitung": [
        "Wohnbereichsleitung", "Wohnbereichsleiter"],
    "Einrichtungsleitung": [
        "Einrichtungsleitung", "Heimleitung"],
    "Praxisanleiter": [
        "Praxisanleiter", "Mentor Pflege"],
    "Bereichsleitung": [
        "Bereichsleitung", "Stationsleitung"],
    "Einsatzleitung": [
        "Einsatzleitung", "Pflegedienstleitung ambulant"],
    "OP-Leitung": [
        "OP-Leitung", "OP-Pflegeleitung"],
    "Advanced Practice Nurse": [
        "Advanced Practice Nurse", "APN", "Pflegefachkraft Fachweiterbildung"],
    "Dauernachtwache": [
        "Dauernachtwache", "Nachtwache Pflege", "Nachtpflege"],
    # ── OP / Anästhesie ──────────────────────────────────────────────────────
    "OTA": [
        "OTA", "Operationstechnische Assistenz"],
    "ATA": [
        "ATA", "Anästhesietechnische Assistenz"],
    "CTA": [
        "CTA", "Chirurgisch-technische Assistenz", "OTA"],
    "ITA": [
        "ITA", "Intensivmedizinisch-Technische-Assistenz", "Intensivpfleger"],
    # ── Ärzte ────────────────────────────────────────────────────────────────
    "Assistenzarzt": [
        "Assistenzarzt", "Arzt in Weiterbildung"],
    "Facharzt": [
        "Facharzt", "Fachärztin"],
    "Oberarzt": [
        "Oberarzt", "Oberärztin"],
    "Chefarzt": [
        "Chefarzt", "Chefärztin"],
    "Zahnarzt": [
        "Zahnarzt", "Zahnärztin"],
    # ── MFA / MTA ────────────────────────────────────────────────────────────
    "MFA": [
        "MFA", "Medizinische Fachangestellte", "Arzthelfer"],
    "MTLA": [
        "MTLA", "Medizinisch-technische Laboratoriumsassistenz"],
    "MTRA": [
        "MTRA", "Medizinisch-technische Radiologieassistenz"],
    "MTAF": [
        "MTAF", "Funktionsdiagnostik"],
    # ── Apotheke / Zahntechnik ───────────────────────────────────────────────
    "PTA": [
        "PTA", "Pharmazeutisch-technische Assistenz"],
    "Zahntechniker": [
        "Zahntechniker", "Zahntechnik"],
    "ZMF": [
        "ZMF", "Zahnmedizinische Fachangestellte", "Zahnarzthelfer"],
    # ── Hebamme / Rettung ────────────────────────────────────────────────────
    "Hebamme": [
        "Hebamme", "Entbindungspflegerin"],
    "Notfallsanitäter": [
        "Notfallsanitäter", "Rettungssanitäter"],
    "Rettungssanitäter": [
        "Rettungssanitäter", "Notfallsanitäter"],
    # ── Therapeuten ──────────────────────────────────────────────────────────
    "Physiotherapeut": [
        "Physiotherapeut", "Krankengymnast"],
    "Ergotherapeut": [
        "Ergotherapeut"],
    "Logopäde": [
        "Logopäde", "Sprachtherapeut"],
    "Psychotherapeut": [
        "Psychotherapeut", "Psychologe"],
    "Osteopath": [
        "Osteopath", "Physiotherapeut"],
    # ── Soziales ─────────────────────────────────────────────────────────────
    "Heilerziehungspfleger": [
        "Heilerziehungspfleger", "HEP", "Heilpädagoge"],
    "Heilpädagoge": [
        "Heilpädagoge", "Heilerziehungspfleger"],
    "Erzieher": [
        "Erzieher", "Sozialpädagoge"],
    "Sozialarbeiter": [
        "Sozialarbeiter", "Sozialpädagoge"],
    # ── Spezialisten ─────────────────────────────────────────────────────────
    "Hygienebeauftragter": [
        "Hygienebeauftragter", "Hygienefachkraft"],
    "Hygienefachkraft": [
        "Hygienefachkraft", "Hygienebeauftragter"],
    "Wundmanager": [
        "Wundmanager", "Wundexperte", "Pflegefachkraft Wundversorgung"],
    "Pflegeberater": [
        "Pflegeberater", "Pflegesachverständiger"],
    "Qualitätsmanagement Pflege": [
        "Qualitätsmanagement Pflege", "QM Pflege", "Pflegefachkraft QM"],
}


# ── Facility-Typ Erkennung pro Stellenanzeige ─────────────────────────────────
# Keywords die auf die jeweilige Einrichtungsart hinweisen (Firmenname + Jobtitel)
FACILITY_DETECT_RULES = [
    # Krankenhaus / Klinik — sehr spezifische Begriffe zuerst
    (["krankenhaus", "klinikum", "klinik", "hospital", "uniklinik",
      "universitätsklinikum", "kreiskrankenhaus", "städtisches kranken",
      "helios", "asklepios", "rhön-klinikum", "diak", "charité", "vivantes",
      "imland", "segeberger kliniken", "uksh", "universitätsklinik",
      "median klinik", "regiomed", "katholisches krankenhaus"],
     "Krankenhaus / Klinik"),
    # Intensivpflege / außerklinische Beatmung — VOR Ambulant!
    (["intensivpflege", "beatmungspflege", "außerklinische beatmung",
      "heimbeatmung", "1:1 pflege", "1:1 intensiv", "1zu1",
      "intensivpflegedienst", "beatmungs", "wachkoma",
      "bonitas", "gip", "deutsches rotes kreuz intensiv",
      "aip ambulante intensivpflege", "cura intensivpflege",
      "linde intensivpflege", "promedica intensiv"],
     "Intensivpflegedienst"),
    # Ambulant
    (["ambulant", "pflegedienst", "sozialstation", "häusliche pflege",
      "hauspflege", "ambulanter pflegeservice", "ambulante pflege"],
     "Ambulanter Pflegedienst"),
    # Psychiatrie
    (["psychiatrie", "psychiatrisch", "nervenklinik", "klinik für psychiatrie",
      "bezirksklinikum", "landes-nervenklinik", "lnk"],
     "Psychiatrie"),
    # Reha
    (["reha-klinik", "rehazentrum", "rehabilitations", "reha klinik",
      "rehaklinik", " reha ", "kurpark", "median reha"],
     "Rehabilitation"),
    # Kinderklinik
    (["kinderkrankenhaus", "kinderklinik", "kinderstation", "kinder- und jugend",
      "kinderchirurgie"],
     "Kinderklinik / Pädiatrie"),
    # Seniorenheim / Altenpflege — bewusst nach Krankenhaus gelistet!
    (["seniorenheim", "seniorenresidenz", "pflegeheim", "altenheim",
      "seniorenzentrum", "seniorenhaus", "seniorenstift", "seniorenpflege",
      "altenpflege", "pflegezentrum", "betreutes wohnen", "senioreneinrichtung",
      "pflegeeinrichtung", "johanniter haus", "johanniterhäuser",
      "awo haus", "caritas haus", "diakonie haus", "diakoniestation",
      "residenz ", "stift ", "seniorenpark", "pflegestift",
      "haus am ", "haus für ", "heim für",
      # Erweiterte Erkennung
      "johanniter", "awo", "caritas", "diakonie", "malteser",
      "seniorenwohn", "pflegewohn",
      "vitanas", "korian", "alloheim", "advita", "convivo", "bfw",
      "pflegen & wohnen", "pflegen und wohnen",
      "pro seniore", "tertianum", "dussmann"],
     "Altenpflege / Pflegeheim"),
]


def _detect_job_facility(job: dict) -> str:
    """Erkennt den Einrichtungstyp einer Stellenanzeige aus Titel + Firmenname."""
    text = " ".join([
        job.get("title", ""),
        job.get("company", ""),
        job.get("description", ""),
    ]).lower()
    for keywords, facility in FACILITY_DETECT_RULES:
        if any(k in text for k in keywords):
            return facility
    return ""  # unbekannt


# Einrichtungsarten die als kompatibel gelten
FACILITY_COMPAT = {
    "intensivpflegedienst": {"intensivpflegedienst", "ambulanter pflegedienst"},
    "ambulanter pflegedienst": {"ambulanter pflegedienst"},
    "krankenhaus / klinik": {"krankenhaus / klinik"},
    "psychiatrie": {"psychiatrie", "krankenhaus / klinik"},
    "rehabilitation": {"rehabilitation"},
    "kinderklinik / pädiatrie": {"kinderklinik / pädiatrie", "krankenhaus / klinik"},
    "altenpflege / pflegeheim": {"altenpflege / pflegeheim"},
}


def _check_facility_match(job: dict, candidate_einrichtung: str) -> bool:
    """True wenn der Job zum Einrichtungswunsch des Kandidaten passt (oder unbekannt)."""
    if not candidate_einrichtung:
        return True
    detected = _detect_job_facility(job)
    if not detected:
        return True  # unbekannt → kein Ausschluss
    cand_l = candidate_einrichtung.lower()
    det_l  = detected.lower()
    # Exakte Übereinstimmung
    if det_l == cand_l:
        return True
    # Kompatibilitäts-Tabelle
    compat = FACILITY_COMPAT.get(cand_l)
    if compat and det_l in compat:
        return True
    # Teilweise Übereinstimmung (z.B. "ambulant" in "ambulanter pflegedienst")
    cand_key = cand_l.split("/")[0].split("(")[0].strip()
    det_key  = det_l.split("/")[0].split("(")[0].strip()
    return cand_key in det_key or det_key in cand_key


def _facility_modifier(einrichtung: str) -> str:
    """Gibt den passenden Suchterm-Modifier für eine Einrichtungsart zurück."""
    if not einrichtung:
        return ""
    raw = einrichtung.lower()
    for keywords, modifier in FACILITY_MODIFIERS:
        if any(k in raw for k in keywords):
            return modifier
    return ""


# Pflegebereich-Synonyme: wenn ein Begriff im Suchterm, gelten alle als Match
CARE_SYNONYMS = [
    {"pflege", "pflegefachkraft", "pflegekraft", "pflegefachmann", "pflegefachfrau",
     "krankenpfleger", "altenpfleger", "gesundheits- und krankenpfleger",
     "pflegehelfer", "pflegeassistent", "heilerziehungspfleger",
     "kinderkrankenpfleger", "kinderkrankenschwester", "fachkrankenpfleger",
     "examiniert"},
    {"ota", "operationstechnisch"},
    {"ata", "anästhesietechnisch"},
    {"arzt", "ärztin", "oberarzt", "assistenzarzt", "facharzt", "chefarzt"},
    {"mfa", "medizinische fachangestellte", "medizinischer fachangestellter"},
    {"physiotherapeut", "physiotherapie"},
    {"ergotherapeut", "ergotherapie"},
    {"hebamme", "entbindungspfleger"},
]

# Jobs die IMMER irrelevant für Pflege-Suchen sind (Negativ-Filter)
# key: wenn Suchterm eines dieser Wörter enthält → Job ist fachfremd
# value: Wörter im Job-Titel die den Ausschluss triggern
_EXCLUDE_CROSS_MATCHES = {
    # Pflege-Suchen dürfen keine Zahn-/Labor-/Apotheken-Stellen zeigen
    "pflege": {"zahnmedizin", "zahnarzt", "zfa", "zmf", "zahntechnik",
               "apothek", "pharma", "labor", "mta", "mtla",
               "tiermedizin", "veterinär"},
    "krankenpfleger": {"zahnmedizin", "zahnarzt", "zfa", "zmf", "zahntechnik",
                       "apothek", "pharma", "labor", "tiermedizin"},
    "kinderkrankenpfleger": {"zahnmedizin", "zahnarzt", "zfa", "zmf",
                             "apothek", "pharma", "labor", "tiermedizin"},
    "altenpfleger": {"zahnmedizin", "zahnarzt", "zfa", "zmf",
                     "apothek", "pharma", "labor", "tiermedizin"},
    "pflegehelfer": {"zahnmedizin", "zahnarzt", "zfa", "zmf",
                     "apothek", "pharma", "labor", "tiermedizin"},
    "hebamme": {"zahnmedizin", "zahnarzt", "zfa", "zmf", "apothek", "tiermedizin"},
    "ota": {"zahnmedizin", "zahnarzt", "apothek", "tiermedizin"},
    "ata": {"zahnmedizin", "zahnarzt", "apothek", "tiermedizin"},
}


def _is_relevant(text: str, search_term: str) -> bool:
    text_l = text.lower()
    term_l = search_term.lower()

    # ── Negativ-Filter: fachfremde Jobs sofort ausschließen ──────────────
    for search_key, blocked_words in _EXCLUDE_CROSS_MATCHES.items():
        if search_key in term_l:
            if any(bw in text_l for bw in blocked_words):
                return False

    # Direkte Wort-Übereinstimmung
    words = [w for w in re.split(r'[\s\-/]+', term_l) if len(w) > 2]
    if not words:
        return True
    direct_matches = sum(1 for w in words if w in text_l)
    if direct_matches >= max(1, len(words) // 2):
        return True

    # Synonyme: Suchterm UND Job-Titel müssen in DERSELBEN Gruppe liegen
    for group in CARE_SYNONYMS:
        if any(syn in term_l for syn in group):
            if any(syn in text_l for syn in group):
                return True

    return False


def _within_radius(job_location: str, user_lat, user_lon, radius: int) -> Optional[float]:
    """Geocode job location and return distance if within radius, else None."""
    if not user_lat or not job_location:
        return None
    # Extrahiere Stadt aus "Hamburg, Schleswig-Holstein" o.ä.
    city = job_location.split(',')[0].strip()
    j_lat, j_lon = geocode(city)
    if j_lat:
        dist = haversine(user_lat, user_lon, j_lat, j_lon)
        if dist <= radius * 1.3:   # 30% Puffer
            return dist
    return None   # außerhalb oder nicht geokodierbar


# Pre-compiled Patterns für Pflegejobs-Parser (hot loop)
_PJ_JOB_ITEM_RE = re.compile(r'job.?item|job.?card|job.?listing|stelle', re.I)
_PJ_LI_RE       = re.compile(r'job|stelle', re.I)
_PJ_HREF_RE     = re.compile(r'/stellenangebote/\d+|/job/\d+')
_PJ_LOC_RE      = re.compile(r'location|ort|city|plz', re.I)
_PJ_COMPANY_RE  = re.compile(r'company|arbeitgeber|employer', re.I)

PFLEGEJOBS_URL = "https://www.pflegejobs.de/stellenangebote/"


def search_pflegejobs(job_title: str, location: str, radius: int,
                      session: requests.Session) -> List[Dict]:
    results = []
    try:
        # Pflegejobs.de: ?s=Suchbegriff&ort=Stadt
        params = {}
        if job_title:
            params["s"] = job_title
        if location:
            params["ort"] = location

        resp = session.get(PFLEGEJOBS_URL, params=params, timeout=8)
        if not resp.ok:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        user_lat, user_lon = geocode(location) if location else (None, None)

        # Job-Einträge suchen — verschiedene mögliche Strukturen
        job_items = (
            soup.find_all(class_=_PJ_JOB_ITEM_RE)
            or soup.find_all("li", class_=_PJ_LI_RE)
            or [a.parent for a in soup.find_all("a", href=_PJ_HREF_RE)]
        )

        seen = set()
        for item in job_items[:30]:
            # Titel
            title_el = item.find(["h2", "h3", "h4", "strong"])
            title = title_el.get_text(strip=True) if title_el else item.get_text(strip=True)[:80]
            if not title or title in seen:
                continue
            seen.add(title)

            # Relevanz
            if job_title and not _is_relevant(title, job_title):
                continue

            # Link
            link_el = item.find("a", href=True)
            url = link_el["href"] if link_el else ""
            if url and not url.startswith("http"):
                url = "https://www.pflegejobs.de" + url

            # Ort
            loc_el = item.find(class_=_PJ_LOC_RE)
            job_loc = loc_el.get_text(strip=True) if loc_el else ""

            # Arbeitgeber
            company_el = item.find(class_=_PJ_COMPANY_RE)
            company = company_el.get_text(strip=True) if company_el else ""

            dist_km = None
            if user_lat and job_loc:
                dist_km = _within_radius(job_loc, user_lat, user_lon, radius)

            results.append({
                "source":        "Pflegejobs",
                "title":         title,
                "company":       company,
                "location":      job_loc or location,
                "distance_km":   dist_km,
                "url":           url,
                "published":     "",
                "department":    "",
                "contact_name":  "",
                "contact_email": "",
                "contact_phone": "",
            })

        results.sort(key=lambda j: j["distance_km"] if j["distance_km"] is not None else 9999)

    except Exception as e:
        print(f"[Pflegejobs] Fehler: {e}")

    return results


# python-jobspy Inline-Script — wird in separatem Python3.11-Prozess ausgeführt
_JOBSPY_SCRIPT = """
import sys, json
from jobspy import scrape_jobs
title, loc = sys.argv[1], sys.argv[2]
df = scrape_jobs(
    site_name=["indeed"],
    search_term=title,
    location=loc,
    results_wanted=15,
    country_indeed="Germany",
    hours_old=72,
)
out = []
for _, row in df.iterrows():
    out.append({
        "source": row.get("site", "indeed"),
        "title": str(row.get("title", "")),
        "company": str(row.get("company_name", "")),
        "location": str(row.get("location", "")),
        "url": str(row.get("job_url", "")),
        "published": str(row.get("date_posted", "")),
    })
print(json.dumps(out))
"""


def search_jobspy(job_title: str, location: str, radius: int,
                  user_lat, user_lon) -> List[Dict]:
    results: List[Dict] = []
    try:
        search_loc = f"{location}, Deutschland" if location else "Deutschland"
        proc = subprocess.run(
            [PYTHON311, "-c", _JOBSPY_SCRIPT, job_title, search_loc],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            print(f"[JobSpy] stderr: {proc.stderr[:200]}")
            return []

        raw = _json.loads(proc.stdout)
        for item in raw:
            # Normalisiere Quellname
            site = item.get("source", "").lower()
            item["source"] = "Indeed" if "indeed" in site else "LinkedIn"

            # Distanz berechnen wenn möglich
            job_city = item["location"].split(",")[0].strip()
            dist_km = None
            if user_lat and job_city:
                dist_km = _within_radius(job_city, user_lat, user_lon, radius)
            item["distance_km"] = dist_km
            results.append(item)

        results.sort(key=lambda j: j["distance_km"] if j["distance_km"] is not None else 9999)

    except subprocess.TimeoutExpired:
        print("[JobSpy] Timeout")
    except Exception as e:
        print(f"[JobSpy] Fehler: {e}")

    return results


# ===========================================================================
# QUELLE 4 — Pflegia (Sitemaps + Apollo SSR)
# ===========================================================================
PFLEGIA_SM = "https://www.pflegia.de/sitemap/jobposition-sitemap-{i}.xml"
PFLEGIA_SM_COUNT = 27  # total sub-sitemaps known from sitemap index


def _extract_mailto_emails(html: str) -> list:
    """Extrahiert E-Mail-Adressen direkt aus mailto:-Links im HTML."""
    emails = re.findall(r'href=["\']mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})', html, re.IGNORECASE)
    _BAD_EMAIL_PARTS = [
        'noreply', 'no-reply', 'bounce', 'example', 'whatsapp', 'facebook',
        'instagram', 'twitter', 'tiktok', 'youtube', 'linkedin', 'xing',
        'pinterest', 'telegram', 'signal', 'snapchat', 'wix.com',
        'sentry.io', 'cloudflare', 'googletagmanager', 'google-analytics',
        'hotjar', 'mailchimp', 'hubspot', 'zendesk', '@sentry',
        'unsubscribe', 'newsletter@', 'marketing@', 'tracking@',
    ]
    return list(dict.fromkeys(
        e for e in emails
        if not any(x in e.lower() for x in _BAD_EMAIL_PARTS)
    ))


def _city_slug(location_str: str) -> str:
    """Normalize a city name to Pflegia URL slug (lowercase, no umlauts)."""
    city = location_str.split(",")[0].strip().lower()
    for src, dst in [("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")]:
        city = city.replace(src, dst)
    city = re.sub(r"[^a-z0-9]+", "-", city).strip("-")
    return city


def search_pflegia(job_title: str, location: str, radius: int,
                   session: requests.Session) -> List[Dict]:
    results: List[Dict] = []
    seen_uuids: set = set()

    try:
        city = _city_slug(location)
        if not city:
            return []

        user_lat, user_lon = geocode(location) if location else (None, None)

        # ── 1. Sammle passende Job-URLs aus Sitemaps (PARALLEL) ──────────────
        matching: List[tuple] = []  # (url, uuid)

        def _scan_sitemap(i: int) -> List[tuple]:
            """Scannt eine einzelne Sitemap und gibt passende (url, uuid) zurück."""
            hits = []
            try:
                r = session.get(PFLEGIA_SM.format(i=i), timeout=6)
                if not r.ok:
                    return []
                for url in re.findall(r"<loc>([^<]+)</loc>", r.text):
                    m = re.search(
                        r"/job-details/([^/]+)/([^/]+)/([a-f0-9-]{36})/", url
                    )
                    if not m:
                        continue
                    url_city, url_slug, uuid = m.groups()
                    if url_city != city:
                        continue
                    if job_title and not _is_relevant(
                        url_slug.replace("-", " "), job_title
                    ):
                        continue
                    hits.append((url, uuid))
            except Exception:
                pass
            return hits

        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(_scan_sitemap, i) for i in range(PFLEGIA_SM_COUNT)]
            for fut in as_completed(futs):
                try:
                    for url, uuid in fut.result(timeout=8):
                        if uuid not in seen_uuids:
                            seen_uuids.add(uuid)
                            matching.append((url, uuid))
                except Exception:
                    pass
                if len(matching) >= 15:
                    break

        if not matching:
            return []

        # ── 2. Seiten PARALLEL abrufen & Apollo-Cache auslesen ─────────────
        # Single-pass-Iteration über apollo.values() → halbiert CPU-Arbeit
        # pro Detail gegenüber zwei separaten Schleifen.
        def _fetch_detail(item):
            url, uuid = item
            try:
                r = session.get(url, timeout=8)
                if not r.ok:
                    return None
                # __NEXT_DATA__ direkt per Regex extrahieren — ~3× schneller
                # als BeautifulSoup für dieses eine Script-Tag.
                nd_match = re.search(
                    r'<script id="__NEXT_DATA__"[^>]*>([^<]+)</script>',
                    r.text,
                )
                if not nd_match:
                    return None
                try:
                    apollo = (
                        _json.loads(nd_match.group(1))
                        .get("props", {})
                        .get("pageProps", {})
                        .get("__APOLLO_CACHE__", {})
                    )
                except Exception:
                    return None

                job_val = apollo.get(f"JobPosition:{uuid}", {})
                if not job_val:
                    return None

                emp_ref = (job_val.get("employer") or {}).get("__ref", "")
                employer = apollo.get(emp_ref, {})
                company = employer.get("name", "")

                geo = job_val.get("geoLocation") or {}
                job_city = geo.get("city", "")
                address = geo.get("formattedAddress", "")
                coords: list = []
                contact_name = ""
                contact_role = ""

                # Ein einziger Pass durch apollo.values() — findet GeoLocation
                # UND DisplayContactPerson zusammen.
                for v in apollo.values():
                    if not isinstance(v, dict):
                        continue
                    tn = v.get("__typename")
                    if tn == "GeoLocation" and not coords:
                        c = (v.get("location") or {}).get("coordinates", [])
                        if c:
                            coords = c
                            if not address:
                                address = v.get("formattedAddress", "")
                    elif tn == "DisplayContactPerson" and not contact_name:
                        first = v.get("name", "")
                        last  = v.get("surname", "")
                        contact_name = f"{first} {last}".strip()
                        contact_role = v.get("jobTitle") or ""
                    if coords and contact_name:
                        break

                dist_km = None
                if user_lat and len(coords) == 2:
                    dist_km = haversine(user_lat, user_lon, coords[1], coords[0])
                    if dist_km > radius * 1.3:
                        return None

                title = job_val.get("title", "") or company or job_city

                return {
                    "source":        "Pflegia",
                    "title":         title,
                    "company":       company,
                    "location":      address or job_city or location,
                    "distance_km":   dist_km,
                    "url":           url,
                    "published":     "",
                    "department":    "",
                    "contact_name":  contact_name,
                    "contact_role":  contact_role,
                    "contact_email": "",
                    "contact_phone": "",
                }
            except Exception:
                return None

        # Höherer Parallelisierungsgrad: 12 Worker statt 6, bis zu 15 Details
        with ThreadPoolExecutor(max_workers=12) as ex:
            futs = [ex.submit(_fetch_detail, item) for item in matching[:15]]
            for fut in as_completed(futs):
                try:
                    job = fut.result(timeout=10)
                    if job:
                        results.append(job)
                except Exception:
                    pass

        results.sort(key=lambda j: j["distance_km"] if j["distance_km"] is not None else 9999)

    except Exception as e:
        print(f"[Pflegia] Fehler: {e}")

    return results


# ===========================================================================
# Medi-Karriere.de Scraper
# ===========================================================================

def search_medikarriere(job_title: str, location: str, radius: int,
                        session: requests.Session,
                        user_lat=None, user_lon=None) -> List[Dict]:
    """Sucht auf medi-karriere.de nach Stellen (mit Pagination, Seiten 1-3)."""
    results = []
    try:
        city = _extract_city(location)
        base_url = (f"https://www.medi-karriere.de/jobs/"
                    f"?searchterm={requests.utils.quote(job_title)}"
                    f"&location={requests.utils.quote(city)}&radius={radius}")
        for page in range(1, 4):
            url = base_url + (f"&paged={page}" if page > 1 else "")
            r = session.get(url, timeout=6)
            if not r.ok:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".job.box-job")
            if not cards:
                break
            count_before = len(results)
            for card in cards:
                try:
                    # Titel
                    a_tag = card.select_one("a[aria-label]")
                    title = a_tag.get("aria-label", "").strip() if a_tag else ""
                    if not title:
                        continue
                    # Firma (Logo-alt)
                    img = card.select_one(".logostartseite img[alt]")
                    company = img.get("alt", "").strip() if img else ""
                    # Ort — in .employer-address steht "DD.MM.YYYYOrtname"
                    addr_el  = card.select_one(".employer-address")
                    raw_addr = addr_el.get_text(strip=True) if addr_el else ""
                    # Format: "DD.MM.YYYYOrtname" oder "DD.MM.YYYY Ortname"
                    job_loc  = re.sub(r"^\d{2}\.\d{2}\.\d{4}\s*", "", raw_addr).strip()
                    job_loc  = _clean_location(job_loc)
                    # URL
                    job_url = a_tag.get("href", "") if a_tag else ""
                    dist = _within_radius(job_loc, user_lat, user_lon, radius)
                    if user_lat and dist is not None and dist > radius:
                        continue
                    results.append({
                        "title":         title,
                        "company":       company,
                        "location":      job_loc,
                        "distance_km":   dist,
                        "url":           job_url,
                        "source":        "Medi-Karriere",
                        "published":     "",
                        "department":    "",
                        "contact_name":  "",
                        "contact_role":  "",
                        "contact_email": "",
                        "contact_phone": "",
                    })
                except Exception:
                    continue
            # Stop early if no new results on this page
            if len(results) == count_before:
                break
    except Exception as e:
        print(f"[MediKarriere] Fehler: {e}")
    return results


# ===========================================================================
# jobs.kliniken.de Scraper
# ===========================================================================

def search_kliniken(job_title: str, location: str, radius: int,
                    session: requests.Session,
                    user_lat=None, user_lon=None) -> List[Dict]:
    """Sucht auf jobs.kliniken.de nach Stellen (stark Krankenhaus-fokussiert, Pagination 1-3)."""
    results = []
    try:
        city = _extract_city(location)
        base_url = (f"https://jobs.kliniken.de/stellenangebote/"
                    f"?q={requests.utils.quote(job_title)}"
                    f"&location={requests.utils.quote(city)}")
        for page in range(1, 4):
            url = base_url + (f"&page={page}" if page > 1 else "")
            r = session.get(url, timeout=6)
            if not r.ok:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("a.k-job")
            if not cards:
                break
            count_before = len(results)
            for card in cards:
                try:
                    title_el = card.select_one("h5")
                    title    = title_el.get_text(strip=True) if title_el else ""
                    if not title:
                        continue
                    spans    = [s.get_text(strip=True) for s in card.select("span")
                                if s.get_text(strip=True)]
                    company  = spans[0] if spans else ""
                    # Location-Span hat immer eine PLZ am Anfang
                    loc_raw  = next((s for s in spans if re.match(r"^\d{5}", s)), "")
                    job_loc  = _clean_location(loc_raw) if loc_raw else ""
                    href     = card.get("href", "")
                    job_url  = f"https://jobs.kliniken.de{href}" if href.startswith("/") else href
                    dist = _within_radius(job_loc, user_lat, user_lon, radius)
                    if user_lat and dist is not None and dist > radius:
                        continue
                    results.append({
                        "title":         title,
                        "company":       company,
                        "location":      job_loc,
                        "distance_km":   dist,
                        "url":           job_url,
                        "source":        "Kliniken.de",
                        "published":     "",
                        "department":    "",
                        "contact_name":  "",
                        "contact_role":  "",
                        "contact_email": "",
                        "contact_phone": "",
                    })
                except Exception:
                    continue
            # Stop early if no new results on this page
            if len(results) == count_before:
                break
    except Exception as e:
        print(f"[Kliniken] Fehler: {e}")
    return results


# ===========================================================================
# gesundheit.jobs Scraper
# ===========================================================================

def search_gesundheitjobs(job_title: str, location: str, radius: int,
                          session: requests.Session,
                          user_lat=None, user_lon=None) -> List[Dict]:
    """Sucht auf gesundheit.jobs nach Stellen (mit Pagination, Seiten 1-3)."""
    results = []
    try:
        city = _extract_city(location)
        base_url = (f"https://www.gesundheit.jobs/stellenangebote/"
                    f"?q={requests.utils.quote(job_title)}"
                    f"&location={requests.utils.quote(city)}")
        for page in range(1, 4):
            url = base_url + (f"&p={page}" if page > 1 else "")
            r = session.get(url, timeout=6)
            if not r.ok:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".jobposting")
            if not cards:
                break
            count_before = len(results)
            for card in cards:
                try:
                    title_el = card.select_one("h3.headline")
                    title    = title_el.get_text(strip=True) if title_el else ""
                    if not title:
                        continue
                    img      = card.select_one("img.jobLogo[alt]")
                    company  = img.get("alt", "").strip() if img else ""
                    # Irrelevante Jobs (Non-Healthcare) rausfiltern
                    if not _is_relevant(title + " " + company, job_title):
                        continue
                    # Ort aus link data-target oder Text
                    loc_el   = card.select_one(".location, .city, [class*='loc'], [class*='ort']")
                    job_loc  = loc_el.get_text(strip=True) if loc_el else ""
                    link_el  = card.select_one("a[href]")
                    href     = link_el.get("href", "") if link_el else ""
                    job_url  = (f"https://www.gesundheit.jobs{href}"
                                if href.startswith("/") else href)
                    dist = _within_radius(job_loc, user_lat, user_lon, radius)
                    if user_lat and dist is not None and dist > radius:
                        continue
                    results.append({
                        "title":         title,
                        "company":       company,
                        "location":      job_loc,
                        "distance_km":   dist,
                        "url":           job_url,
                        "source":        "Gesundheit.jobs",
                        "published":     "",
                        "department":    "",
                        "contact_name":  "",
                        "contact_role":  "",
                        "contact_email": "",
                        "contact_phone": "",
                    })
                except Exception:
                    continue
            # Stop early if no new results on this page
            if len(results) == count_before:
                break
    except Exception as e:
        print(f"[GesundheitJobs] Fehler: {e}")
    return results


# ===========================================================================
# Kontakt-Anreicherung — externe Apply-URL folgen + Kontakt extrahieren
# ===========================================================================

def _get_external_apply_url(job_url: str, session: requests.Session) -> str:
    """
    Für LinkedIn/Indeed: externe Bewerber-URL aus der Seite extrahieren.
    Für alle anderen Quellen: URL direkt zurückgeben (ist schon die Firmen-Seite).
    Gibt "" zurück bei Fehler.
    """
    try:
        if "linkedin.com/jobs" in job_url:
            r = session.get(job_url, timeout=10)
            if not r.ok:
                return ""
            # LinkedIn bettet applyUrl als JSON in die Seite ein (auch ohne Login)
            m = re.search(r'"applyUrl"\s*:\s*"(https?://[^"]+)"', r.text)
            if m:
                return urllib.parse.unquote(m.group(1))
            # Fallback: JSON-LD
            soup = BeautifulSoup(r.text, "html.parser")
            for sc in soup.find_all("script", type="application/ld+json"):
                try:
                    data = _json.loads(sc.string or "")
                    u = (data if isinstance(data, dict) else {}).get("url", "")
                    if u and "linkedin.com" not in u:
                        return u
                except Exception:
                    pass
            return ""

        if "indeed.com" in job_url:
            r = session.get(job_url, timeout=10)
            if not r.ok:
                return ""
            soup = BeautifulSoup(r.text, "html.parser")
            # Indeed: "Auf Unternehmenswebsite bewerben"-Link
            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt  = a.get_text().lower()
                if "indeed.com" not in href and ("apply" in href.lower() or "bewerb" in txt):
                    return href
            return ""

        if "kliniken.de" in job_url:
            r = session.get(job_url, timeout=10)
            if not r.ok:
                return job_url
            soup = BeautifulSoup(r.text, "html.parser")
            # kliniken.de: direkte Firmen-Website-Links (z.B. "https://www.ukm.de")
            # oder Karriere-/Bewerben-Links (auch über Tracker wie anzeigenvorschau.net)
            company_url = ""
            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt = a.get_text().strip().lower()
                if not href.startswith("http") or "kliniken.de" in href:
                    continue
                # Direkte Firmen-URL (kein Tracker, kein Social Media)
                if not any(x in href for x in ["anzeigenvorschau", "twitter", "facebook",
                                                "consent", "google", "linkedin"]):
                    if not company_url:
                        company_url = href
                # Karriere-/Bewerben-Link (auch Tracker-URLs)
                if any(kw in txt for kw in ["bewerb", "apply", "karriere", "stellenangebot"]):
                    return href
                if any(kw in href.lower() for kw in ["career", "karriere", "bewerb", "apply"]):
                    return href
            # Fallback: JSON-LD hiringOrganization URL
            for sc in soup.find_all("script", type="application/ld+json"):
                try:
                    data = _json.loads(sc.string or "")
                    if isinstance(data, dict):
                        org = data.get("hiringOrganization") or {}
                        if isinstance(org, dict):
                            u = org.get("sameAs", "") or org.get("url", "")
                            if u and "kliniken.de" not in u and u.startswith("http"):
                                return u
                except Exception:
                    pass
            # Letzte Option: direkte Firmen-URL die wir gefunden haben
            if company_url:
                return company_url
            return job_url

    except Exception as e:
        print(f"[GetApplyURL] {job_url}: {e}")

    # Direkte Firmen-Seite (Kimeta, Pflegejobs, MEDWING/Personio, Pflegia)
    return job_url


# Bundesländer-Regex zum Bereinigen von Location-Strings (z.B. kliniken.de)
_BUNDESLAND_RE = re.compile(
    r"(Baden-Württemberg|Bayern|Berlin|Brandenburg|Bremen|Hamburg|Hessen|"
    r"Mecklenburg-Vorpommern|Niedersachsen|Nordrhein-Westfalen|Rheinland-Pfalz|"
    r"Saarland|Sachsen-Anhalt|Sachsen|Schleswig-Holstein|Thüringen)$"
)


def _clean_location(raw: str) -> str:
    """Entfernt PLZ und Bundesland aus einem Location-String."""
    loc = re.sub(r"^\d{5}\s*", "", raw).strip()   # PLZ vorne weg
    loc = _BUNDESLAND_RE.sub("", loc).strip()       # Bundesland hinten weg
    return loc


_AGGREGATOR_DOMAINS = {
    "linkedin.com", "indeed.com", "pflegia.de", "pflegejobs.de",
    "kimeta.de", "monster.de", "stepstone.de", "xing.com",
    "jobware.de", "arbeitsagentur.de", "meinestadt.de", "kliniken.de",
}


_GENERIC_WORDS = frozenset({
    "ambulanter", "ambulante", "ambulant", "stationäre", "stationärer",
    "pflege", "pflegedienst", "pflegeheim", "seniorenheim", "seniorenresidenz",
    "seniorenpflege", "senioren", "pflegezentrum", "krankenhaus", "klinik",
    "klinikum", "und", "für", "der", "die", "das", "in", "am", "im", "zu",
    "von", "den", "dem", "des", "hamburg", "berlin", "münchen", "köln",
    "frankfurt", "dresden", "leipzig", "düsseldorf", "stuttgart", "hannover",
    "bremen", "dortmund", "essen", "nürnberg", "duisburg", "bochum",
})


def _guess_company_domains(company: str) -> list:
    """Erzeugt Domain-Kandidaten aus dem Firmennamen (kein HTTP nötig).
    Produziert BREIT gefächerte Kandidaten: .de / .com, mit/ohne Bindestrich,
    pflege-Präfixe, erstes-Wort, erste-zwei-Wörter, etc."""
    name = company.lower()
    # Rechtsformen entfernen
    for suffix in (
        "gmbh & co. kg", "gmbh & co kg", "gmbh & co.", "ggmbh", "gmbh",
        "mbh", "e.v.", "e.v", " ag ", " ag", "ohg", "kg ", " kg",
        "stiftung", "holding"
    ):
        name = name.replace(suffix, " ")
    name = re.sub(r"[|&+,/\"]", " ", name)
    name = re.sub(r"[–—]", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" -")

    words = [w for w in name.split() if len(w) > 1]
    core = [w for w in words if w not in _GENERIC_WORDS and len(w) > 2]

    stems: list = []
    if words:
        # Volle Namen
        if len(words) >= 3:
            stems.append("".join(words[:3]))
            stems.append("-".join(words[:3]))
        if len(words) >= 2:
            stems.append("".join(words[:2]))
            stems.append("-".join(words[:2]))
        stems.append(words[0])

    if core:
        stems.append(core[0])
        if len(core) >= 2:
            stems.append(f"{core[0]}-{core[1]}")
            stems.append(f"{core[0]}{core[1]}")
        # Pflege-Präfixe (für kleine Dienste ohne eigene Marke)
        stems.extend([
            f"pflegedienst-{core[0]}",
            f"pflege-{core[0]}",
            f"{core[0]}-pflege",
            f"seniorenzentrum-{core[0]}",
            f"klinik-{core[0]}",
        ])

    # Normalisieren + dedupen
    seen = set()
    clean_stems: list = []
    for s in stems:
        s = re.sub(r"[^a-z0-9\-]", "", s).strip("-")
        if s and len(s) > 2 and s not in seen:
            seen.add(s)
            clean_stems.append(s)

    # Zu URLs: .de (primär), .com (sekundär) — in dieser Reihenfolge testen
    urls: list = []
    for s in clean_stems:
        urls.append(f"https://www.{s}.de")
    for s in clean_stems[:3]:
        urls.append(f"https://www.{s}.com")
    return urls


def _find_company_domain(company: str, job_url: str,
                          session: requests.Session) -> str:
    """Ermittelt die echte Firmen-Website (kein Aggregator). Cached.
    KEIN Suchmaschinen-Fallback — direkte Domain-Guessing ist schneller
    und zuverlässiger. Negativ-Ergebnis wird ebenfalls gecached."""
    # 1. Cache-Lookup (nur wenn kein job_url)
    if company and not job_url:
        hit = _domain_cache_get(company)
        if hit is not None:
            return hit

    # 2. Job-URL ist bereits auf der Firmen-Domain?
    if job_url:
        try:
            parsed = urllib.parse.urlparse(job_url)
            host = parsed.netloc.lower().replace("www.", "")
            if host and not any(agg in host for agg in _AGGREGATOR_DOMAINS):
                result = f"{parsed.scheme}://{parsed.netloc}"
                if company:
                    _domain_cache_set(company, result)
                return result
        except Exception:
            pass

    # 3. Domain erraten — parallel prüfen (alle Kandidaten gleichzeitig)
    if company:
        guesses = _guess_company_domains(company)
        if guesses:
            found = [None]  # Early-exit Container

            def _try_domain(url):
                if found[0]:
                    return None
                try:
                    r = session.get(url, timeout=3, allow_redirects=True)
                    if r.ok and len(r.text) > 500:
                        p = urllib.parse.urlparse(r.url)
                        return f"{p.scheme}://{p.netloc}"
                except Exception:
                    return None
                return None

            with ThreadPoolExecutor(max_workers=min(len(guesses), 10)) as ex:
                futs = [ex.submit(_try_domain, u) for u in guesses]
                for fut in as_completed(futs):
                    try:
                        d = fut.result(timeout=4)
                        if d:
                            found[0] = d
                            break
                    except Exception:
                        pass
            if found[0]:
                _domain_cache_set(company, found[0])
                return found[0]

    # Negativ-Cache
    if company:
        _domain_cache_set(company, "")
    return ""


def _extract_jsonld_contact(html: str) -> dict:
    """Extrahiert Kontaktdaten aus JSON-LD (schema.org Organization/ContactPoint)."""
    result = {}
    try:
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                             html, re.DOTALL | re.IGNORECASE):
            try:
                data = _json.loads(m.group(1))
                if isinstance(data, list):
                    for item in data:
                        _parse_jsonld_org(item, result)
                elif isinstance(data, dict):
                    _parse_jsonld_org(data, result)
                if result.get("contact_email") and result.get("contact_phone"):
                    return result
            except (ValueError, KeyError):
                continue
    except Exception:
        pass
    return result


def _parse_jsonld_org(data: dict, result: dict):
    """Parst ein JSON-LD-Objekt nach Organisation/Kontaktdaten."""
    if not isinstance(data, dict):
        return
    typ = data.get("@type", "")
    if typ in ("Organization", "MedicalOrganization", "Hospital",
               "NursingFacility", "LocalBusiness"):
        # Direkte Felder
        if not result.get("contact_email"):
            result["contact_email"] = data.get("email", "")
        if not result.get("contact_phone"):
            result["contact_phone"] = data.get("telephone", "")
        # ContactPoint
        cp = data.get("contactPoint")
        if isinstance(cp, dict):
            if not result.get("contact_email"):
                result["contact_email"] = cp.get("email", "")
            if not result.get("contact_phone"):
                result["contact_phone"] = cp.get("telephone", "")
        elif isinstance(cp, list):
            for item in cp:
                if isinstance(item, dict):
                    if not result.get("contact_email"):
                        result["contact_email"] = item.get("email", "")
                    if not result.get("contact_phone"):
                        result["contact_phone"] = item.get("telephone", "")


def _scrape_company_contact_pages(domain: str, session: requests.Session) -> dict:
    """Crawlt bekannte Unterseiten der Firmen-Website nach Kontaktdaten."""
    # Karriere/Kontakt-Seiten zuerst, Impressum zuletzt (hat GF statt AP)
    # Prioritäts-Gruppen: Karriere-Seiten zuerst, dann Kontakt, dann Impressum
    priority_paths = [
        ["/karriere", "/jobs", "/stellenangebote", "/bewerbung", "/kontakt"],
        ["/ansprechpartner", "/team", "/ueber-uns/kontakt", "/ueber-uns", ""],
        ["/impressum"],
    ]
    best: dict = {}
    all_emails: list = []

    def _fetch_and_parse(path):
        """Einzelne Seite abrufen und Kontaktdaten extrahieren."""
        try:
            r = session.get(domain + path, timeout=5, allow_redirects=True)
            if not r.ok:
                return None
            html = r.text
            result = {"path": path}

            jsonld = _extract_jsonld_contact(html)
            mailto_emails = _extract_mailto_emails(html)
            text = BeautifulSoup(html, "html.parser").get_text(" ")
            is_impressum = "/impressum" in path
            info = contact_extractor.extract_impressum(text) if is_impressum else contact_extractor.extract(text)

            # Emails sammeln
            found_emails = list(mailto_emails)
            if jsonld.get("contact_email"):
                found_emails.append(jsonld["contact_email"])
            if info.get("all_emails"):
                found_emails.extend(info["all_emails"])
            result["emails"] = found_emails

            # Beste Email wählen (mailto > JSON-LD > Text)
            best_email = ""
            if mailto_emails:
                best_email = mailto_emails[0]
            elif jsonld.get("contact_email"):
                best_email = jsonld["contact_email"]
            elif info.get("contact_email"):
                best_email = info["contact_email"]

            result["email"] = best_email
            result["phone"] = jsonld.get("contact_phone") or info.get("contact_phone", "")
            result["name"] = info.get("contact_name", "")
            return result
        except Exception:
            return None

    for group in priority_paths:
        # Alle Pfade der Gruppe parallel abrufen
        with ThreadPoolExecutor(max_workers=len(group)) as ex:
            futs = {ex.submit(_fetch_and_parse, p): p for p in group}
            for fut in as_completed(futs):
                try:
                    result = fut.result(timeout=6)
                    if not result:
                        continue
                    all_emails.extend(result.get("emails", []))
                    email = result.get("email", "")
                    phone = result.get("phone", "")
                    name = result.get("name", "")

                    if email and phone:
                        return {"contact_email": email, "contact_phone": phone,
                                "contact_name": name or best.get("contact_name", "")}

                    if email and not best.get("contact_email"):
                        best["contact_email"] = email
                    if phone and not best.get("contact_phone"):
                        best["contact_phone"] = phone
                    if name and not best.get("contact_name"):
                        best["contact_name"] = name
                except Exception:
                    continue

        # Wenn schon Email + Telefon haben, Rest überspringen
        if best.get("contact_email") and best.get("contact_phone"):
            return best

    # Fallback: beste Email aus allen gesammelten wählen
    if not best.get("contact_email") and all_emails:
        domain_host = urllib.parse.urlparse(domain).netloc.lower().replace('www.', '')
        ranked = contact_extractor.rank_emails(list(dict.fromkeys(all_emails)), domain_host)
        if ranked:
            best["contact_email"] = ranked[0]

    return best


def compute_match_score(job: dict, search_title: str,
                        einrichtung: str, radius_km: int = 30) -> int:
    """
    Berechnet einen Matching-Score (0–100) wie gut eine Stelle zum Kandidaten passt.

    Komponenten:
      Titel-Match        0–40 Punkte
      Einrichtungs-Match 0–30 Punkte
      Distanz            0–20 Punkte
      Kontaktdaten       0–10 Punkte
    """
    score = 0
    title_l  = job.get("title", "").lower()
    search_l = (search_title or "").lower()

    # ── Titel (0–40) ──────────────────────────────────────────────────────
    if search_l:
        if search_l in title_l:
            score += 40
        else:
            words = [w for w in re.split(r"[\s\-/]+", search_l) if len(w) > 2]
            if words:
                hits = sum(1 for w in words if w in title_l)
                score += int((hits / len(words)) * 30)

    # ── Einrichtung (0–30) ────────────────────────────────────────────────
    facility_match = job.get("facility_match", True)
    detected       = job.get("detected_facility", "")
    if not einrichtung:
        score += 15          # keine Präferenz → neutral
    elif facility_match:
        score += 30          # exakter Match
    elif not detected:
        score += 15          # Einrichtungsart unbekannt → neutral
    # else: Mismatch → 0

    # ── Distanz (0–20) ────────────────────────────────────────────────────
    dist = job.get("distance_km")
    if dist is not None:
        if dist <= 10:
            score += 20
        elif dist <= 20:
            score += 16
        elif dist <= 30:
            score += 12
        elif dist <= radius_km:
            score += 8
        elif dist <= radius_km * 1.3:
            score += 4
    else:
        score += 10          # unbekannte Distanz → neutral

    # ── Kontaktdaten (0–10) ───────────────────────────────────────────────
    has_email = bool(job.get("contact_email"))
    has_phone = bool(job.get("contact_phone"))
    if has_email and has_phone:
        score += 10
    elif has_email or has_phone:
        score += 5

    return min(100, max(0, score))


# ===========================================================================
# JobSearcher — Haupt-Klasse
# ===========================================================================
class JobSearcher:
    def __init__(self):
        self.session = _make_session()

    def search(
        self,
        job_title: str,
        address: str,           # Wohnort/Adresse als Radius-Zentrum
        department: str = "",
        einrichtung: str = "",  # Einrichtungsart
        radius: int = 25,
        arbeitszeit: str = "",  # Präferenz: "Vollzeit" / "Teilzeit" — kein Ausschlussfilter
        schicht: str = "",      # Präferenz: "Tagdienst" / "Dauernacht" etc. — kein Ausschlussfilter
        progress_cb=None,
    ) -> List[Dict]:
        # ── Cache-Check ───────────────────────────────────────────────────────
        cache_key = (job_title, address, einrichtung, radius, arbeitszeit, schicht)
        now = time.time()
        if cache_key in _SEARCH_CACHE:
            cached_result, cached_at = _SEARCH_CACHE[cache_key]
            if now - cached_at < _CACHE_TTL:
                if progress_cb:
                    progress_cb(f"Cache-Treffer — {len(cached_result)} Stellen (gespeichert vor {int(now - cached_at)}s)")
                return cached_result

        all_jobs: List[Dict] = []

        def _prog(msg):
            if progress_cb:
                progress_cb(msg)

        # Stadt aus Adresse extrahieren — überspring Straße, PLZ, "Deutschland"
        location = _extract_city(address) if address else ""

        # Geocode Heimatadresse für Radius-Berechnungen
        user_lat, user_lon = geocode(address) if address else (None, None)

        # Einrichtungsart → Suchmodifier (z.B. "ambulant", "Klinik", "Reha")
        fac_mod = _facility_modifier(einrichtung)

        # Titel-Aliase — z.B. "Pflegefachkraft" → auch "Altenpfleger" suchen
        title_aliases = TITLE_SEARCH_ALIASES.get(job_title, [job_title])

        # Fachabteilungen aufsplitten — jedes Fach als eigener Suchterm
        dept_parts = []
        dept_short = ""
        if department:
            dept_parts = [p.strip() for p in re.split(r'[,;]|\bund\b', department)
                          if len(p.strip()) > 2]
            dept_short = dept_parts[0] if dept_parts else ""

        # Arbeitszeit / Schicht → Suchmodifier (bias, kein Ausschluss)
        zeit_mod = ""
        if arbeitszeit == "Vollzeit":
            zeit_mod = "Vollzeit"
        elif arbeitszeit == "Teilzeit":
            zeit_mod = "Teilzeit"
        schicht_mod = ""
        if schicht and "tagdienst" in schicht.lower():
            schicht_mod = "Tagdienst"
        elif schicht and any(k in schicht.lower() for k in ("dauernacht", "nacht")):
            schicht_mod = "Nachtdienst"

        def _make_term(alias: str, with_dept: bool) -> str:
            parts = [alias]
            if with_dept and dept_short:
                parts.append(dept_short)
            if fac_mod and fac_mod.lower() not in alias.lower():
                parts.append(fac_mod)
            if zeit_mod:
                parts.append(zeit_mod)
            if schicht_mod:
                parts.append(schicht_mod)
            return " ".join(parts)

        # Haupt-Suchterme: alle Aliase × (mit Fachabt.)
        primary_terms = [_make_term(a, True)  for a in title_aliases]
        # Basis-Terme: alle Aliase × (ohne Fachabt., mit Einrichtung)
        base_terms    = [_make_term(a, False) for a in title_aliases]
        # Fallback ohne Einrichtungsfilter
        bare_terms    = title_aliases[:]

        # ── Extra-Suchterme: weitere Fachabteilungen + Intensivpflege ──
        extra_terms: List[str] = []
        # Zusätzliche Fächer (2., 3.) als eigene Suchterme
        for dp in dept_parts[1:]:
            extra_terms.append(f"{title_aliases[0]} {dp}")
        if fac_mod == "Intensivpflege":
            extra_terms.extend([
                "Intensivpflege außerklinisch",
                "außerklinische Beatmung Pflege",
                "Intensivpflegedienst",
                "Pflegefachkraft Beatmung",
                "1:1 Intensivpflege",
            ])

        search_term = primary_terms[0]  # für Statusmeldungen

        def _run_sources(term: str, loc: str, rad: int) -> List[Dict]:
            """Alle Quellen PARALLEL mit ThreadPoolExecutor abfragen."""
            found: List[Dict] = []
            tasks = [
                (search_pflegia,        (term, loc, rad, self.session)),
                (search_jobspy,         (term, loc, rad, user_lat, user_lon)),
                (search_pflegejobs,     (term, loc, rad, self.session)),
                (search_medikarriere,   (term, loc, rad, self.session, user_lat, user_lon)),
                (search_kliniken,       (term, loc, rad, self.session, user_lat, user_lon)),
                (search_gesundheitjobs, (term, loc, rad, self.session, user_lat, user_lon)),
            ]
            with ThreadPoolExecutor(max_workers=6) as ex:
                futures = {ex.submit(fn, *args): fn.__name__ for fn, args in tasks}
                for fut in as_completed(futures):
                    try:
                        found.extend(fut.result(timeout=20))
                    except Exception as e:
                        print(f"[Source/{futures[fut]}] {e}")
            return found

        def _run_sources_fast(terms: List[str], loc: str, rad: int) -> List[Dict]:
            """ALLE Terme × ALLE Quellen in einem einzigen ThreadPool — max Parallelität."""
            found: List[Dict] = []
            all_tasks = []
            for term in terms:
                all_tasks.extend([
                    (search_pflegia,        (term, loc, rad, self.session)),
                    (search_jobspy,         (term, loc, rad, user_lat, user_lon)),
                    (search_pflegejobs,     (term, loc, rad, self.session)),
                    (search_medikarriere,   (term, loc, rad, self.session, user_lat, user_lon)),
                    (search_kliniken,       (term, loc, rad, self.session, user_lat, user_lon)),
                    (search_gesundheitjobs, (term, loc, rad, self.session, user_lat, user_lon)),
                ])
            with ThreadPoolExecutor(max_workers=16) as ex:
                futures = {ex.submit(fn, *args): fn.__name__ for fn, args in all_tasks}
                for fut in as_completed(futures):
                    try:
                        found.extend(fut.result(timeout=20))
                    except Exception:
                        pass
            return found

        def _dedup(jobs: List[Dict]) -> List[Dict]:
            seen: set = set()
            out: List[Dict] = []
            for job in jobs:
                key = (job["title"].lower()[:50], (job["company"] or "").lower()[:30])
                if key not in seen:
                    seen.add(key)
                    out.append(job)
            return out

        # ── Schnellsuche: nur Haupt-Alias + max 2 Extra-Terme, ALLE parallel ──
        fast_terms = primary_terms[:1]  # nur erster Alias mit Fachabt.
        if extra_terms:
            fast_terms.extend(extra_terms[:2])  # max 2 Extra-Terme
        _prog(f"Suche: {search_term} in {location} ({radius} km) …")
        all_jobs.extend(_run_sources_fast(fast_terms, location, radius))
        unique = _dedup(all_jobs)
        _prog(f"  → {len(unique)} Ergebnisse")

        # ── Runde 2: restliche Aliase + Extra-Terme (nur wenn wenig Ergebnisse) ──
        remaining_terms = primary_terms[1:] + extra_terms[2:]
        if len(unique) < 10 and remaining_terms:
            _prog("Erweitere Suche …")
            all_jobs.extend(_run_sources_fast(remaining_terms, location, radius))
            unique = _dedup(all_jobs)
            _prog(f"  → {len(unique)} Ergebnisse")

        # ── Runde 3: ähnliche Fachbereiche + ohne Fachabteilung ──────────────
        # Wenn spezifische Fachabt.-Suche wenig liefert → auf verwandte Felder
        # ausweichen UND ohne Fachabt.-Filter alle Stellen laden.
        if len(unique) < 10 and dept_short:
            similar = _SIMILAR_FACHABTEILUNGEN.get(dept_short.lower(), [])
            sim_terms = [f"{title_aliases[0]} {s}" for s in similar[:3]]
            fallback_terms = sim_terms + base_terms[:2]
            if fallback_terms:
                _prog("Suche ähnliche Fachbereiche / ohne Fachabt. …")
                all_jobs.extend(_run_sources_fast(fallback_terms, location, radius))
                unique = _dedup(all_jobs)
                _prog(f"  → {len(unique)} Ergebnisse")

        # ── Runde 4: Radius verdoppeln ────────────────────────────────────────
        if len(unique) < 5 and radius < 80:
            wider = min(radius * 2, 100)
            _prog(f"Radius auf {wider} km erweitert …")
            all_jobs.extend(_run_sources_fast(base_terms[:1], location, wider))
            unique = _dedup(all_jobs)

        # ── Runde 5: ohne Einrichtungsfilter (letzter Fallback) ──────────────
        if len(unique) < 5 and fac_mod:
            _prog("Suche ohne Einrichtungsfilter …")
            all_jobs.extend(_run_sources(bare_terms[0], location, radius))
            unique = _dedup(all_jobs)

        # ── Runde 6: Fallback-Jobtitel ───────────────────────────────────────
        if len(unique) < 5:
            from cv_parser import TITLE_FALLBACK
            fallback_title = TITLE_FALLBACK.get(job_title, "")
            if fallback_title and fallback_title != job_title:
                _prog(f"Suche auch als '{fallback_title}' …")
                all_jobs.extend(_run_sources(fallback_title, location, min(radius * 2, 100)))
                unique = _dedup(all_jobs)

        # ── Radius-Filter: Jobs ohne Distanz geocoden, Jobs außerhalb entfernen ──
        if user_lat:
            max_dist = radius * 1.3  # 30% Puffer
            filtered = []
            for job in unique:
                d = job.get("distance_km")
                if d is not None:
                    if d <= max_dist:
                        filtered.append(job)
                    continue
                # Kein dist_km → Ort bestimmen und geocoden
                job_loc = job.get("location", "")
                if not job_loc:
                    # Ort aus Firma + Titel extrahieren:
                    # "ATOS Klinik Fleetinsel Hamburg GmbH" → "Hamburg"
                    # "Heiligenfeld Klinik Berlin" → "Berlin"
                    combined = f"{job.get('company', '')} {job.get('title', '')}"
                    # Wörter von rechts nach links (Stadt steht oft hinten)
                    for word in reversed(combined.replace(",", " ").split()):
                        clean = word.strip("().-/")
                        if len(clean) < 3 or not clean[0].isupper():
                            continue
                        cl = clean.lower()
                        if cl in _CITY_COORDS:
                            job_loc = clean
                            break
                if job_loc:
                    d = _within_radius(job_loc, user_lat, user_lon, radius)
                    job["distance_km"] = d
                    if not job.get("location"):
                        job["location"] = job_loc
                if d is not None and d <= max_dist:
                    filtered.append(job)
                # d is None → nicht geocodierbar oder außerhalb → RAUS
            _prog(f"  → {len(filtered)}/{len(unique)} im Radius von {radius} km")
            unique = filtered

        # ── Facility-Match prüfen + sortieren ────────────────────────────────
        # Jeden Job klassifizieren und mit facility_match / detected_facility taggen
        for job in unique:
            detected = _detect_job_facility(job)
            job["detected_facility"] = detected
            job["facility_match"]    = _check_facility_match(job, einrichtung)

        if einrichtung:
            matched   = [j for j in unique if j["facility_match"]]
            unmatched = [j for j in unique if not j["facility_match"]]
            # Nur umstellen wenn es wirklich passende gibt
            unique = matched + unmatched if matched else unique
            _prog(f"  → {len(matched)} passende / {len(unmatched)} andere Einrichtungen")

        # ── Light-Enrichment: Job-Detailseiten für Kontakte scrapen ──────────
        # Nur 1 HTTP-Request pro Job — schnell und effektiv
        # ── Pflegia-Enrichment: Firmenwebsite über Name+Firma finden ─────────
        # Pflegia liefert Ansprechpartner-Name aber keine Email/Telefon.
        # → Firmenwebsite finden und dort Kontaktseiten scrapen.
        pflegia_need = [j for j in unique
                        if j.get("source", "").lower() == "pflegia"
                        and j.get("company")
                        and not (j.get("contact_email") or j.get("contact_phone"))]
        # Deduplizieren nach Firma (gleiche Firma = gleiche Kontaktdaten)
        seen_companies = set()
        pflegia_dedup = []
        for j in pflegia_need:
            ckey = j["company"].lower().strip()
            if ckey not in seen_companies:
                seen_companies.add(ckey)
                pflegia_dedup.append(j)

        if pflegia_dedup:
            _prog(f"Pflegia: Firmenwebsites für {len(pflegia_dedup)} Arbeitgeber …")
            company_contacts = {}  # company_lower -> {email, phone, name}

            def _enrich_pflegia_company(job):
                company = job["company"]
                ckey = company.lower().strip()
                try:
                    # 1. employerUrl aus Pflegia-Detailseite holen
                    domain = ""
                    job_url = job.get("url", "")
                    if job_url:
                        r = self.session.get(job_url, timeout=10)
                        if r.ok:
                            nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                                           r.text, re.DOTALL)
                            if nd:
                                cache = _json.loads(nd.group(1)).get("props", {}).get(
                                    "pageProps", {}).get("__APOLLO_CACHE__", {})
                                for v in cache.values():
                                    if isinstance(v, dict) and v.get("employerUrl"):
                                        eu = v["employerUrl"]
                                        parsed = urllib.parse.urlparse(eu)
                                        domain = f"{parsed.scheme}://{parsed.netloc}"
                                        break
                    # 2. Fallback: Domain raten
                    if not domain:
                        domain = _find_company_domain(company, "", self.session)
                    if not domain:
                        return ckey, {}
                    info = _scrape_company_contact_pages(domain, self.session)
                    return ckey, info or {}
                except Exception:
                    return ckey, {}

            with ThreadPoolExecutor(max_workers=10) as ex:
                futs = [ex.submit(_enrich_pflegia_company, j) for j in pflegia_dedup[:20]]
                for fut in as_completed(futs):
                    try:
                        ckey, info = fut.result(timeout=30)
                        if info:
                            company_contacts[ckey] = info
                    except Exception:
                        pass

            # Kontaktdaten auf alle Pflegia-Jobs derselben Firma anwenden
            for j in pflegia_need:
                ckey = j["company"].lower().strip()
                info = company_contacts.get(ckey, {})
                if info:
                    if not j.get("contact_email"):
                        j["contact_email"] = info.get("contact_email", "")
                    if not j.get("contact_phone"):
                        j["contact_phone"] = info.get("contact_phone", "")
                    # Pflegia hat gute AP-Namen — Name von Website nur ergänzen wenn leer
                    if not j.get("contact_name") and info.get("contact_name"):
                        j["contact_name"] = info["contact_name"]

            p_email = sum(1 for j in pflegia_need if j.get("contact_email"))
            p_phone = sum(1 for j in pflegia_need if j.get("contact_phone"))
            _prog(f"  → Pflegia: {p_email} Emails, {p_phone} Telefon gefunden")

        # ── Light-Enrichment für scrape-fähige Quellen ───────────────────────
        # Auch Jobs mit nur Name oder nur Telefon enrichen (fehlende Felder ergänzen)
        _JS_SOURCES = {"pflegia", "indeed", "linkedin"}
        need_contact = [j for j in unique
                        if not (j.get("contact_email") and j.get("contact_phone") and j.get("contact_name"))
                        and j.get("url")
                        and j.get("source", "").lower() not in _JS_SOURCES]
        if need_contact:
            # Beste Matches zuerst enrichen
            need_contact.sort(key=lambda j: j.get("match_score", 0), reverse=True)
            to_enrich = need_contact[:25]
            _prog(f"Kontaktdaten für {len(to_enrich)} Stellen …")

            def _apply_contact(job, info):
                """Kontaktdaten aus Extraktor-Ergebnis übernehmen (mit Plausibilitätsprüfung)."""
                name = info.get("contact_name", "").strip()
                # Nur plausible Namen: 4-50 Zeichen, 2-4 Wörter, kein Müll
                words = name.split()
                if (name and 4 <= len(name) <= 50 and 2 <= len(words) <= 4
                        and not any(c in name for c in "\n\t")
                        and all(w[0].isupper() for w in words if w)):
                    if not job.get("contact_name"):
                        job["contact_name"] = name
                if not job.get("contact_email"):
                    job["contact_email"] = info.get("contact_email", "")
                if not job.get("contact_phone"):
                    job["contact_phone"] = info.get("contact_phone", "")

            def _light_enrich(job):
                """Schnelle Kontakt-Extraktion mit Apply-URL-Redirect."""
                job = dict(job)
                url = job.get("url", "")

                if not url:
                    return job

                all_emails = []
                try:
                    # Erst echte Firmen-URL finden (z.B. kliniken.de → Asklepios)
                    target = _get_external_apply_url(url, self.session)
                    if not target:
                        target = url

                    r = self.session.get(target, timeout=10)
                    if not r.ok:
                        return job
                    html = r.text

                    # JSON-LD strukturierte Daten
                    jsonld = _extract_jsonld_contact(html)
                    if jsonld.get("contact_email"):
                        all_emails.append(jsonld["contact_email"])
                    if jsonld.get("contact_phone") and not job.get("contact_phone"):
                        job["contact_phone"] = jsonld["contact_phone"]

                    # Mailto-Links extrahieren
                    emails = _extract_mailto_emails(html)
                    all_emails.extend(emails)

                    # Kontakt-Extraktor auf Seitentext
                    text = BeautifulSoup(html, "html.parser").get_text(" ")
                    info = contact_extractor.extract(text)
                    if info.get("all_emails"):
                        all_emails.extend(info["all_emails"])
                    _apply_contact(job, info)

                    # Falls Apply-URL != Original und noch kein Kontakt:
                    # auch Original-Seite scrapen (z.B. kliniken.de hat manchmal Kontakt)
                    if target != url and not (job.get("contact_email") or job.get("contact_phone")):
                        r2 = self.session.get(url, timeout=8)
                        if r2.ok:
                            emails2 = _extract_mailto_emails(r2.text)
                            all_emails.extend(emails2)
                            text2 = BeautifulSoup(r2.text, "html.parser").get_text(" ")
                            info2 = contact_extractor.extract(text2)
                            if info2.get("all_emails"):
                                all_emails.extend(info2["all_emails"])
                            _apply_contact(job, info2)

                    # Beste Email aus allen gesammelten wählen
                    if not job.get("contact_email") and all_emails:
                        ranked = contact_extractor.rank_emails(list(dict.fromkeys(all_emails)))
                        if ranked:
                            job["contact_email"] = ranked[0]

                except Exception:
                    pass
                return job

            enriched_map = {id(j): j for j in unique}
            with ThreadPoolExecutor(max_workers=12) as ex:
                futs = {ex.submit(_light_enrich, j): id(j) for j in to_enrich}
                for fut in as_completed(futs):
                    try:
                        enriched = fut.result(timeout=8)
                        enriched_map[futs[fut]] = enriched
                    except Exception:
                        pass
            unique = list(enriched_map.values())

            found_email = sum(1 for j in unique if j.get("contact_email"))
            found_phone = sum(1 for j in unique if j.get("contact_phone"))
            _prog(f"  → {found_email} Emails, {found_phone} Telefon gefunden")

        _prog(f"Fertig — {len(unique)} Stellen gefunden.")

        # ── Ergebnis cachen ───────────────────────────────────────────────────
        _SEARCH_CACHE[cache_key] = (unique, time.time())

        # Domain-Cache asynchron auf Disk speichern (nur wenn dirty)
        _save_domain_cache()

        return unique
