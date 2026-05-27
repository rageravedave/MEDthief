import re


EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
PHONE_RE = re.compile(r'(?:\+49|0)\s*[0-9\s\(\)\-\/]{7,20}')

# Emails die definitiv kein Kontakt sind
_BAD_EMAIL_PARTS = frozenset([
    'example', 'muster', 'test@', 'noreply', 'no-reply', 'bounce',
    'unsubscribe', 'newsletter', 'marketing', 'tracking',
    'whatsapp', 'facebook', 'instagram', 'twitter', 'tiktok',
    'youtube', 'linkedin', 'xing', 'pinterest', 'telegram',
    'wix.com', 'sentry.io', 'cloudflare', 'googletagmanager',
    'google-analytics', 'hotjar', 'mailchimp', 'hubspot', 'zendesk',
])

# Name-Capture: case-sensitive (Uppercase start), uses literal space (not \s)
# Keywords use inline (?i:...) for case-insensitivity
_NAME = r'[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?(?: (?:von|van|de))? [A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?(?: [A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)?'

NAME_PATTERNS = [
    # "Ansprechpartner(in): Petra Müller"
    re.compile(
        r'(?i:Ansprechpartner(?:in)?|Kontaktperson|Ihre Ansprechpartnerin?'
        r'|Ihr Ansprechpartner)[:\s]+(' + _NAME + r')',
    ),
    # "Frau Müller" / "Herr Dr. Schmidt"
    re.compile(
        r'(?i:Frau|Herr) (?:(?i:Dr|Prof|Dipl)\. )?(' + _NAME + r')',
    ),
    # "Kontakt: Petra Müller"
    re.compile(
        r'(?i:Kontakt)[:\s]+(' + _NAME + r')',
    ),
    # "Personalleitung: Petra Müller" / "Pflegedienstleitung: ..."
    re.compile(
        r'(?i:Personal(?:leitung|abteilung|referent(?:in)?)|Pflegedienstleitung|PDL'
        r'|Recruiting|Bewerbungsmanagement)[:\s]+(' + _NAME + r')',
    ),
    # "Name in Klammern: (Petra Müller)"
    re.compile(
        r'\(([A-ZÄÖÜ][a-zäöüß]+ [A-ZÄÖÜ][a-zäöüß]+)\)',
    ),
]

# Impressum-spezifische Muster (gesetzlich vorgeschrieben in DE)
IMPRESSUM_PATTERNS = [
    re.compile(
        r'(?i:Geschäftsführer(?:in)?|Geschäftsleitung|Inhaber(?:in)?'
        r'|Verantwortlich(?:er)?(?:\s+(?:i\.?\s*S\.?\s*d\.?\s*§|gem(?:äß)?|nach))?[^:]{0,30}?)'
        r'[:\s]+(' + _NAME + r')',
    ),
]


def extract(text: str) -> dict:
    """Extract contact info (name, email, phone) from any text."""
    emails = EMAIL_RE.findall(text)
    emails = [e for e in emails if not any(x in e.lower() for x in _BAD_EMAIL_PARTS)]

    phones = PHONE_RE.findall(text)
    phones = [p.strip() for p in phones if len(re.sub(r'\D', '', p)) >= 7]

    name = ''
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            candidate = m.group(1).strip()
            words = candidate.split()
            if (2 <= len(words) <= 4
                    and not any(c in candidate for c in '\n\t')
                    and all(w[0].isupper() for w in words if w not in ('von', 'van', 'de'))):
                name = candidate
                break

    return {
        'contact_name':  name,
        'contact_email': emails[0] if emails else '',
        'contact_phone': phones[0] if phones else '',
        'all_emails':    emails[:5],
        'all_phones':    phones[:3],
    }


def extract_impressum(text: str) -> dict:
    """Extract contact info specifically from Impressum pages."""
    result = extract(text)

    if not result['contact_name']:
        for pattern in IMPRESSUM_PATTERNS:
            m = pattern.search(text)
            if m:
                name = m.group(1).strip()
                words = name.split()
                if 2 <= len(words) <= 4:
                    result['contact_name'] = name
                    break

    return result


def rank_emails(emails: list, company_domain: str = '') -> list:
    """
    Sortiert Emails nach Relevanz für Bewerbungskontakt.
    Beste zuerst: persönliche > bewerbung@ > personal@ > info@ > rest
    """
    if not emails:
        return []

    domain = company_domain.lower().replace('www.', '') if company_domain else ''

    def _score(email: str) -> int:
        e = email.lower()
        local = e.split('@')[0] if '@' in e else ''
        email_domain = e.split('@')[1] if '@' in e else ''

        score = 0
        if domain and domain in email_domain:
            score += 100

        # Persönliche Email (vorname.nachname) = beste
        if '.' in local and local not in ('no.reply',):
            parts = local.split('.')
            if len(parts) == 2 and all(len(p) > 1 for p in parts):
                score += 50

        for prefix, pts in [
            ('bewerbung', 40), ('karriere', 38), ('jobs', 35),
            ('personal', 33), ('hr', 30), ('recruiting', 28),
            ('stellenangebote', 25),
        ]:
            if local.startswith(prefix):
                score += pts
                break

        if local == 'info':
            score += 10
        if local in ('support', 'service', 'office'):
            score += 5

        return score

    return sorted(emails, key=_score, reverse=True)
