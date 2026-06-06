# Offerten-Assistent: Admin-Bereich, Zugangssteuerung, Abschluss-Sprung & Kostenbremse

## Context

Der Offerten-Assistent (`/home/ralf/offerte`, FastAPI + Claude + SQLite) ist heute komplett
**öffentlich und anonym**: jeder Besucher kann beliebig lange chatten. Geschützt wird nur per
IP-Rate-Limit (`MAX_SESSIONS_PER_IP`) und hartem Turn-Cap (`MAX_TURNS_PER_SESSION=40`,
`main.py:90`). Es gibt **keinen Admin-Bereich, keine Auth, kein Token-/Kosten-Tracking**.

Drei Bedürfnisse von Ralf:
1. **Zugangskontrolle + Admin-Bereich** (Vorbild: Schwesterprojekt `/home/ralf/bewerbung-ralfwbalz`,
   das genau dieses Muster hat – `auth.py` mit bcrypt + `SessionMiddleware`, Admin-Routen,
   Token-Tabelle für Einladungs-Zugang). Ralf soll **bestimmen können, wer Zugriff hat**.
2. **Abschluss-Sprung zur Homepage**: ist die Offerte fertig besprochen, soll der Nutzer
   (automatisch + per Button) auf ralfwbalz.ch geleitet werden.
