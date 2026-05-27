"""
CV Parser — erkennt sowohl MEDWING Kurzprofile als auch normale Lebensläufe.

MEDWING Kurzprofil-Felder (direkt ausgelesen):
  Qualifikation, Einsatzort, Gewünschter Arbeitsort, Fachabteilungen,
  Gewünschte Stelle, Verfügbar ab, Gewünschte Schichten,
  Telefon, Email, Wohnort
"""

import re
import pdfplumber


# ── Exakte MEDWING-Titel → Suchbegriff ───────────────────────────────────────
# Direkte Lookup-Tabelle für alle offiziellen MEDWING-Qualifikationen.
# Wert = normierter Suchbegriff der an die Job-Boards geht.
MEDWING_TITLE_MAP = {
    # ── Altenpflege ──────────────────────────────────────────────────────────
    "Exam. Altenpfleger/in":                                "Altenpfleger",
    "Exam. Altenpfleger/in mit Fachweiterbildung":          "Altenpfleger Fachweiterbildung",
    "Exam. Altenpflegehelfer/in":                           "Altenpflegehelfer",
    "Pflegefachkraft":                                      "Pflegefachkraft",
    "Pflegefachkraft mit Fachweiterbildung":                "Pflegefachkraft Fachweiterbildung",
    "Sonstige Position in der Altenpflege":                 "Pflegefachkraft",
    # ── Krankenpflege ────────────────────────────────────────────────────────
    "Exam. Gesundheits- und Krankenpfleger/in":             "Gesundheits- und Krankenpfleger",
    "Exam. Gesundheits- und Krankenpfleger/in mit Fachweiterbildung": "Gesundheits- und Krankenpfleger Fachweiterbildung",
    "Exam. Gesundheits- und Kinderkrankenpfleger/in":       "Kinderkrankenpfleger",
    "Exam. Gesundheits- und Kinderkrankenpfleger/in mit Fachweiterbildung": "Kinderkrankenpfleger Fachweiterbildung",
    "Exam. Fachkrankenpfleger/in für Psychatrie":           "Fachkrankenpfleger Psychiatrie",
    "Intensivpfleger/in":                                   "Intensivpfleger",
    "Sonstige Position in der Krankenpflege":               "Pflegefachkraft",
    "Gesundheits- und Krankenpfleger/in (in Ausbildung)":   "Krankenpfleger Azubi",
    "Gesundheits- und Krankenpfleger/in (ohne Anerkennung)":"Gesundheits- und Krankenpfleger",
    # ── Pflegehilfe ──────────────────────────────────────────────────────────
    "Pflegehelfer/in (Basiskurs)":                          "Pflegehelfer",
    "Pflegehelfer/in (mit Erfahrung)":                      "Pflegehelfer",
    "Pflegehelfer/in":                                      "Pflegehelfer",
    "Exam. Krankenpflegehelfer":                            "Krankenpflegehelfer",
    "Gesundheits- und Pflegeassistenz (GPA)":               "Pflegeassistenz",
    "Pflegefachassistenz":                                  "Pflegefachassistenz",
    "Säuglings- und Kleinkindpflegeassistenz":              "Säuglingspflegeassistenz",
    "Altenpfleger/in (in Ausbildung)":                      "Altenpfleger Azubi",
    # ── Pflege-Leitung / Management ──────────────────────────────────────────
    "Praxisanleiter/in":                                    "Praxisanleiter",
    "Stationsleitung":                                      "Stationsleitung",
    "Stationsassistenz":                                    "Stationsassistenz",
    "Stellvertretende Stationsleitung":                     "Stationsleitung",
    "Pflegedienstleitung (PDL)":                            "Pflegedienstleitung",
    "Stellvertretende Pflegedienstleitung (PDL)":           "Pflegedienstleitung",
    "Trainee PDL":                                          "Pflegedienstleitung",
    "Pflegedirektion":                                      "Pflegedirektion",
    "Bereichsleitung":                                      "Bereichsleitung",
    "Einsatzleitung":                                       "Einsatzleitung",
    "Wohnbereichsleitung":                                  "Wohnbereichsleitung",
    "Stellvertretende Wohnbereichsleitung":                 "Wohnbereichsleitung",
    "Dauernachtwache":                                      "Dauernachtwache",
    "Advanced Practice Nurse (APN)":                        "Advanced Practice Nurse",
    # ── Einrichtungsleitung ───────────────────────────────────────────────────
    "Einrichtungsleitung":                                  "Einrichtungsleitung",
    "Stellvertretende Einrichtungsleitung":                 "Einrichtungsleitung",
    "Trainee Einrichtungsleitung":                          "Einrichtungsleitung",
    "Regionalleitung":                                      "Regionalleitung",
    "Qualitätsmanagementbeauftragte/r":                     "Qualitätsmanagement Pflege",
    # ── Operationsbereich ────────────────────────────────────────────────────
    "Operationstechnische Assistenz (OTA)":                 "OTA",
    "Anästhesietechnische Assistenz (ATA)":                 "ATA",
    "Chirurgisch-technische/r Assistent/in (CTA)":          "CTA",
    "Intensivmedizinisch-Technische-Assistenz (ITA)":       "ITA",
    "OP-Leitung":                                           "OP-Leitung",
    # ── Ärzte ────────────────────────────────────────────────────────────────
    "Chefarzt/ Chefärztin":                                 "Chefarzt",
    "Zahnarzt/ Zahnärztin":                                 "Zahnarzt",
    "Ärztliche Leitung":                                    "Ärztliche Leitung",
    "Niedergelassene/r Ärztin/Arzt":                        "Niedergelassener Arzt",
    "Assistenzarzt/ Assistenzärztin":                       "Assistenzarzt",
    "Assistenzarzt/ Assistenzärztin ohne Approbation":      "Assistenzarzt",
    "Facharzt/ Fachärztin":                                 "Facharzt",
    "Facharzt/ Fachärztin ohne Approbation":                "Facharzt",
    "Oberarzt/ Oberärztin":                                 "Oberarzt",
    "Leitende/r Oberarzt/ Oberärztin":                      "Leitender Oberarzt",
    "Physician Assistant (PA)":                             "Physician Assistant",
    # ── MFA / MTA ────────────────────────────────────────────────────────────
    "Medizinische/r Fachangestellte/r (MFA)":               "MFA",
    "Medizinisch-technische Assistenz für Funktionsdiagnostik (MTAF)": "MTAF",
    "Medizinisch-technische Laboratoriumsassistenz (MTLA)": "MTLA",
    "Medizinisch-technische Radiologieassistenz (MTRA)":    "MTRA",
    "Sonstige Position als MTA":                            "MTA",
    "Praxismanager/in":                                     "Praxismanager",
    "Sonstige Position als Praxispersonal":                 "Praxispersonal",
    # ── Zahntechnik / Apotheke ────────────────────────────────────────────────
    "Zahntechniker/in":                                     "Zahntechniker",
    "Zahnmedizinische/r Fachangestellte/r":                 "ZMF",
    "Pharmazeutisch-technische Assistenz (PTA)":            "PTA",
    "Apotheker/in":                                         "Apotheker",
    # ── Hebamme / Rettung ────────────────────────────────────────────────────
    "Hebamme/ Entbindungshelfer/in":                        "Hebamme",
    "Leitende Hebamme/Entbindungspflegerin":                "Leitende Hebamme",
    "Notfallsanitäter/in":                                  "Notfallsanitäter",
    "Rettungshelfer/in":                                    "Rettungshelfer",
    "Rettungssanitäter/in":                                 "Rettungssanitäter",
    "Rettungsassistent/in":                                 "Rettungsassistent",
    # ── Therapeuten ──────────────────────────────────────────────────────────
    "Physiotherapeut/in":                                   "Physiotherapeut",
    "Ergotherapeut/in":                                     "Ergotherapeut",
    "Logopäde/ Logopädin":                                  "Logopäde",
    "Osteopath/in":                                         "Osteopath",
    "Psychotherapeut/in":                                   "Psychotherapeut",
    "Psychomotoriker/in":                                   "Psychomotoriker",
    "Physiologe":                                           "Physiologe",
    "Ernährungsberater":                                    "Ernährungsberater",
    "Anderer therapeutischer Beruf":                        "Therapeut",
    # ── Soziales / Pädagogik ─────────────────────────────────────────────────
    "Heilerziehungspfleger/in":                             "Heilerziehungspfleger",
    "Heilpädagoge/ Heilpädagogin":                          "Heilpädagoge",
    "Erzieher/in":                                          "Erzieher",
    "Sozialassistenz":                                      "Sozialassistenz",
    "Sozialarbeiter/in":                                    "Sozialarbeiter",
    # ── Spezialisten ─────────────────────────────────────────────────────────
    "Hygienebeauftragte/r":                                 "Hygienebeauftragter",
    "Hygienefachkraft":                                     "Hygienefachkraft",
    "Case Manager":                                         "Case Manager",
    "Wundmanager":                                          "Wundmanager",
    "Pflegeberater/in":                                     "Pflegeberater",
    "Pflegesachverständige/r":                              "Pflegesachverständiger",
    "Medizinische Kodierfachkraft":                         "Kodierfachkraft",
    "Kaufmann/-frau im Gesundheitswesen":                   "Kaufmann Gesundheitswesen",
    # ── Sonstiges ────────────────────────────────────────────────────────────
    "Augenoptiker":                                         "Augenoptiker",
    "Ultraschalldiagnostiker":                              "Ultraschalldiagnostiker",
    "Technische Sterilisationsassistenz":                   "Sterilisationsassistenz",
    "Tiermedizinische/r Fachangestellte/r":                 "Tiermedizinische Fachangestellte",
}

