"""
SMTP E-Mail Verifikation — prüft ob eine (geschätzte) Email-Adresse existiert.
Nutzt SMTP RCPT-TO ohne eine echte E-Mail zu versenden.

Rückgabe:
  'valid'   — Server hat Adresse bestätigt (250 OK)
  'invalid' — Server hat Adresse abgewiesen (550/551/552/553)
  'unknown' — Konnte nicht verifiziert werden (Timeout, Verbindungsfehler, …)
"""
import smtplib
import socket
import re


def verify_email(email: str) -> str:
    """Führt einen SMTP-Handshake durch, um die Existenz einer Email zu prüfen."""
    if not email or "@" not in email:
        return "unknown"
    try:
        domain = email.split("@")[1].lower()
        mx_host = _get_mx(domain)
        if not mx_host:
            return "unknown"
        with smtplib.SMTP(timeout=10) as smtp:
            smtp.connect(mx_host, 25)
            smtp.helo("cvjobmatcher.medwing.com")
            smtp.mail("noreply@medwing.com")
            code, _ = smtp.rcpt(email)
            if code == 250:
                return "valid"
            elif str(code).startswith("5"):
                return "invalid"
            return "unknown"
    except smtplib.SMTPRecipientsRefused:
        return "invalid"
    except smtplib.SMTPConnectError:
        return "unknown"
    except (socket.timeout, ConnectionRefusedError, OSError):
        return "unknown"
    except Exception:
        return "unknown"


def _get_mx(domain: str) -> str:
    """Gibt den bevorzugten MX-Host zurück."""
    # Zuerst dnspython versuchen (präzisere MX-Auflösung)
    try:
        import dns.resolver  # type: ignore
        records = dns.resolver.resolve(domain, "MX")
        best = sorted(records, key=lambda r: r.preference)[0]
        return str(best.exchange).rstrip(".")
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: Domain direkt ansprechen
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo(domain, 25)
        return domain
    except Exception:
        pass
    return ""
