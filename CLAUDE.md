# CLAUDE.md

Diese Datei leitet Claude Code (claude.ai/code) bei der Arbeit in diesem Repository an.

## Was das ist

KI-gestützter **Offerten-Assistent** für ralfwbalz.ch, erreichbar unter
**offerte.ralfwbalz.ch**. Ein potenzieller Kunde plant im Gespräch ein IT-Projekt; am Ende
erzeugt der Agent eine strukturierte Offerten-Grundlage. Ein- und Ausgabe können per Sprache
erfolgen (Browser), zusätzlich läuft ein Chat-Protokoll mit. Alle nutzerseitigen Texte sind
deutsch – das bitte beibehalten.

## Commands

```bash
# Primär: Docker (entspricht Produktion)
docker compose up --build -d   # Image bauen, Container auf 127.0.0.1:8003 starten
docker compose logs -f         # Logs verfolgen
docker compose down            # Container stoppen/entfernen

# Alternativ lokal (venv + uvicorn). WeasyPrint braucht System-Libs
# (libpango, libcairo, libgdk-pixbuf, libffi) – ggf. per apt installieren.
./start.sh                 # .venv anlegen, Deps installieren, uvicorn auf 127.0.0.1:8003
./start.sh --reload        # Auto-Reload für lokale Entwicklung
```

Keine Tests/Linter konfiguriert.

## Konfiguration (`.env`, siehe `.env.example`)

- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI (Sprach-Ein-/Ausgabe): `OPENAI_API_KEY`; optional `OPENAI_TTS_VOICE` (Default
  `nova`), `OPENAI_TTS_SPEED` (Default `1.2`), `OPENAI_STT_MODEL`, `OPENAI_TTS_MODEL`
- SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `CONTACT_EMAIL`
- `PUBLIC_BASE_URL` (für den Freigabe-Link in der Mail an Ralf **und** die Einladungslinks)
- `HOMEPAGE_URL` (Default `https://ralfwbalz.ch`) – Ziel des Abschluss-Sprungs nach erstellter Offerte
- `DATA_DIR` (SQLite-DB + PDFs; im Container `/data`, lokal `./data`)
- Admin-Bereich: `ADMIN_USER`, `ADMIN_PASSWORD_HASH` (bcrypt-Hash, Erzeugung siehe `.env.example`),
  `SECRET_KEY` (signiert das Session-Cookie für Admin-Login **und** den Einladungs-Zugang)
- Kostenbremse (kombiniert Turns **und** Token, was zuerst greift): `MAX_SESSIONS_PER_IP`
  (Default 5, gleitendes 1-Stunden-Fenster pro IP), `MAX_TURNS_PER_SESSION` (Default 40),
  `SOFT_TURNS` (Default 30), `MAX_TOKENS_PER_SESSION` (Default 150000), `SOFT_TOKENS`
  (Default 110000). Die Hard-/Soft-Schwellen sind **im Admin überschreibbar** (`einstellungen`-
  Tabelle, gelesen über `settings.py`); die Env-Werte sind nur Fallback/Default.

## Architektur

- **`main.py`** – FastAPI: liefert die UI, den SSE-Chat-Endpunkt `POST /chat`, die
  Audio-Endpunkte `POST /chat/stt` und `POST /chat/tts` (OpenAI, siehe `voice.py`) und den
  Freigabe-Endpunkt `GET /freigabe/{token}`. `SessionMiddleware` (signiertes Cookie) plus
  Exception-Handler für `auth.NichtAngemeldet` → Redirect `/admin/login`. Rate-Limit pro IP,
  kombinierte Kostenbremse (siehe unten), Honeypot-Feld `website`, **Zugangs-Gate** vor
  `/`, `/chat`, `/chat/stt`, `/chat/tts`. Die Audio-Endpunkte bedienen nur bestehende
  `session_id`s, damit die kostenpflichtigen OpenAI-Calls am selben Missbrauchsschutz wie der
  Chat hängen.
