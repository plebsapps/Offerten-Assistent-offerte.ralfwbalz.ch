# Übergabe-Status für den Cloud-Agenten

> Diese Datei beschreibt den **aktuellen Stand** der in `IMPLEMENTATION_PLAN.md`
> beschriebenen Erweiterung (Admin-Bereich, umschaltbarer Zugang, Abschluss-Sprung,
> Kostenbremse). Sie wurde für einen frischen Agenten geschrieben, der die Umsetzung
> **fortsetzt**. Lies zuerst `IMPLEMENTATION_PLAN.md` (Gesamtplan) und dann diese Datei.

## Bereits erledigt (committet auf diesem Branch)

- **`requirements.txt`**: `bcrypt==4.2.0`, `itsdangerous==2.2.0` ergänzt.
- **`.env.example`**: `HOMEPAGE_URL`, `ADMIN_USER`, `ADMIN_PASSWORD_HASH`, `SECRET_KEY`,
  `SOFT_TURNS`, `MAX_TOKENS_PER_SESSION`, `SOFT_TOKENS` dokumentiert.
- **`auth.py`** (neu): 1:1-Port aus `../bewerbung-ralfwbalz/auth.py` – bcrypt-Login,
  Session-Helfer (`anmelden`/`abmelden`/`ist_angemeldet`), `require_admin`, `NichtAngemeldet`.
- **`settings.py`** (neu): `zugangsmodus()` (`oeffentlich`/`einladung`) und Limit-Getter
  `max_turns()/soft_turns()/max_tokens()/soft_tokens()` – lesen aus `einstellungen` mit
  Env/Default-Fallback.
- **`db.py`**: neue Tabellen `einstellungen` + `zugangslinks`; Migration der `sessions`-Spalten
  `tokens_in`/`tokens_out`; neue Funktionen: `get_setting`/`set_setting`,
  `create_zugangslink`/`list_zugangslinks`/`get_zugangslink`/`set_zugangslink_deaktiviert`/
  `touch_zugangslink`, `add_session_usage`/`get_session_usage`, `list_sessions`/`list_offers`/
  `get_offer_by_session`.

Alle bisher geänderten Module kompilieren (`python3 -m py_compile db.py settings.py auth.py
agent.py main.py` = OK).

## NOCH ZU TUN (in dieser Reihenfolge)

1. **`agent.py`** – `stream_reply(session_id, history, wind_down: bool = False)`:
   - Bei `wind_down=True` einen Abschluss-Hinweis an `SYSTEM_PROMPT` hängen (zusammenfassen,
     dem Kunden mündlich sagen, dass man zum Abschluss kommt und offene Punkte Ralf persönlich
     klärt, dann – sofern Kontaktdaten da sind – `offerte_erstellen` aufrufen).
   - Token-`usage` je Stream-Runde aufsummieren (`final.usage.input_tokens/output_tokens`) und
     am Ende `db.add_session_usage(session_id, in, out)` schreiben.

2. **`main.py`** – Verdrahtung:
   - `SessionMiddleware` (secret = `SECRET_KEY`, `same_site="lax"`,
     `https_only=PUBLIC_BASE_URL.startswith("https")`) + Exception-Handler für
     `auth.NichtAngemeldet` → Redirect `/admin/login`.
   - `app.include_router(routes_admin.router)`.
   - `import settings, auth, routes_admin`; `HOMEPAGE_URL = os.environ.get(...)`.
   - **Zugangs-Gate**: bei `settings.zugangsmodus()=="einladung"`:
     - `GET /`: gültiges `?z=<token>` (über `db.get_zugangslink`, nicht deaktiviert/abgelaufen)
       setzt `request.session["zugang_ok"]=True` + `db.touch_zugangslink`; sonst (kein
       `zugang_ok` im Cookie) Template `einladung.html` mit Status 403.
     - `POST /chat`, `/chat/stt`, `/chat/tts`: ohne `request.session.get("zugang_ok")` → 403.
       (stt/tts brauchen dafür einen `request: Request`-Parameter.)
   - `GET /` gibt `homepage_url=HOMEPAGE_URL` an `index.html`.
   - **Kombiniertes Limit** in `/chat` (vor dem Agentenaufruf): `turns = count_messages(...,'user')`,
     `tin,tout = get_session_usage(...)`. Hard (`turns>=max_turns()` ODER
     `tin+tout>=max_tokens()`) → bestehendes `limit`-Event. Soft (`turns>=soft_turns()` ODER
     Tokens `>=soft_tokens()`) → `wind_down=True` an `agent.stream_reply` übergeben.
   - `robots.txt`: `Disallow: /admin` ergänzen.