# Vollständige Liste aller MEDWING-Titel (für Dropdown in der UI)
MEDWING_ALL_TITLES = sorted(MEDWING_TITLE_MAP.keys())

# ── Fallback-Kette: wenn Suche mit spezifischem Titel leer, diesen nehmen ────
TITLE_FALLBACK = {
    "Altenpfleger":                         "Pflegefachkraft",
    "Altenpfleger Fachweiterbildung":        "Altenpfleger",
    "Gesundheits- und Krankenpfleger":      "Pflegefachkraft",
    "Gesundheits- und Krankenpfleger Fachweiterbildung": "Gesundheits- und Krankenpfleger",
    "Kinderkrankenpfleger":                 "Pflegefachkraft",
    "Kinderkrankenpfleger Fachweiterbildung": "Kinderkrankenpfleger",
    "Fachkrankenpfleger Psychiatrie":       "Pflegefachkraft",
    "Intensivpfleger":                      "Pflegefachkraft",
    "Pflegefachkraft Fachweiterbildung":    "Pflegefachkraft",
    "Pflegehelfer":                         "Pflegefachkraft",
    "Krankenpflegehelfer":                  "Pflegehelfer",
    "Pflegeassistenz":                      "Pflegehelfer",
    "Pflegefachassistenz":                  "Pflegehelfer",
    "Stationsleitung":                      "Pflegefachkraft",
    "Pflegedienstleitung":                  "Stationsleitung",
    "Wohnbereichsleitung":                  "Pflegefachkraft",
    "Einrichtungsleitung":                  "Pflegedienstleitung",
    "OTA":                                  "Pflegefachkraft",
    "ATA":                                  "Pflegefachkraft",
    "CTA":                                  "OTA",
    "ITA":                                  "Intensivpfleger",
    "Heilerziehungspfleger":               "Pflegefachkraft",
    "Hebamme":                              "Pflegefachkraft",
    "MFA":                                  "Praxispersonal",
    "Assistenzarzt":                        "Arzt",
    "Facharzt":                             "Arzt",
    "Oberarzt":                             "Facharzt",
    "Physiotherapeut":                      "Therapeut",
    "Ergotherapeut":                        "Therapeut",
    "Logopäde":                             "Therapeut",
}