- **`routes_admin.py`** – Admin-Bereich unter `/admin*` (alle Routen außer Login via
  `Depends(auth.require_admin)`): Login/Logout, Dashboard (Kennzahlen), `/admin/zugang`
  (Modus-Toggle + Einladungslinks anlegen/deaktivieren, optional direkt per E-Mail versenden via
  `offer.send_invitation`), `/admin/gespraeche` (+ Transkript), `/admin/offerten` (PDF-Download +
  Freigabe an Kunden via `offer.release_to_customer`), `/admin/einstellungen` (Limit-Schwellen +
  KI-Anbieter-Toggle pflegen).
- **`auth.py`** – bcrypt-Login + Session-Helfer (`anmelden`/`abmelden`/`ist_angemeldet`,
  `require_admin`, `NichtAngemeldet`). Zugangsdaten nur aus der Umgebung.
- **`settings.py`** – zentrale Laufzeit-Konfig: `zugangsmodus()` (`oeffentlich`/`einladung`),
  `ki_anbieter()` (`claude`/`openai`), `basis_url()` (öffentliche Basis-URL mit Produktions-
  Fallback) und die Limit-Getter, gelesen aus der `einstellungen`-Tabelle mit Env-/Default-Fallback.
- **`agent.py`** – Dispatcher `stream_reply(..., wind_down=bool)`, der je nach
  `settings.ki_anbieter()` an die Claude- (`_stream_reply_claude`) oder OpenAI-Implementierung
  (`agent_openai.stream_reply`, lazy import) delegiert. Beide liefern dieselbe Event-Schnittstelle.
  Claude-Pfad: `claude-opus-4-8`, adaptive thinking, streaming. Geteilt werden `SYSTEM_PROMPT`,
  `WIND_DOWN_HINWEIS` und das Tool `offerte_erstellen` (structured), das am Ende die Offerten-
  Grundlage extrahiert. Der Tool-Aufruf rendert/versendet die Offerte direkt
  (`offer.create_and_send`); danach läuft die Streaming-Schleife (`MAX_TOOL_ROUNDS`) noch eine
  Runde weiter für die mündliche Abschlussbestätigung. Tool-Fehler werden als `tool_result`-Text
  zurückgegeben, nicht geworfen. Bei aktivem Soft-Limit hängt `wind_down=True` einen Abschluss-
  Hinweis an den Prompt; je Stream-Runde wird `usage` aufsummiert und via `db.add_session_usage`
  geschrieben.
- **`agent_openai.py`** – OpenAI-Variante (ChatGPT, `gpt-4.1`). Spiegelt die Event-Schnittstelle,
  nutzt `chat.completions.create(stream=True, stream_options={"include_usage": True})`, hüllt das
  geteilte `OFFER_TOOL`-Schema ins Function-Format und setzt streamende `tool_calls` über die
  Chunks zusammen. `OPENAI_API_KEY` erforderlich (ohnehin für Sprache nötig).
- **`offer.py`** – Pydantic-/Dict-Daten → WeasyPrint-PDF → SMTP. Wichtig: Versand-Flow.
  `send_invitation(...)` mailt zusätzlich einen Einladungslink (ohne Anhang) an den Empfänger;
  `_smtp_send` hat dafür optionales PDF.
- **`db.py`** – SQLite (`sessions` inkl. `tokens_in`/`tokens_out`, `messages`, `offers`,
  `einstellungen`, `zugangslinks`; WAL-Modus, eine Verbindung pro Aufruf; Spalten-Migration
  via `ALTER TABLE … / except OperationalError`). Die `session_id` wird browserseitig pro
  Seitenaufruf erzeugt (`crypto.randomUUID`, nicht persistiert) – ein Reload startet daher ein
  neues Gespräch.