3. **`routes_admin.py`** (neu, Muster aus `../bewerbung-ralfwbalz/routes_admin.py`):
   - `/admin/login` (GET/POST), `/admin/logout`.
   - `/admin` Dashboard (Kennzahlen aus `list_sessions`/`list_offers`).
   - `/admin/zugang`: Modus-Toggle (`set_setting('zugangsmodus', ...)`), Liste der Links,
     „neuen Link erzeugen" (`secrets.token_urlsafe(24)`, optionale Gültigkeit in Tagen),
     Deaktivieren; zeigt fertigen Link `PUBLIC_BASE_URL/?z=<token>`.
   - `/admin/gespraeche` (Liste via `list_sessions`) + `/admin/gespraeche/{session_id}`
     (Transkript via `get_history`).
   - `/admin/offerten` (Liste via `list_offers`), `/admin/offerten/{session_id}/pdf`
     (FileResponse auf `pdf_path`), `/admin/offerten/{token}/freigeben` →
     `offer.release_to_customer(token)` (existiert, idempotent).
   - `/admin/einstellungen` (GET/POST): `max_turns/soft_turns/max_tokens/soft_tokens` via
     `set_setting` pflegen.
   - Alle Routen außer Login via `Depends(auth.require_admin)`.

4. **Templates** (neu, `templates/admin/`): `layout.html`, `login.html`, `dashboard.html`,
   `zugang.html`, `gespraeche.html`, `gespraech.html`, `offerten.html`, `einstellungen.html`;
   außerdem `templates/einladung.html` (öffentliche „Zugang nur per Einladung"-Seite).
   Schlicht halten, am bestehenden `static/css/style.css` orientiert (navy Topbar, kein Tailwind).

5. **Frontend Abschluss-Sprung**: `templates/index.html` verstecktes Overlay (Text, Countdown,
   Button „Zur Homepage von Ralf W. Balz", URL aus `homepage_url`); `static/js/app.js`:
   `offer_created` setzt Flag, bei `done` + Flag im `finally` Overlay zeigen + ~8-s-Countdown →
   Redirect auf `homepage_url`, Button leitet sofort weiter. CSS für Overlay + Admin in
   `static/css/style.css` ergänzen.

6. **Verifikation** (lokal `./start.sh --reload`, Port 8003) gemäß Abschnitt „Verifikation" in
   `IMPLEMENTATION_PLAN.md`: Migration, öffentlicher Chat (Regression), Admin-Login,
   Zugang-Toggle, Kostenbremse (Soft/Hard), Abschluss-Sprung, Offerten-Freigabe. Danach Docker.

7. **`CLAUDE.md`** aktualisieren (neue Endpunkte `/admin*`, Zugangsmodus, neue Env-Variablen,
   Token-/Kosten-Tracking).

## Hinweise
- `agent.py` war bereits vor dieser Aufgabe geändert (`MODEL = "claude-sonnet-4-6"`); Modellwahl
  ist **nicht** Teil der Aufgabe – Wert beibehalten.
- Reuse: `offer.release_to_customer`/`offer.create_and_send` (`offer.py`), `db.get_history`,
  `db.count_messages`, `db._conn`.
- Vor `git push`/Commit auf einen anderen als den WIP-Branch bzw. vor Merge nach `main`:
  Rücksprache mit Ralf (kein automatischer Kunden-Versand aktivieren – zweistufiger Flow bleibt).
- **`IMPLEMENTATION_PLAN.md` und `IMPLEMENTATION_STATUS.md` nach Abschluss wieder entfernen.**
