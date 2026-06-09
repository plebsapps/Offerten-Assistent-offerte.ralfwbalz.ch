"""Admin-Authentifizierung.

Zugangsdaten kommen aus der Umgebung (ADMIN_USER + ADMIN_PASSWORD_HASH, bcrypt),
nie aus dem Code. Nach erfolgreichem Login wird die Session über das von
SessionMiddleware signierte Cookie markiert.

Geschützte Routen hängen von `require_admin` ab. Ist niemand angemeldet, wird
`NichtAngemeldet` ausgelöst; der in main.py registrierte Handler leitet zum
Login um. Übernommen vom Schwesterprojekt bewerbung-ralfwbalz.
"""
import os
import secrets

import bcrypt
from fastapi import Request

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")


class NichtAngemeldet(Exception):
    """Signalisiert, dass ein Admin-Login fehlt (führt zum Redirect)."""


def pruefe_login(user: str, passwort: str) -> bool:
    """Benutzernamen konstantzeit-vergleichen und Passwort per bcrypt prüfen."""
    user_ok = secrets.compare_digest(user or "", ADMIN_USER)
    pw_ok = False
    if ADMIN_PASSWORD_HASH:
        try:
            pw_ok = bcrypt.checkpw((passwort or "").encode("utf-8"),
                                   ADMIN_PASSWORD_HASH.encode("utf-8"))
        except ValueError:
            pw_ok = False  # ungültiger Hash in der Konfiguration
    return user_ok and pw_ok


def ist_angemeldet(request: Request) -> bool:
    return bool(request.session.get("admin"))


def anmelden(request: Request) -> None:
    request.session["admin"] = True


def abmelden(request: Request) -> None:
    request.session.pop("admin", None)


def require_admin(request: Request) -> None:
    """Dependency für geschützte Admin-Routen."""
    if not ist_angemeldet(request):
        raise NichtAngemeldet()