# ── Regex-Fallback für generische CVs (keine MEDWING-Profile) ────────────────
TITLE_RULES = [
    (["kinderkranken", "kinderschwester", "kinderkrankenpfleg", "pädiatrische pflege"],
     "Kinderkrankenpfleger"),
    (["ota", "operationstechnisch"],
     "OTA"),
    (["ata", "anästhesietechnisch"],
     "ATA"),
    (["heilerziehungspfleger", "heilerziehung"],
     "Heilerziehungspfleger"),
    (["fachkrankenpfleger", "fachkrankenpflegerin"],
     "Fachkrankenpfleger Psychiatrie"),
    (["pflegedienstleitung", " pdl"],
     "Pflegedienstleitung"),
    (["stationsleitung"],
     "Stationsleitung"),
    (["wohnbereichsleitung"],
     "Wohnbereichsleitung"),
    (["pflegefachkraft", "pflegefachmann", "pflegefachfrau",
      "gesundheits- und krankenpfleger", "krankenpfleger", "krankenschwester",
      "altenpfleger", "altenpflegerin", "altenpflegefachkraft",
      "exam.", "examiniert", "pflegekraft"],
     "Pflegefachkraft"),
    (["pflegehelfer", "pflegehilfskraft", "pflegeassistent"],
     "Pflegehelfer"),
    (["medizinische fachangestellte", "medizinischer fachangestellter", "mfa"],
     "MFA"),
    (["physiotherapeut"],   "Physiotherapeut"),
    (["ergotherapeut"],     "Ergotherapeut"),
    (["logopäde", "logopädin"], "Logopäde"),
    (["erzieher", "erzieherin", "sozialpädagog"], "Erzieher"),
    (["assistenzarzt", "assistenzärztin"], "Assistenzarzt"),
    (["facharzt", "fachärztin"], "Facharzt"),
    (["oberarzt", "oberärztin"], "Oberarzt"),
    (["hebamme"], "Hebamme"),
    (["notfallsanitäter"], "Notfallsanitäter"),
]


def normalize_job_title(raw: str) -> str:
    """Normalisiert rohen CV-Jobtitel auf Suchbegriff.
    Prüft zuerst exakte MEDWING-Titel, dann Regex-Fallback."""
    if not raw:
        return ""
    # 1. Exakter MEDWING-Lookup (case-insensitiv, /in-Varianten tolerant)
    raw_stripped = raw.strip()
    if raw_stripped in MEDWING_TITLE_MAP:
        return MEDWING_TITLE_MAP[raw_stripped]
    # Toleranz: trailing /in, (in Ausbildung) etc. leicht normiert
    raw_lower = raw_stripped.lower()
    for key, val in MEDWING_TITLE_MAP.items():
        if key.lower() == raw_lower:
            return val
    # 2. Regex-Fallback für generische CVs
    for keywords, normalized in TITLE_RULES:
        if any(k in raw_lower for k in keywords):
            return normalized
    return raw_stripped[:60]


# ── Einrichtungsart Normalisierung ───────────────────────────────────────────
FACILITY_RULES = [
    # Intensivpflege VOR Ambulant — "Intensivpflegedienst" enthält "pflegedienst"!
    (["intensivpflege", "beatmungspflege", "außerklinische beatmung",
      "1:1 intensiv", "heimbeatmung", "wachkoma"],
     "Intensivpflegedienst"),
    (["kinderkranken", "pädiatrie", "neonatologie", "kinderklinik", "kinderstation"],
     "Kinderklinik / Pädiatrie"),
    (["psychiatrie", "psychosomatik", "psychotherapie", "psychiat"],
     "Psychiatrie"),
    (["rehabilitat", "reha-klinik", "reha"],
     "Rehabilitation"),
    (["ambulant", "sozialstation", "pflegedienst", "häuslich"],
     "Ambulanter Pflegedienst"),
    (["altenheim", "pflegeheim", "seniorenheim", "altenpflege",
      "stationäre pflege", "pflegeeinrichtung", "seniorenpflege", "senioren"],
     "Altenpflege / Pflegeheim"),
    (["krankenhaus", "klinik", "hospital", "klinikum", "akut", "notaufnahme"],
     "Krankenhaus / Klinik"),
]


