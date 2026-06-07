"""SQLite-Persistenz für Sessions, Nachrichten und Offerten.

Bewusst schlank gehalten (eine Datei, eine Verbindung pro Aufruf) – passt zum
Low-Traffic-Profil und vermeidet einen separaten DB-Container.
"""
import os
import sqlite3
import time
from datetime import datetime

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "offerte.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                created_at  TEXT NOT NULL,
                ip          TEXT,
                status      TEXT NOT NULL DEFAULT 'aktiv'
            );
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL,
                data_json       TEXT NOT NULL,
                pdf_path        TEXT,
                freigabe_token  TEXT UNIQUE NOT NULL,
                released_at     TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS einstellungen (
                schluessel  TEXT PRIMARY KEY,
                wert        TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS zugangslinks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                token           TEXT UNIQUE NOT NULL,
                notiz           TEXT,
                created_at      TEXT NOT NULL,
                gueltig_bis     TEXT,
                deaktiviert     INTEGER NOT NULL DEFAULT 0,
                letzte_nutzung  TEXT,
                empfaenger_email TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """
        )
        # Migrationen für bestehende DBs: Token-Verbrauch je Session nachrüsten.
        for spalte in ("tokens_in", "tokens_out"):
            try:
                c.execute(f"ALTER TABLE sessions ADD COLUMN {spalte} INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits
        # Migration: Empfänger-E-Mail je Einladungslink nachrüsten.
        try:
            c.execute("ALTER TABLE zugangslinks ADD COLUMN empfaenger_email TEXT")
        except sqlite3.OperationalError:
            pass  # Spalte existiert bereits


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def session_exists(session_id: str) -> bool:
    with _conn() as c:
        row = c.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None


def create_session(session_id: str, ip: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, ip) VALUES (?, ?, ?)",
            (session_id, _now(), ip),
        )


def count_recent_sessions_for_ip(ip: str, within_seconds: int) -> int:
    cutoff = datetime.fromtimestamp(time.time() - within_seconds).isoformat(timespec="seconds")
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE ip = ? AND created_at >= ?",
            (ip, cutoff),
        ).fetchone()
        return row["n"]


def add_message(session_id: str, role: str, content: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, _now()),
        )


def get_history(session_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


def count_messages(session_id: str, role: str = "user") -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ? AND role = ?",
            (session_id, role),
        ).fetchone()
        return row["n"]


def save_offer(session_id: str, data_json: str, pdf_path: str, token: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO offers (session_id, data_json, pdf_path, freigabe_token, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, data_json, pdf_path, token, _now()),
        )


def get_offer_by_token(token: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM offers WHERE freigabe_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None


def mark_released(token: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE offers SET released_at = ? WHERE freigabe_token = ?",
            (_now(), token),
        )


# ----------------------------------------------------------- Einstellungen ----

def get_setting(key: str, default: str = "") -> str:
    with _conn() as c:
        row = c.execute(
            "SELECT wert FROM einstellungen WHERE schluessel = ?", (key,)
        ).fetchone()
        return row["wert"] if row else default


def set_setting(key: str, wert: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO einstellungen (schluessel, wert) VALUES (?, ?) "
            "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
            (key, wert),
        )


# ------------------------------------------------------------ Zugangslinks ----

def create_zugangslink(token: str, notiz: str, gueltig_bis: str | None,
                       empfaenger_email: str = "") -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO zugangslinks (token, notiz, created_at, gueltig_bis, empfaenger_email) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, notiz, _now(), gueltig_bis, empfaenger_email),
        )


def get_zugangslink_by_id(link_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM zugangslinks WHERE id = ?", (link_id,)
        ).fetchone()
        return dict(row) if row else None


def list_zugangslinks() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM zugangslinks ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_zugangslink(token: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM zugangslinks WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None


def set_zugangslink_deaktiviert(link_id: int, deaktiviert: bool) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE zugangslinks SET deaktiviert = ? WHERE id = ?",
            (1 if deaktiviert else 0, link_id),
        )


def touch_zugangslink(token: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE zugangslinks SET letzte_nutzung = ? WHERE token = ?",
            (_now(), token),
        )


# ----------------------------------------------------------- Token-Verbrauch --

def add_session_usage(session_id: str, tokens_in: int, tokens_out: int) -> None:
    """Schreibt den Token-Verbrauch einer Runde kumulativ in die Session."""
    with _conn() as c:
        c.execute(
            "UPDATE sessions SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ? "
            "WHERE id = ?",
            (tokens_in, tokens_out, session_id),
        )


def get_session_usage(session_id: str) -> tuple[int, int]:
    with _conn() as c:
        row = c.execute(
            "SELECT tokens_in, tokens_out FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return (0, 0)
        return (row["tokens_in"] or 0, row["tokens_out"] or 0)


# -------------------------------------------------------------- Admin-Listen --

def list_sessions() -> list[dict]:
    """Sessions mit Turn-Zahlen, Token-Verbrauch und Offerten-Flag (für den Admin)."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT s.id, s.created_at, s.ip, s.status, s.tokens_in, s.tokens_out,
                   (SELECT COUNT(*) FROM messages m
                      WHERE m.session_id = s.id AND m.role = 'user') AS user_turns,
                   (SELECT COUNT(*) FROM messages m
                      WHERE m.session_id = s.id) AS msg_count,
                   (SELECT COUNT(*) FROM offers o
                      WHERE o.session_id = s.id) AS hat_offerte
            FROM sessions s
            ORDER BY s.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_offers() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, session_id, data_json, pdf_path, freigabe_token, "
            "released_at, created_at FROM offers ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_offer_by_session(session_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM offers WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
