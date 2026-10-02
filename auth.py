"""Admin-Zugang über die gemeinsame Anmeldung von ralfwbalz.ch.

Angemeldet wird nicht mehr hier, sondern auf https://ralfwbalz.ch/anmelden – ein
Zugang für die Redaktion und die Statistik dort und für diesen Admin-Bereich. Die
Hauptseite setzt nach geprüftem Benutzer und Passwort das Cookie ``rwb_zugang`` mit
``Domain=ralfwbalz.ch``; diese App prüft nur noch dessen Signatur, mit demselben
``ZUGANG_SCHLUESSEL`` aus der eigenen ``.env``. Benutzer und Passwort kennt sie nicht.

Die Prüfung ist eine bewusste Kopie von ``_token_gueltig`` aus ``ralfwbalz/main.py``:
die beiden Projekte teilen weiterhin keinen Code. Ändert sich dort das Format des
Cookies oder die Schlüsselableitung, muss es hier nachgezogen werden.

Geschützte Routen hängen von ``require_admin`` ab. Ist niemand angemeldet, wird
``NichtAngemeldet`` ausgelöst; der in main.py registrierte Handler leitet zur
gemeinsamen Anmeldung um.
"""
import hashlib
import hmac
import logging
import os
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

ZUGANG_COOKIE = "rwb_zugang"


class NichtAngemeldet(Exception):
    """Signalisiert, dass die Anmeldung fehlt (führt zum Redirect)."""


def _schluessel() -> bytes | None:
    """HMAC-Schlüssel aus ZUGANG_SCHLUESSEL, None wenn nicht (brauchbar) gesetzt.

    Erst beim Aufruf gelesen: main.py importiert dieses Modul vor ``load_dotenv()``.
    Ohne Schlüssel ist niemand angemeldet – ein leerer Wert ergäbe sonst eine bekannte
    Konstante, mit der sich jeder ein gültiges Cookie selbst ausstellen könnte.
    """
    geheim = os.environ.get("ZUGANG_SCHLUESSEL", "").strip()
    if len(geheim) < 32:
        return None
    return hashlib.sha256(("zugang|" + geheim).encode()).digest()


def _token_gueltig(schluessel: bytes, token: str | None) -> bool:
    """Format ``{ablauf}.{hmac-sha256-hex über ablauf}``, Ablauf als Unix-Zeit."""
    if not token or "." not in token:
        return False
    ablauf_roh = token.partition(".")[0]
    if not ablauf_roh.isdigit():
        return False
    signatur = hmac.new(schluessel, ablauf_roh.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"{ablauf_roh}.{signatur}", token):
        return False
    return int(ablauf_roh) > time.time()


def ist_angemeldet(request: Request) -> bool:
    schluessel = _schluessel()
    return schluessel is not None and _token_gueltig(
        schluessel, request.cookies.get(ZUGANG_COOKIE))


def require_admin(request: Request) -> None:
    """Dependency für geschützte Admin-Routen.

    Alles ausser GET muss zusätzlich von dieser Seite selbst stammen. Das Cookie gilt
    für die ganze Domain und ist ``SameSite=Lax``; ralfwbalz.ch und jede andere
    Subdomain sind für den Browser dieselbe Site, ein Formular von dort brächte es mit.
    """
    if not ist_angemeldet(request):
        raise NichtAngemeldet()
    if request.method not in ("GET", "HEAD"):
        herkunft = request.headers.get("origin") or request.headers.get("referer") or ""
        if urlparse(herkunft).netloc != request.headers.get("host", ""):
            logger.warning(f"Admin: Anfrage fremder Herkunft abgewiesen ({herkunft[:100]})")
            raise HTTPException(status_code=403)