def normalize_facility(raw: str) -> str:
    """Normalisiert Einrichtungsart auf Standard-Typ.
    Wenn mehrere Typen passen (Kandidat ist flexibel), gib den spezifischsten zurück.
    Wenn >= 4 verschiedene Typen matchen → Kandidat ist offen, kein Filter nötig."""
    if not raw:
        return ""
    raw_l = raw.lower()
    matched = []
    for keywords, normalized in FACILITY_RULES:
        if any(k in raw_l for k in keywords):
            matched.append(normalized)
    if not matched:
        return ""
    # Kandidat gibt viele Typen an → offen für alles → keinen Filter setzen
    if len(matched) >= 3:
        return ""
    return matched[0]


# ── Fachabteilung — alle relevanten Fächer ────────────────────────────────────
# Fächer die zu generisch sind und nicht als Suchterm taugen
_SKIP_DEPTS = {"sonstiges", "sonstige", "allgemein", "allgemeinstation", "allgemeinmedizin"}

# Umfassende Liste aller deutschen medizinischen Fachabteilungen & Stationen.
# Wird für Regex-Scan im CV-Text verwendet (keyword → kanonischer Name).
FACHABTEILUNGEN = [
    # Innere Medizin & Subdisziplinen
    "Innere Medizin", "Kardiologie", "Nephrologie", "Dialyse", "Hämatologie",
    "Onkologie", "Gastroenterologie", "Hepatologie", "Pneumologie",
    "Endokrinologie", "Diabetologie", "Rheumatologie", "Angiologie",
    "Infektiologie", "Palliativmedizin", "Palliativstation",
    # Chirurgie
    "Allgemeinchirurgie", "Viszeralchirurgie", "Unfallchirurgie",
    "Gefäßchirurgie", "Herzchirurgie", "Thoraxchirurgie",
    "Kinderchirurgie", "Plastische Chirurgie", "Handchirurgie",
    "Mund-Kiefer-Gesichtschirurgie", "MKG", "Neurochirurgie", "Orthopädie",
    # Intensiv & Notfall
    "Intensivstation", "Intensivmedizin", "Intensivpflege", "IMC",
    "Stroke Unit", "Chest Pain Unit", "Notaufnahme", "Zentrale Notaufnahme",
    "ZNA", "Rettungsdienst", "Anästhesie", "Anästhesiologie", "Aufwachraum",
    # Kinder
    "Pädiatrie", "Kinderheilkunde", "Kinderklinik", "Neonatologie",
    "Kinderintensiv", "Kinder- und Jugendpsychiatrie", "KJP",
    "Kinder- und Jugendmedizin", "Frühgeborene",
    # Neuro / Psych
    "Neurologie", "Neurochirurgie", "Stroke", "Psychiatrie",
    "Psychosomatik", "Psychotherapie", "Suchtmedizin", "Gerontopsychiatrie",
    # Geriatrie / Reha
    "Geriatrie", "Altersmedizin", "Rehabilitation", "Reha",
    "Frührehabilitation", "Neurorehabilitation",
    # Gyn / Geburt
    "Gynäkologie", "Geburtshilfe", "Frauenheilkunde", "Kreißsaal",
    "Wochenstation", "Pränataldiagnostik", "Senologie",
    # Spezial
    "Urologie", "Dermatologie", "HNO", "Hals-Nasen-Ohren", "Augenheilkunde",
    "Ophthalmologie", "Radiologie", "Strahlentherapie", "Radioonkologie",
    "Nuklearmedizin", "Labormedizin", "Pathologie", "Transfusionsmedizin",
    # OP & Funktionsbereiche
    "OP", "Operationssaal", "Zentral-OP", "Endoskopie", "Herzkatheterlabor",
    "Funktionsdiagnostik", "EKG", "EEG",
    # Altenpflege & Wohnbereiche
    "Gerontopsychiatrie", "Demenz", "Beschützter Bereich", "Wohnbereich",
    "Kurzzeitpflege", "Tagespflege", "Nachtpflege", "Hospiz",
    "Ambulanter Dienst", "Sozialstation",
]

# Keyword → kanonischer Name (case-insensitive). Sortiert nach Länge
# DESC damit längere Matches (z.B. "Kinder- und Jugendpsychiatrie") VOR
# kürzeren (z.B. "Psychiatrie") gefunden werden.
_FA_ENTRIES = sorted(
    FACHABTEILUNGEN,
    key=lambda s: -len(s),
)
_FA_KEYWORD_RE = re.compile(
    r'(?<![A-Za-zäöüÄÖÜß])(' +
    r'|'.join(re.escape(k) for k in _FA_ENTRIES) +
    r')(?![A-Za-zäöüÄÖÜß])',
    re.IGNORECASE,
)