### Zugangssteuerung & Kostenbremse
- **Zugangsmodus** (`settings.zugangsmodus()`, im Admin umschaltbar): `oeffentlich` (wie bisher,
  Default) oder `einladung`. Im Modus `einladung` braucht es einen gültigen Token-Link
  `PUBLIC_BASE_URL/?z=<token>`; ein gültiger Token setzt `request.session["zugang_ok"]` und der
  Chat ist freigeschaltet, sonst liefert `/` die `einladung.html` (403) und `/chat*` antworten
  403. `zugangslinks` sind **bewusst mehrfach nutzbar** (eine `session_id` entsteht pro Reload neu)
  und lassen sich deaktivieren bzw. zeitlich begrenzen (`gueltig_bis`). Optional speichert ein Link
  eine `empfaenger_email` und kann direkt per E-Mail verschickt werden; die Link-URL baut sich aus
  `settings.basis_url()` (Produktions-Fallback → nie localhost nach aussen).
- **KI-Anbieter** (`settings.ki_anbieter()`, im Admin umschaltbar): `claude` (Default) oder
  `openai` (ChatGPT `gpt-4.1`). `agent.stream_reply` dispatcht entsprechend.
- **Kostenbremse** in `POST /chat` (vor dem Agentenaufruf): geprüft werden Turns **und** Token
  (`turns`/`tokens_in+tokens_out`). Erreicht eines die **Hard**-Schwelle (`max_turns`/`max_tokens`),
  gibt es das bestehende `limit`-Event (Stopp). Erreicht eines die **Soft**-Schwelle
  (`soft_turns`/`soft_tokens`), läuft der Agent mit `wind_down=True` und leitet hörbar zum
  Abschluss über. Token werden in `agent.stream_reply` aus `final.usage` summiert.
- **Abschluss-Sprung**: nach `offer_created` zeigt `static/js/app.js` ein Overlay (`index.html`)
  mit ~8-s-Countdown und Button und leitet auf `HOMEPAGE_URL` weiter.

### Sprache
Das LLM hat **keine** eigene Sprachfunktion. STT und TTS laufen serverseitig über die
OpenAI-API (`voice.py`): der Browser nimmt Audio per `MediaRecorder` auf und schickt es an
`/chat/stt` (Whisper, `gpt-4o-mini-transcribe`); die fertige Antwort wird über `/chat/tts`
(`gpt-4o-mini-tts`, Stimme `nova`) als MP3 vorgelesen (`static/js/app.js`). Das funktioniert
in allen Browsern (auch Firefox/Safari). Ohne Mikrofon-/Aufnahmeunterstützung funktioniert
die Texteingabe weiter; ein Banner weist darauf hin. Vorbild ist die Schwesterseite
`bewerbung-ralfwbalz`.

### Versand-Flow (bewusst zweistufig)
Bei Tool-Aufruf wird die Offerte als PDF gerendert und **zuerst nur an Ralf**
(`CONTACT_EMAIL`) gesendet – inklusive Chat-Transkript und einem tokenisierten
**Freigabe-Link**. Erst wenn Ralf `GET /freigabe/{token}` aufruft, geht die Offerte an den
Auftraggeber (`offer.release_to_customer`, idempotent via `released_at`/`mark_released`). Den
automatischen Versand an den Kunden nicht ohne Rücksprache aktivieren. Das PDF liegt unter
`DATA_DIR/offers/offerte-{session_id}.pdf` und wird bei der Freigabe wiederverwendet (fehlt
es, wird neu gerendert).

### Chat-Stream
`POST /chat` liefert Server-Sent Events. `agent.stream_reply` yieldet Events
(`token`, `offer_created`, `done`, `error`, `limit`); `static/js/app.js` parst den Stream,
zeigt das Transkript live und liest die fertige Antwort per TTS vor.

## Deployment

Docker-Compose-Service `web` (Image/Container `offerte`), bindet nur `127.0.0.1:8003`,
Volume `./data:/data`. nginx auf dem Host terminiert Let's-Encrypt-TLS und proxyt weiter
(`setup/offerte.ralfwbalz.ch`; SSE: `proxy_buffering off`). Voraussetzung: DNS-A-Record
`offerte.ralfwbalz.ch`. Eigenes Git-Repo → GitHub `plebsapps/offerte` (privat).
