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
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

logger = logging.getLogger("offerte.uebergabe")

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
# Kennung, mit der der Rechner die Rückmeldung «Gespräch hat begonnen» wünscht.
_RUECKRUF = re.compile(r"[A-Za-z0-9_-]{16,64}")
RUECKRUF_ZEITLIMIT = 8


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


def rueckruf_lesen(roh) -> str:
    """Die Kennung aus dem Rumpf – oder '' (dann gibt es keine Rückmeldung)."""
    return roh if isinstance(roh, str) and _RUECKRUF.fullmatch(roh) else ""


def rueckruf_melden(rechner_url: str, kennung: str) -> bool:
    """Meldet dem Aufwandsrechner von Server zu Server, dass das Gespräch zu einer Übergabe
    begonnen hat (``POST /api/uebergabe/gestartet``). Der Rechner zählt daraus seine
    Conversion; diese App weiss nicht wofür und schickt nichts ausser der Kennung.

    Eigener Schlüssel (Präfix ``uebergabe-rueckruf|``), damit eine Übergabe nie als
    Rückmeldung taugt. Wirft nie: scheitert der Aufruf, läuft das Gespräch trotzdem."""
    geheim = os.environ.get("ZUGANG_SCHLUESSEL", "").strip()
    if len(geheim) < 32 or not _RUECKRUF.fullmatch(kennung or ""):
        return False
    schluessel = hashlib.sha256(("uebergabe-rueckruf|" + geheim).encode()).digest()
    rumpf = json.dumps({"rueckruf": kennung}).encode()
    zeit = str(int(time.time()))
    signatur = hmac.new(schluessel, zeit.encode() + b"." + rumpf, hashlib.sha256).hexdigest()
    anfrage = urllib.request.Request(
        f"{rechner_url}/api/uebergabe/gestartet", data=rumpf, method="POST",
        headers={"Content-Type": "application/json", KOPF_ZEIT: zeit, KOPF_SIGNATUR: signatur,
                 "User-Agent": "offerte"})
    try:
        with urllib.request.urlopen(anfrage, timeout=RUECKRUF_ZEITLIMIT) as antwort:
            return 200 <= antwort.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as fehler:
        logger.error("Rückmeldung an den Aufwandsrechner fehlgeschlagen: %s", fehler)
        return False