# Aliasse (Abkürzungen & Varianten → kanonischer Name)
_FA_ALIASES = {
    "zna": "Notaufnahme",
    "imc": "IMC",
    "its": "Intensivstation",
    "mkg": "Mund-Kiefer-Gesichtschirurgie",
    "hno": "HNO",
    "kjp": "Kinder- und Jugendpsychiatrie",
    "op": "OP",
    "ekg": "Funktionsdiagnostik",
    "eeg": "Funktionsdiagnostik",
    "stroke": "Stroke Unit",
    "demenz": "Gerontopsychiatrie",
    "reha": "Rehabilitation",
    "kardio": "Kardiologie",
    "gyn": "Gynäkologie",
    "uro": "Urologie",
    "derma": "Dermatologie",
    "ophthalmologie": "Augenheilkunde",
    "frauenheilkunde": "Gynäkologie",
    "altersmedizin": "Geriatrie",
    "kinderheilkunde": "Pädiatrie",
    "hals-nasen-ohren": "HNO",
}


def best_fachabteilung(raw: str) -> str:
    """Gibt die wichtigsten Fächer zurück (kommagetrennt, max 5)."""
    if not raw:
        return ""
    # Trenne nach Schrägstrich, Komma, Semikolon, "und", Zeilenumbruch, "/"
    parts = re.split(r'[,;/]|\bund\b|\boder\b|\n', raw)
    parts = [p.strip(" -•·.") for p in parts]
    parts = [p for p in parts if len(p) > 2]
    # Generische/nutzlose Fächer entfernen
    parts = [p for p in parts if p.lower() not in _SKIP_DEPTS]
    # Dedupe (case-insensitiv) unter Erhaltung der Reihenfolge
    seen = set()
    out = []
    for p in parts:
        pl = p.lower()
        if pl in seen:
            continue
        seen.add(pl)
        out.append(p[:50])
        if len(out) >= 5:
            break
    return ", ".join(out)


def scan_fachabteilungen(text: str) -> str:
    """Durchsucht den VOLLEN CV-Text nach bekannten Fachabteilungen.
    Gibt alle gefundenen Fächer kommagetrennt zurück (dedupliziert, max 5)."""
    if not text:
        return ""
    found: list = []
    seen = set()
    for m in _FA_KEYWORD_RE.finditer(text):
        raw = m.group(1)
        key = raw.lower()
        canon = _FA_ALIASES.get(key, raw)
        # Normiere auf die Schreibweise aus FACHABTEILUNGEN (Titelform finden)
        for orig in FACHABTEILUNGEN:
            if orig.lower() == canon.lower():
                canon = orig
                break
        ck = canon.lower()
        if ck in seen or ck in _SKIP_DEPTS:
            continue
        seen.add(ck)
        found.append(canon)
        if len(found) >= 5:
            break
    return ", ".join(found)


# ── MEDWING Kurzprofil: Schlüssel → Regex ─────────────────────────────────────
# Alle bekannten Feld-Labels als Lookahead-Stopper (PDF-Spalten mischen Zeilen)
_FIELD_STOP = (
    r'(?=\n(?:Qualifikation|Verfügbar|Einsatzort|Gewünschte\s+Stelle|'
    r'Gewünschte\s+Schichten|Gewünschter\s+Arbeitsort|Fachabteilungen|'
    r'Persönliche|Geburtsdatum|Geburtsort|Nationalität|Telefon|Email|Wohnort|'
    r'Berufserfahrung|Schul-\s+und|Zusätzliche|MEDWING\s+GmbH|\n))'
)

MEDWING_FIELDS = {
    # Alle Felder mit DOTALL + Lookahead auf nächstes Feld-Label
    # So werden mehrzeilige Werte (PDF-Spalten-Umbrüche) korrekt erfasst
    "qualifikation":         None,  # Spezialparsing wegen PDF-Spalten
    "verfuegbar_ab":         re.compile(r'Verfügbar\s+ab\s+(\d{2}[./]\d{2}[./]\d{4}|\d{4}[./]\d{2}[./]\d{2}|sofort|ab\s+sofort|[A-Za-zä-ü]+\s+\d{4})', re.I),
    "einsatzort":            re.compile(r'Einsatzort\s+(.{2,120}?)(?=\s+Gewünschter?\s+Arbeitsort|\n(?:Qualifikation|Verfügbar|Einsatzort|Gewünschte\s+Stelle|Gewünschte\s+Schichten|Gewünschter\s+Arbeitsort|Fachabteilungen|Persönliche|Geburtsdatum|Geburtsort|Nationalität|Telefon|Email|Wohnort|Berufserfahrung|Schul-\s+und|Zusätzliche|MEDWING\s+GmbH|\n))', re.I | re.DOTALL),
    "gewuenschte_stelle":    re.compile(r'Gewünschte\s+Stelle\s+([^\n]{2,80})', re.I),  # nur 1 Zeile — Spalten-sicher
    "gewuenschte_schichten": re.compile(r'Gewünschte\s+Schichten\s+(.{2,80}?)' + _FIELD_STOP, re.I | re.DOTALL),
    "arbeitsort_typ":        re.compile(r'Gewünschter\s+Arbeitsort\s+(.{2,250}?)' + _FIELD_STOP, re.I | re.DOTALL),
    "telefon":               re.compile(r'Telefon\s+(\+?[\d\s\-/()]{7,20})', re.I),
    "email":                 re.compile(r'Email\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})', re.I),
    "wohnort":               re.compile(r'Wohnort\s+(.{2,120}?)' + _FIELD_STOP, re.I | re.DOTALL),
    "fachabteilungen":       re.compile(r'Fachabteilungen\s+(.{2,200}?)' + _FIELD_STOP, re.I | re.DOTALL),
}

