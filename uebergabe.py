"""Übergabe aus dem Aufwandsrechner (aufwandsrechner.ralfwbalz.ch).

Der Rechner schickt Kontakt und Schätzung eines Besuchers **von Server zu Server** an
``POST /uebergabe`` und leitet den Browser danach mit einem Token hierher. So steht in
keiner Adresse und in keinem Zugriffsprotokoll mehr als ein Zufallswert.

Echt ist ein Aufruf nur mit gültiger Signatur. Der Schlüssel wird aus demselben
``ZUGANG_SCHLUESSEL`` abgeleitet, den beide Apps schon für die gemeinsame Anmeldung haben –
mit eigenem Präfix, damit eine Signatur hier nie als Anmelde-Cookie taugt und umgekehrt.

Das Gegenstück liegt in ``~/aufwandsrechner/uebergabe.py``; die Projekte teilen keinen
Code. Ändert sich das Verfahren, muss es auf beiden Seiten nachgezogen werden.
"""
import base64
import binascii
import hashlib
import hmac
import os
import re
import time

KOPF_ZEIT = "x-uebergabe-zeit"
KOPF_SIGNATUR = "x-uebergabe-signatur"
# Älter (oder weiter in der Zukunft) als das gilt ein Aufruf nicht – gegen Wiedereinspielen.
ZEITFENSTER = 60
# So lange lässt sich ein Token im Browser einlösen.
GUELTIG_MINUTEN = 30
ANREDEN = ("männlich", "weiblich", "Herr", "Frau", "Firma")
KONTEXT_HOECHSTENS = 8000
# Das PDF der Schätzung reist als Base64 im Rumpf mit (üblich: 20 bis 30 KB).
PDF_HOECHSTENS = 400_000
RUMPF_HOECHSTENS = 600_000
_NUMMER = re.compile(r"AR-\d{4}-\d{5,}")


def _schluessel() -> bytes | None:
    geheim = os.environ.get("ZUGANG_SCHLUESSEL", "").strip()
    if len(geheim) < 32:
        return None   # ohne brauchbaren Schlüssel gibt es keine Übergabe
    return hashlib.sha256(("uebergabe|" + geheim).encode()).digest()


def signatur_gueltig(zeit: str | None, signatur: str | None, rumpf: bytes) -> bool:
    schluessel = _schluessel()
    if schluessel is None or not zeit or not signatur or not zeit.isdigit():
        return False
    if abs(time.time() - int(zeit)) > ZEITFENSTER:
        return False
    erwartet = hmac.new(schluessel, zeit.encode() + b"." + rumpf, hashlib.sha256).hexdigest()
    return hmac.compare_digest(erwartet, signatur)


def pdf_lesen(roh, nummer) -> tuple[bytes | None, str]:
    """PDF der Schätzung aus dem Rumpf (Base64) samt Dateiname. Fehlt es oder ist es kein
    PDF in vernünftiger Grösse, gibt es einfach keines – die Übergabe gilt trotzdem."""
    if not isinstance(roh, str) or not roh:
        return None, ""
    try:
        inhalt = base64.b64decode(roh, validate=True)
    except (binascii.Error, ValueError):
        return None, ""
    if not inhalt.startswith(b"%PDF") or len(inhalt) > PDF_HOECHSTENS:
        return None, ""
    # Den Dateinamen selbst bilden, nie einen gelieferten übernehmen.
    nummer = nummer if isinstance(nummer, str) and _NUMMER.fullmatch(nummer) else ""
    return inhalt, f"Aufwandsschaetzung_{nummer}.pdf" if nummer else "Aufwandsschaetzung.pdf"
