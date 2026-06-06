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


def zugangsmodus() -> str:
    """'oeffentlich' (Default, wie bisher) oder 'einladung' (nur per Token-Link)."""
    wert = db.get_setting("zugangsmodus", "oeffentlich")
    return wert if wert in ZUGANGSMODI else "oeffentlich"


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