# Fallback: Ort-Patterns für normale CVs
LOCATION_PATTERNS = [
    re.compile(r'(?:Wohnort|Wohnhaft)[:\s]+(?:\d{5}\s+)?([A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[A-ZÄÖÜ][a-zäöüß\-]+)?)', re.IGNORECASE),
    re.compile(r'(?:wohnhaft|wohne|lebe|ansässig)\s+in\s+([A-ZÄÖÜ][a-zäöüß\-]+)', re.IGNORECASE),
    re.compile(r'(?:^|\n)\d{5}\s+([A-ZÄÖÜ][a-zäöüß\-]+)', re.MULTILINE),
    re.compile(r'suche.*?(?:in|im\s+Raum)\s+([A-ZÄÖÜ][a-zäöüß\-]+)', re.IGNORECASE),
]

JOB_SEARCH_PATTERNS = [
    re.compile(r'(?:suche|bewerbe\s+mich).*?(?:als|für\s+die\s+Stelle\s+als)\s+([^\.\n,]{3,60})', re.IGNORECASE),
    re.compile(r'(?:examinierte[r]?|ausgebildete[r]?|gelernte[r]?)\s+([^\.\n,]{3,50})', re.IGNORECASE),
    re.compile(r'(?:Berufsbezeichnung|Beruf|Position)[:\s]+([^\.\n,]{3,50})', re.IGNORECASE),
]

# Job-Keywords als letzter Fallback
JOB_KEYWORDS = [
    "Kinderkrankenpfleger", "Kinderkrankenpflegerin", "Kinderkrankenschwester",
    "OTA", "Operationstechnische Assistenz",
    "ATA", "Anästhesietechnische Assistenz",
    "Heilerziehungspfleger", "Heilerziehungspflegerin",
    "Fachkrankenpfleger", "Fachkrankenpflegerin",
    "Pflegefachkraft", "Pflegefachmann", "Pflegefachfrau",
    "Gesundheits- und Krankenpfleger", "Gesundheits- und Krankenpflegerin",
    "Krankenpfleger", "Krankenschwester",
    "Altenpfleger", "Altenpflegerin",
    "Pflegehelfer", "Pflegehilfskraft", "Pflegeassistent",
    "Medizinische Fachangestellte", "Medizinischer Fachangestellter",
    "Physiotherapeut", "Ergotherapeut", "Logopäde",
    "Erzieher", "Sozialpädagoge",
]