3. **Kostenbremse mit sanftem Auslaufen**: damit niemand „stundenlang plant" und das
   API-Guthaben aufbraucht, soll der Agent **früh genug** zum Abschluss überleiten
   („wir kommen zum Ende; offene Punkte klärt Ralf persönlich") statt hart abzubrechen.

### Entscheidungen (mit Ralf abgestimmt)
- **Zugang = umschaltbar (Toggle)** im Admin: `oeffentlich` (wie heute) ODER `einladung`
  (nur per Token-Link). Öffentliche Leads bleiben möglich, lassen sich aber jederzeit dichtmachen.
- **Abschluss = Auto-Weiterleitung + Button** zu ralfwbalz.ch (Countdown ~8 s, Button als Fallback).
- **Kostenlimit = kombiniert**: sowohl Turns als auch echter Token-Verbrauch; was zuerst die
  Soft-Schwelle erreicht, löst die Vorwarnung aus, die Hard-Schwelle den Abschluss.
- **Admin-Umfang = alles vier**: Einladungslinks verwalten, Gespräche+Transkripte ansehen,
  Offerten ansehen/PDF/Freigabe, Kosten+Limits sehen/einstellen.

> Hinweis: `agent.py` ist aktuell uncommitted geändert (`MODEL = "claude-sonnet-4-6"`); Modellwahl
> ist **nicht** Teil dieser Aufgabe – bestehenden Wert beibehalten.

---

## Architektur-Überblick

Das Bewerbungs-Projekt liefert die Blaupause (aber **PostgreSQL → hier SQLite portieren**;
offerte hält bewusst eine schlanke SQLite-Datei, `db.py:1-20`).

Neue/erweiterte Bausteine:
- `auth.py` (neu, 1:1-Port von `bewerbung-ralfwbalz/auth.py`): bcrypt-Login + Session-Cookie.
- `routes_admin.py` (neu): Admin-Login, Dashboard, Zugang/Links, Gespräche, Offerten, Einstellungen.
- `settings.py` (neu, klein): liest Limit-/Modus-Werte aus der `einstellungen`-Tabelle mit
  Env/Default-Fallback – eine zentrale Stelle für Schwellen + Zugangsmodus.
- `db.py`: neue Tabellen `einstellungen`, `zugangslinks`; Spalten `tokens_in`/`tokens_out` in
  `sessions`; neue Query-Funktionen.
- `main.py`: `SessionMiddleware` + Exception-Handler, Zugangs-Gate vor `/`+`/chat`, Soft/Hard-Limit
  auf Turns **und** Tokens, Admin-Router einbinden, Homepage-URL ans Template geben.
- `agent.py`: `stream_reply(..., wind_down: bool)` – im Wind-down-Fall Abschluss-Instruktion an den
  System-Prompt hängen; Token-`usage` pro Runde aufsummieren und in die Session schreiben.
- `static/js/app.js` + `templates/index.html`: Abschluss-Overlay mit Countdown-Redirect + Button;
  Soft-Limit-Hinweis wird ohnehin als normaler Agententext gestreamt.
- `templates/admin/*` (neu): `layout.html`, `login.html`, `dashboard.html`, `zugang.html`,
  `gespraeche.html`, `gespraech.html`, `offerten.html`, `einstellungen.html` – Styling minimal,
  am bestehenden `static/css/style.css` orientiert (kein Tailwind-CDN nötig).
- `requirements.txt`: `bcrypt`, `itsdangerous` (von `SessionMiddleware` benötigt) ergänzen.
- `.env.example`: `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `SECRET_KEY`, `HOMEPAGE_URL`
  (Default `https://ralfwbalz.ch`) und Soft-/Token-Schwellen dokumentieren.

---

## Umsetzung – Schritte

### 1. Abhängigkeiten & Konfiguration
- `requirements.txt`: `bcrypt` und `itsdangerous` hinzufügen.
- `.env.example` ergänzen: `ADMIN_USER`, `ADMIN_PASSWORD_HASH` (bcrypt-Hash; Erzeugung im Kommentar
  notieren), `SECRET_KEY` (für SessionMiddleware), `HOMEPAGE_URL=https://ralfwbalz.ch`,
  `SOFT_TURNS` (z.B. 30), `MAX_TOKENS_PER_SESSION` + `SOFT_TOKENS` (z.B. 150000/110000).
  Bestehende `MAX_TURNS_PER_SESSION`/`MAX_SESSIONS_PER_IP` bleiben als Default-Fallback.

### 2. Datenbank (`db.py`)
- In `init()` ergänzen (idempotent, `CREATE TABLE IF NOT EXISTS`):
  - `einstellungen (schluessel TEXT PRIMARY KEY, wert TEXT)` – Key-Value für Zugangsmodus + Limits.
  - `zugangslinks (id INTEGER PK AUTOINCREMENT, token TEXT UNIQUE NOT NULL, notiz TEXT,
    created_at TEXT, gueltig_bis TEXT, deaktiviert INTEGER DEFAULT 0, letzte_nutzung TEXT)`.
    **Bewusst mehrfach nutzbar** (anders als Bewerbung-Einmaltoken), da eine offerte-`session_id`
    pro Seitenaufruf neu entsteht – der Kunde muss den Link mehrfach/nach Reload nutzen können.
- Migration für die `sessions`-Spalten `tokens_in`/`tokens_out`: `ALTER TABLE sessions ADD COLUMN ...`
  in `try/except sqlite3.OperationalError` (Spalte existiert bereits) – Standard-SQLite-Migration.
- Neue Funktionen (Stil wie vorhandene `_conn()`-Helfer):
  - Einstellungen: `get_setting(key, default)`, `set_setting(key, wert)`.
  - Links: `create_zugangslink(token, notiz, gueltig_bis)`, `list_zugangslinks()`,
    `get_zugangslink(token)`, `set_zugangslink_deaktiviert(id, bool)`, `touch_zugangslink(token)`.
  - Usage: `add_session_usage(session_id, tin, tout)` (kumulativ), `get_session_usage(session_id)`.
  - Admin-Listen: `list_sessions()` (mit User-/Assistant-Turn-Count + Tokens via JOIN/Subquery),
    `list_offers()`, ggf. `get_offer_by_session(session_id)`. `get_history` für Transkript existiert.

### 3. Auth & Session (`auth.py` + `main.py`)
- `auth.py` aus `bewerbung-ralfwbalz/auth.py` übernehmen (`pruefe_login`, `ist_angemeldet`,
  `anmelden`, `abmelden`, `require_admin`, `NichtAngemeldet`) – unverändert übertragbar.
- `main.py`: `SessionMiddleware` registrieren (secret aus `SECRET_KEY`, `https_only` analog
  Bewerbung an `PUBLIC_BASE_URL` koppeln) und Exception-Handler für `NichtAngemeldet` → Redirect
  `/admin/login`. Admin-Router via `app.include_router(routes_admin.router)`.

### 4. Zugangs-Gate (`settings.py` + `main.py`)
- `settings.py`: `zugangsmodus()` liest `einstellungen['zugangsmodus']` (Default `oeffentlich`);
  Limit-Getter `soft_turns()`, `max_turns()`, `soft_tokens()`, `max_tokens()` mit Env/Default-Fallback.
- `GET /`: bei Modus `einladung` Zugang prüfen – gültiger `?z=<token>` (über `get_zugangslink`,
  nicht deaktiviert, nicht abgelaufen) setzt `request.session['zugang_ok']=True` und `touch_zugangslink`;
  sonst, wenn kein `zugang_ok` im Session-Cookie, eine schlanke „Zugang nur per Einladung"-Seite
  ausliefern. Bei Modus `oeffentlich`: wie heute.
- `POST /chat`, `/chat/stt`, `/chat/tts`: bei Modus `einladung` zusätzlich
  `request.session.get('zugang_ok')` verlangen (sonst 403). Öffentlich: unverändert.

### 5. Kostenbremse: Soft-Wind-down + Hard-Stop
- **Token-Erfassung** (`agent.py`): in der Streaming-Schleife je Runde `final.usage` auslesen und
  summieren; am Ende `db.add_session_usage(session_id, in, out)`. (`anthropic` liefert
  `input_tokens`/`output_tokens` im finalen Message-Objekt.)
- **Schwellen-Check** (`main.py`, vor dem Agentenaufruf):
  - `turns = count_messages(session_id,'user')`, `tin,tout = get_session_usage(...)`.
  - **Hard** erreicht (`turns >= max_turns()` ODER `tin+tout >= max_tokens()`): wie heute
    `limit`-Event mit freundlichem Schlusstext zurückgeben (Backstop).
  - **Soft** erreicht (`turns >= soft_turns()` ODER Tokens `>= soft_tokens()`): `wind_down=True`
    an `agent.stream_reply` übergeben.
- **Wind-down im Agenten** (`agent.py`): bei `wind_down=True` einen Zusatzblock an `SYSTEM_PROMPT`
  hängen, der den Agenten anweist, das Gespräch **jetzt zusammenzufassen, dem Kunden mündlich zu
  sagen, dass man zum Abschluss kommt und offene Punkte Ralf persönlich klärt**, und – sofern
  Kontaktdaten vorliegen – `offerte_erstellen` aufzurufen. So entsteht ein sanftes Auslaufen,
  bevor der Hard-Stop greift. (Stil-Constraints des Prompts: eine Frage, gesprochene Sprache, keine Preise.)

### 6. Abschluss-Sprung zur Homepage (`index.html` + `app.js`)
- `index.html`: ein verstecktes Abschluss-Overlay mit Text, Countdown und Button
  `Zur Homepage von Ralf W. Balz`; Homepage-URL aus dem Template-Kontext (`HOMEPAGE_URL`,
  von `main.py` an `index` übergeben).
- `app.js`: `offer_created` setzt ein Flag `offerCreated=true` (zusätzlich zum bestehenden
  Status-Hinweis). Wenn der Stream mit `done` endet **und** `offerCreated`, im `finally` nach dem
  TTS-Start das Overlay zeigen und einen ~8-s-Countdown starten, der auf `HOMEPAGE_URL` weiterleitet;
  Button leitet sofort weiter. (Keine Weiterleitung, wenn keine Offerte erzeugt wurde.)

### 7. Admin-Bereich (`routes_admin.py` + `templates/admin/*`)
- Routen (alle außer Login via `Depends(require_admin)`):
  - `GET /admin/login`, `POST /admin/login`, `GET /admin/logout` (Port aus Bewerbung).
  - `GET /admin` Dashboard: Kennzahlen (Gespräche heute, offene/erstellte Offerten, Token-Summe).
  - `GET /admin/zugang`: Modus-Toggle (`oeffentlich`/`einladung`, schreibt `set_setting`),
    Liste der Links, Formular „neuen Einladungslink erzeugen" (`secrets.token_urlsafe(24)`,
    optionale Gültigkeit), Deaktivieren-Button; zeigt fertigen Link `PUBLIC_BASE_URL/?z=<token>`.
  - `GET /admin/gespraeche`: `list_sessions()` mit Turn-/Token-Spalten; `GET /admin/gespraeche/{id}`:
    volles Transkript via `get_history`.
  - `GET /admin/offerten`: `list_offers()` mit Status (released?), PDF-Download
    (`DATA_DIR/offers/offerte-{session_id}.pdf`), Button „An Kunden freigeben" →
    ruft `offer.release_to_customer(token)` (existiert, idempotent) **direkt im Admin** auf.
  - `GET/POST /admin/einstellungen`: Soft/Hard-Turns + Token-Budget pflegen (`set_setting`).
