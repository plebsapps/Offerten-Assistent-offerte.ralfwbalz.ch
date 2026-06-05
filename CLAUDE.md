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
- `PUBLIC_BASE_URL` (für den Freigabe-Link in der Mail an Ralf)
- `DATA_DIR` (SQLite-DB + PDFs; im Container `/data`, lokal `./data`)
- `MAX_SESSIONS_PER_IP` (Default 5, gleitendes 1-Stunden-Fenster pro IP),
  `MAX_TURNS_PER_SESSION` (Default 40 User-Turns) – Kosten-/Missbrauchsschutz

## Architektur

- **`main.py`** – FastAPI: liefert die UI, den SSE-Chat-Endpunkt `POST /chat`, die
  Audio-Endpunkte `POST /chat/stt` und `POST /chat/tts` (OpenAI, siehe `voice.py`) und den
  Freigabe-Endpunkt `GET /freigabe/{token}`. Rate-Limit pro IP und Turn-Limit pro Session,
  Honeypot-Feld `website`. Die Audio-Endpunkte bedienen nur bestehende `session_id`s, damit
  die kostenpflichtigen OpenAI-Calls am selben Missbrauchsschutz wie der Chat hängen.
- **`agent.py`** – Claude (`claude-opus-4-8`, adaptive thinking, streaming). System-Prompt
  führt das Beratungsgespräch (gesprochene Sprache, eine Frage pro Antwort, **keine Preise**).
  Tool `offerte_erstellen` (structured) extrahiert am Ende die Offerten-Grundlage. Der
  Aufruf rendert/versendet die Offerte direkt (`offer.create_and_send`); danach läuft die
  Streaming-Schleife (`MAX_TOOL_ROUNDS`) noch eine Runde weiter, damit Claude die mündliche
  Abschlussbestätigung gibt. Tool-Fehler werden als `tool_result`-Text zurückgegeben, nicht
  geworfen.
- **`offer.py`** – Pydantic-/Dict-Daten → WeasyPrint-PDF → SMTP. Wichtig: Versand-Flow.
- **`db.py`** – SQLite (`sessions`, `messages`, `offers`; WAL-Modus, eine Verbindung pro
  Aufruf). Die `session_id` wird browserseitig pro Seitenaufruf erzeugt (`crypto.randomUUID`,
  nicht persistiert) – ein Reload startet daher ein neues Gespräch.

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

Docker-Compose-Service `offerte`, bindet nur `127.0.0.1:8003`, Volume `./data:/data`.
nginx auf dem Host terminiert Let's-Encrypt-TLS und proxyt weiter
(`setup/offerte.ralfwbalz.ch`; SSE: `proxy_buffering off`). Voraussetzung: DNS-A-Record
`offerte.ralfwbalz.ch`. Eigenes Git-Repo → GitHub `plebsapps/offerte` (privat).