class CVParser:
    def parse(self, pdf_path: str) -> dict:
        text = self._extract_text(pdf_path)
        is_medwing = self._is_medwing_profile(text)

        if is_medwing:
            return self._parse_medwing(text)
        else:
            return self._parse_generic(text)

    def _extract_text(self, pdf_path: str) -> str:
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        return text

    def _is_medwing_profile(self, text: str) -> bool:
        indicators = ["Qualifikation", "Einsatzort", "Gewünschter Arbeitsort",
                      "Beschleunigter Bewerbungsprozess"]
        hits = sum(1 for ind in indicators if ind.lower() in text.lower())
        return hits >= 2

    # ── MEDWING Kurzprofil Parser ─────────────────────────────────────────────
    def _parse_medwing(self, text: str) -> dict:
        def extract(key):
            pat = MEDWING_FIELDS.get(key)
            if not pat:
                return ""
            m = pat.search(text)
            if m:
                return " ".join(m.group(1).split()).strip().rstrip(".,")
            return ""

        # Qualifikation: Spezialbehandlung wegen PDF-Spalten
        # "Qualifikation Exam. Gesundheits- und Gewünschte Stelle Festanstellung...\nKrankenpfleger/in"
        # → Zeile 1 bis "Gewünschte Stelle" + Zeile 2 (Fortsetzung) zusammensetzen
        qualifikation = ""
        m_q = re.search(
            r'Qualifikation\s+(.+?)(?=\nVerfügbar|\nEinsatzort|\nGewünschte\s+Schicht|\nFachabteil|\nPersönliche|\n\n)',
            text, re.I | re.DOTALL
        )
        if m_q:
            raw_q = m_q.group(1)
            lines_q = raw_q.split('\n')
            parts_q = []
            for line in lines_q:
                # Rechte Spalte abschneiden (beginnt mit bekanntem Feld-Label)
                line = re.sub(
                    r'\s+(?:Gewünschte\s+Stelle|Gewünschte\s+Schichten|Gewünschter\s+Arbeitsort)\s+.*',
                    '', line, flags=re.I
                ).strip()
                if line:
                    parts_q.append(line)
            qualifikation = " ".join(parts_q).strip().rstrip(".,")

        einsatzort_raw  = extract("einsatzort")
        arbeitsort_typ_raw  = extract("arbeitsort_typ")

        # MEDWING-Feld "Fachabteilungen" ist oft leer oder PDF-Spalten-geschädigt
        # → wenn leer, scanne den ganzen CV-Text nach bekannten Fächern
        fach_raw = extract("fachabteilungen")
        if not fach_raw or len(fach_raw) < 3:
            fach_raw = scan_fachabteilungen(text)
        else:
            # Ergänze mit Keyword-Scan (kombiniere beides)
            scanned = scan_fachabteilungen(text)
            if scanned:
                fach_raw = f"{fach_raw}, {scanned}"

        # PDF-Spalten: Arbeitsort und Einsatzort können vermischt sein
        # Kombiniere beide Rohwerte und normalisiere direkt
        combined_facility_text = f"{arbeitsort_typ_raw} {einsatzort_raw}"
        arbeitsort_typ = combined_facility_text

        # Einsatzort: Einrichtungstyp-Begriffe und rechte Spalte entfernen
        einsatzort = einsatzort_raw
        fachabteilungen = fach_raw
        verfuegbar      = extract("verfuegbar_ab")
        telefon         = extract("telefon")
        email           = extract("email")
        wohnort         = extract("wohnort")

        # PDF-Spalten-Artefakt: Einsatzort kann Text aus Nachbarspalte enthalten
        if einsatzort:
            # "Gewünschter Arbeitsort ..." abschneiden (rechte Spalte reingerutscht)
            einsatzort = re.sub(
                r'\s*Gewünschter?\s+Arbeitsort.*$', '', einsatzort, flags=re.IGNORECASE
            ).strip()
            # Einrichtungstyp-Begriffe entfernen
            einsatzort = re.sub(
                r'\s+(?:Intensivpflegedienst|Ambulanter?\s+Pflegedienst|Krankenhaus|'
                r'Klinik|Psychiatrie|Rehabilitation|Altenpflege|Pflegeheim|'
                r'Stationary\s+Care)\s*',
                '', einsatzort, flags=re.IGNORECASE
            ).strip().rstrip(',')
            # Rechte-Spalte-Müll entfernen (z.B. "Hamburg Monat" → "Hamburg")
            # Einsatzort ist eine Stadt — Zeitangaben und andere Artefakte entfernen
            einsatzort = re.sub(
                r'\s+(?:Monat|Woche|Tag|sofort|ab\s+sofort|Verfügbar|verfügbar|'
                r'Vollzeit|Teilzeit|Festanstellung|Befristet|Unbefristet|'
                r'Frühschicht|Spätschicht|Nachtschicht|Schicht)\b.*$',
                '', einsatzort, flags=re.IGNORECASE
            ).strip()

        # Ort aus Wohnort ableiten
        location = einsatzort
        if not location and wohnort:
            m = re.search(r'\d{5},?\s+([A-ZÄÖÜ][a-zäöüß\-]+)', wohnort)
            if m:
                location = m.group(1)
            else:
                # Letztes kommagetrennte Segment vor "Deutschland"
                parts = re.split(r',', re.sub(r',?\s*Deutschland', '', wohnort))
                city = parts[-1].strip() if parts else ""
                if re.match(r'^[A-ZÄÖÜ]', city):
                    location = city

        return {
            "is_medwing":      True,
            "name":            self._find_name(text),
            "job_title":       normalize_job_title(qualifikation),
            "location":        location,
            "facility_type":   normalize_facility(arbeitsort_typ),
            "fachabteilungen": best_fachabteilung(fachabteilungen),
            "stelle_typ":      extract("gewuenschte_stelle"),
            "schichten":       extract("gewuenschte_schichten"),
            "verfuegbar_ab":   verfuegbar,
            "contact_phone":   telefon,
            "contact_email":   email,
            "wohnort":         wohnort,
            "raw_text":        text,
            # Fallback-Titel für erweiterte Suche
            "job_title_fallback": TITLE_FALLBACK.get(normalize_job_title(qualifikation), ""),
        }

    # ── Normaler Lebenslauf Parser (Fallback) ─────────────────────────────────
    def _parse_generic(self, text: str) -> dict:
        raw_title = self._find_job_title(text)
        raw_facility = self._find_facility_type(text)
        raw_dept = self._find_fachabteilung(text)

        norm_title = normalize_job_title(raw_title)

        return {
            "is_medwing":      False,
            "name":            self._find_name(text),
            "job_title":       norm_title,
            "location":        self._find_location(text),
            "facility_type":   normalize_facility(raw_facility),
            "fachabteilungen": best_fachabteilung(raw_dept),
            "stelle_typ":      "",
            "schichten":       "",
            "verfuegbar_ab":   "",
            "contact_phone":   "",
            "contact_email":   "",
            "wohnort":         "",
            "raw_text":        text,
            "job_title_fallback": TITLE_FALLBACK.get(norm_title, ""),
        }

    def _find_job_title(self, text: str) -> str:
        # Versuche direkte Muster (Bewerbungsformulierungen)
        for pat in JOB_SEARCH_PATTERNS:
            m = pat.search(text)
            if m:
                c = m.group(1).strip().rstrip(".")
                if len(c) > 3:
                    return c
        # Keyword-Suche (spezifischste zuerst)
        text_lower = text.lower()
        found = [kw for kw in JOB_KEYWORDS if kw.lower() in text_lower]
        return max(found, key=len) if found else ""

    def _find_location(self, text: str) -> str:
        for pat in LOCATION_PATTERNS:
            m = pat.search(text)
            if m:
                city = m.group(1).strip().rstrip(",.")
                if len(city) > 2:
                    return city
        return ""

    def _find_name(self, text: str) -> str:
        """Extrahiert den Kandidatennamen aus dem CV-Text.
        MEDWING: 'Persönliche Daten ... Name: Vorname Nachname'
        Generisch: Name steht oft in den ersten 3-5 Zeilen oder nach Labels."""
        # 1. MEDWING: "Persönliche Daten" Block → "Name:" / nach "Persönliche Informationen"
        m = re.search(
            r'(?:Name|Kandidat(?:in)?|Bewerber(?:in)?)[:\s]+([A-ZÄÖÜ][a-zäöüß]+(?:\s+(?:von|van|de|der|zu|zur))?'
            r'\s+[A-ZÄÖÜ][a-zäöüß\-]+)',
            text,
        )
        if m:
            return m.group(1).strip()

        # 2. MEDWING: "Persönliche Daten" → nächste Zeile(n) mit Namen
        m2 = re.search(
            r'Persönliche\s+(?:Daten|Informationen)\s*\n([^\n]+)',
            text, re.IGNORECASE,
        )
        if m2:
            candidate = m2.group(1).strip()
            # Name ist typischerweise 2-4 Wörter, alle mit Großbuchstabe
            words = candidate.split()
            name_parts = []
            for w in words:
                if re.match(r'^[A-ZÄÖÜ]', w) or w.lower() in (
                    "von", "van", "de", "der", "du", "zu", "zur", "le", "la"
                ):
                    name_parts.append(w)
                else:
                    break
            if 2 <= len(name_parts) <= 5:
                return " ".join(name_parts)

        # 3. Generisch: erste Zeile(n) — Name ist oft ganz oben
        lines = [l.strip() for l in text.split("\n")[:8] if l.strip()]
        for line in lines:
            # Ignoriere typische Header-Zeilen (Daten, Adressen, E-Mails, Telefon)
            if re.search(r'@|http|www|\d{5}|^\d|tel|fon|fax|straße|str\.|plz|lebenslauf|'
                         r'bewerbung|curriculum|vita|profil|seite|page',
                         line, re.IGNORECASE):
                continue
            # 2-4 Wörter, hauptsächlich Großbuchstabe → wahrscheinlich ein Name
            words = line.split()
            if 2 <= len(words) <= 5:
                name_parts = []
                for w in words:
                    if re.match(r'^[A-ZÄÖÜ]', w) or w.lower() in (
                        "von", "van", "de", "der", "du", "zu", "zur", "le", "la"
                    ):
                        name_parts.append(w)
                    else:
                        break
                if len(name_parts) >= 2 and len(name_parts) == len(words):
                    return " ".join(name_parts)
        return ""

    def _find_facility_type(self, text: str) -> str:
        """Sucht nach Einrichtungsart im CV-Text."""
        # Schaue besonders in Berufserfahrungs-Abschnitt
        exp_match = re.search(
            r'(?:Berufserfahrung|Erfahrung|Tätigkeit)[^\n]*\n(.{0,800})',
            text, re.IGNORECASE | re.DOTALL
        )
        search_text = exp_match.group(1) if exp_match else text
        return search_text[:500]  # Rohtext, normalize_facility übernimmt Normalisierung

    def _find_fachabteilung(self, text: str) -> str:
        """Extrahiert Fachabteilung(en) aus beliebigem CV-Text.
        Strategie (mehrere Signale kombinieren):
          1. Label-basiert: "Abteilung: …", "Einsatzbereich: …", "Station auf …"
          2. Keyword-Scan: breite Liste aller bekannten Fächer im VOLLEN Text
        Gibt kommagetrennte Liste aller Treffer zurück (dedupliziert)."""
        if not text:
            return ""
        hits: list = []
        seen = set()

        def _add(s: str):
            s = s.strip(" -•·.,;:")[:50]
            if not s or len(s) < 3:
                return
            sl = s.lower()
            if sl in seen or sl in _SKIP_DEPTS:
                return
            seen.add(sl)
            hits.append(s)

        # 1. Label-basierte Muster (auch mehrzeilig)
        label_patterns = [
            re.compile(
                r'(?:Abteilung|Fachabteilung|Fachbereich|Station|Bereich|'
                r'Einsatzbereich|Einsatzgebiet|Tätigkeitsbereich|Fachgebiet)[:\s\-–]+'
                r'([^\n\r,;]{3,80})',
                re.IGNORECASE,
            ),
            re.compile(
                r'(?:Station\s+auf|tätig\s+(?:in|als|im)|Einsatz\s+(?:in|im)|'
                r'im\s+Bereich|in\s+der\s+Abteilung)\s+([A-ZÄÖÜ][^\n\r,;.]{3,60})',
                re.IGNORECASE,
            ),
        ]
        for pat in label_patterns:
            for m in pat.finditer(text):
                _add(m.group(1))
                if len(hits) >= 5:
                    break

        # 2. Keyword-Scan über den VOLLEN Text
        scan = scan_fachabteilungen(text)
        if scan:
            for part in re.split(r',\s*', scan):
                _add(part)
                if len(hits) >= 5:
                    break

        return ", ".join(hits[:5])
