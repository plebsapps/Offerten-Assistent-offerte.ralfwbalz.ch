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
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """
        )


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
