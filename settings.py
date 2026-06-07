"""Zentrale Laufzeit-Konfiguration: Zugangsmodus und Kosten-/Längenlimits.

Werte kommen aus der ``einstellungen``-Tabelle (im Admin pflegbar) mit Fallback
auf Umgebungsvariablen bzw. Code-Defaults. So lassen sich Schwellen und der
Zugangsmodus zur Laufzeit ändern, ohne Neustart oder Deploy.
"""
import os

import db

# Defaults greifen, wenn weder DB-Eintrag noch Env gesetzt sind.
_DEFAULTS = {
    "max_turns": int(os.environ.get("MAX_TURNS_PER_SESSION", "40")),
    "soft_turns": int(os.environ.get("SOFT_TURNS", "30")),
    "max_tokens": int(os.environ.get("MAX_TOKENS_PER_SESSION", "150000")),
    "soft_tokens": int(os.environ.get("SOFT_TOKENS", "110000")),
}

ZUGANGSMODI = ("oeffentlich", "einladung")
KI_ANBIETER = ("claude", "openai")

# Produktions-Fallback, falls PUBLIC_BASE_URL nicht gesetzt ist. So enthält ein
# Einladungslink (z. B. in der Mail) nie versehentlich localhost oder eine leere Basis.
_BASIS_URL_FALLBACK = "https://offerte.ralfwbalz.ch"


def zugangsmodus() -> str:
    """'oeffentlich' (Default, wie bisher) oder 'einladung' (nur per Token-Link)."""
    wert = db.get_setting("zugangsmodus", "oeffentlich")
    return wert if wert in ZUGANGSMODI else "oeffentlich"


def basis_url() -> str:
    """Öffentliche Basis-URL ohne Trailing-Slash (für Einladungs- und Freigabe-Links).

    Liest ``PUBLIC_BASE_URL``; ist sie leer, greift der Produktions-Fallback, damit
    Links nach aussen nie auf localhost oder eine leere Basis zeigen.
    """
    return (os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
            or _BASIS_URL_FALLBACK)


def ki_anbieter() -> str:
    """Gesprächs-KI: 'claude' (Anthropic, Default) oder 'openai' (ChatGPT)."""
    wert = db.get_setting("ki_anbieter", os.environ.get("KI_ANBIETER", "claude"))
    return wert if wert in KI_ANBIETER else "claude"


def _int_setting(key: str) -> int:
    wert = db.get_setting(key, "")
    if wert:
        try:
            return int(wert)
        except ValueError:
            pass
    return _DEFAULTS[key]


def max_turns() -> int:
    return _int_setting("max_turns")


def soft_turns() -> int:
    return _int_setting("soft_turns")


def max_tokens() -> int:
    return _int_setting("max_tokens")


def soft_tokens() -> int:
    return _int_setting("soft_tokens")