- `templates/admin/layout.html` mit Navigation (Dashboard · Zugang · Gespräche · Offerten ·
  Einstellungen · Logout); übrige Templates schlicht, am vorhandenen `style.css` orientiert.

---

## Kritische Dateien
- Neu: `auth.py`, `routes_admin.py`, `settings.py`, `templates/admin/*`.
- Geändert: `main.py`, `db.py`, `agent.py`, `static/js/app.js`, `templates/index.html`,
  `requirements.txt`, `.env.example`, ggf. `CLAUDE.md` (neue Endpunkte/Env dokumentieren).
- Wiederverwenden: `offer.release_to_customer` / `offer.create_and_send` (`offer.py:133/81`),
  `db.get_history`, `db.count_messages`, `db._conn`, `auth.py`-Muster aus `bewerbung-ralfwbalz`.

## Verifikation (lokal, `./start.sh --reload`, Port 8003)
1. **Start & Migration**: App startet, `data/offerte.db` enthält neue Tabellen/Spalten
   (`sqlite3 data/offerte.db '.schema'`).
2. **Öffentlich (Default)**: `/` lädt, Chat funktioniert wie bisher → keine Regression.
3. **Admin-Login**: bcrypt-Hash in `.env`, `/admin/login` → falsches PW = 401, richtiges → Dashboard.
4. **Zugang-Toggle**: Modus auf `einladung` stellen → `/` ohne `?z=` zeigt Einladungsseite,
   `/chat` liefert 403. Link erzeugen, `/?z=<token>` öffnen → Chat wieder möglich; Link deaktivieren
   → Zugang gesperrt. Zurück auf `oeffentlich` → wieder offen.
5. **Kostenbremse**: `SOFT_TURNS`/`SOFT_TOKENS` testweise klein setzen → Agent leitet hörbar zum
   Abschluss über; `MAX_*` klein → `limit`-Backstop greift. In `/admin/gespraeche` erscheinen
   Turn-/Token-Zahlen.
6. **Abschluss-Sprung**: Gespräch bis `offerte_erstellen` führen → Overlay + Countdown →
   Weiterleitung auf ralfwbalz.ch; Button leitet sofort weiter.
7. **Offerten-Admin**: erzeugte Offerte in `/admin/offerten`, PDF-Download, „Freigeben" sendet an
   Kunden (idempotent – zweiter Klick erneut ohne Fehler).
8. **Docker-Parität**: `docker compose up --build -d`, gleiche Stichproben auf `127.0.0.1:8003`.

## Hinweis zur 16:00-Ausführung
Der eigentliche Umsetzungs-Start um **16:00** muss über `/schedule` als eigenständiger Remote-Agent
angelegt werden – ich kann das nicht selbst auslösen. Der geplante Agent startet **ohne** den
Kontext dieses Chats; sein Auftrag sollte daher explizit auf diese Plandatei verweisen
(`/home/ralf/.claude/plans/ich-m-chte-das-du-modular-waffle.md`) und „umsetzen + lokal verifizieren,
keine Commits/Pushes ohne Rückfrage" enthalten.
