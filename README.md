# Offerten-Assistent · offerte.ralfwbalz.ch

KI-gestützter **Offerten-Assistent** für Ralf W. Balz. Ein potenzieller Kunde plant
im Gespräch mit dem Assistenten sein IT-Projekt; der Agent klärt Schritt für Schritt
die relevanten Punkte und erzeugt am Ende eine strukturierte **Offerten-Grundlage**.
Diese geht als PDF (samt Chat-Transkript) **zuerst nur an Ralf** zur persönlichen
Prüfung – erst nach seiner Freigabe erhält der Auftraggeber die Offerte.

Ein- und Ausgabe können per **Sprache** erfolgen; zusätzlich läuft ein sichtbares
Chat-Protokoll mit. Alle nutzerseitigen Texte sind deutsch.

## Funktionsumfang

- **Chat mit SSE-Streaming** der Claude-Antworten; `session_id` wird browserseitig pro
  Seitenaufruf erzeugt (ein Reload startet ein neues Gespräch).
- **Sprach-Ein-/Ausgabe** serverseitig über OpenAI (Whisper-STT + neuronales TTS,
  Stimme `nova`); der Browser nimmt per `MediaRecorder` auf → funktioniert in allen
  modernen Browsern (auch Firefox/Safari). Ohne Mikrofon bleibt die Texteingabe nutzbar.
- **Strukturierte Offerten-Grundlage** per Tool-Call (`offerte_erstellen`); der Agent
  nennt bewusst **keine Preise** – die kalkuliert Ralf persönlich.
- **Zweistufiger Versand-Flow**: PDF zuerst an Ralf inkl. tokenisiertem Freigabe-Link;
  erst nach `GET /freigabe/{token}` geht die Offerte an den Auftraggeber (idempotent).
- **Kosten-/Missbrauchsschutz**: Rate-Limit pro IP, Turn-Limit pro Session, Honeypot-Feld;
  die OpenAI-Audio-Endpunkte bedienen nur bestehende Sessions.

## Technologie

FastAPI · Jinja2 · SQLite (WAL) · Anthropic Claude (`claude-opus-4-8`, Streaming,
adaptive Thinking) · OpenAI (Sprache: Whisper-STT + `gpt-4o-mini-tts`) · WeasyPrint (PDF) ·
SMTP (STARTTLS) · Docker Compose. Eines von mehreren Geschwisterprojekten unter
`/home/ralf`; Sprach-Ein-/Ausgabe ist von `bewerbung-ralfwbalz` übernommen.

## Schnellstart (Docker, empfohlen)

```bash
cp .env.example .env         # anschliessend Werte ausfüllen (siehe unten)
docker compose up --build -d # Image bauen, Web auf 127.0.0.1:8003 starten

docker compose logs -f web   # Logs verfolgen
docker compose down          # stoppen
```

Die App lauscht bewusst nur auf `127.0.0.1:8003`; in Produktion terminiert der
Host-nginx TLS und proxyt weiter (siehe `setup/`).

## Lokal ohne Docker

```bash
./start.sh            # legt .venv an, installiert requirements, startet uvicorn auf :8003
./start.sh --reload   # Auto-Reload für die Entwicklung
```

Voraussetzung: die WeasyPrint-Systembibliotheken (`libpango`, `libcairo`,
`libgdk-pixbuf`, `libffi`). Für die meisten Fälle ist `docker compose up` einfacher.

## Konfiguration (`.env`)

Vorlage: `.env.example`. Wichtigste Werte:

| Variable | Zweck |
|---|---|
| `ANTHROPIC_API_KEY` | Claude-API-Schlüssel (LLM des Agenten) |
| `OPENAI_API_KEY` | OpenAI-Schlüssel für Sprache (STT/TTS); optional `OPENAI_TTS_VOICE` (Default `nova`), `OPENAI_TTS_SPEED` (Default `1.2`), `OPENAI_STT_MODEL`, `OPENAI_TTS_MODEL` |
| `SMTP_HOST/PORT/USER/PASSWORD` | SMTP-Versand (STARTTLS) |
| `CONTACT_EMAIL` | Empfänger der Prüf-Mail (= Ralf) |
| `PUBLIC_BASE_URL` | Basis-URL für den Freigabe-Link in der Mail an Ralf |
| `DATA_DIR` | SQLite-DB + erzeugte PDFs (im Container `/data`, lokal `./data`) |
| `MAX_SESSIONS_PER_IP` | neue Gespräche pro IP, gleitendes 1-Stunden-Fenster (Default 5) |
| `MAX_TURNS_PER_SESSION` | User-Nachrichten pro Gespräch (Default 40) |

## Architektur

Flache Modulstruktur:

| Datei | Aufgabe |
|---|---|
| `main.py` | FastAPI-App: UI-Auslieferung, SSE-Chat-Endpunkt `POST /chat`, Audio-Endpunkte `POST /chat/stt` (Audio→Text) und `POST /chat/tts` (Text→MP3), Freigabe-Endpunkt `GET /freigabe/{token}`. Rate-/Turn-Limit, Honeypot. |
| `agent.py` | Claude-Anbindung: `stream_reply()` (Streaming) und Tool `offerte_erstellen`, das die Offerte rendert/versendet. |
| `voice.py` | Sprach-Ein-/Ausgabe via OpenAI (httpx): `transcribe()` (Whisper-STT) und `synthesize()` (TTS, MP3). |
| `offer.py` | Pydantic-/Dict-Daten → WeasyPrint-PDF → SMTP-Versand. |
| `db.py` | SQLite (`sessions`, `messages`, `offers`; WAL-Modus, eine Verbindung pro Aufruf). |

Sprache läuft komplett serverseitig: der Browser (`static/js/app.js`) nimmt Audio per
`MediaRecorder` auf und schickt es an `/chat/stt`; die fertige Antwort wird über
`/chat/tts` als MP3 zurückgegeben und vorgelesen. Die kostenpflichtigen Audio-Endpunkte
bedienen nur bereits gestartete `session_id`s und hängen damit am selben Schutz wie der Chat.

### Versand-Flow (bewusst zweistufig)

Beim Tool-Aufruf wird die Offerte als PDF gerendert und **zuerst nur an Ralf**
(`CONTACT_EMAIL`) gesendet – inklusive Chat-Transkript und tokenisiertem Freigabe-Link.
Erst wenn Ralf `GET /freigabe/{token}` aufruft, geht die Offerte an den Auftraggeber
(`offer.release_to_customer`, idempotent via `released_at`). Das PDF liegt unter
`DATA_DIR/offers/offerte-{session_id}.pdf` und wird bei der Freigabe wiederverwendet.

## Deployment

Docker-Compose-Service `web` (Image/Container `offerte`), bindet nur `127.0.0.1:8003`,
Volume `./data:/data`. Host-nginx terminiert Let's-Encrypt-TLS und proxyt weiter
(`setup/offerte.ralfwbalz.ch`; für SSE wichtig: `proxy_buffering off`). Voraussetzung:
DNS-A-Record für `offerte.ralfwbalz.ch`.

Eigenes Git-Repo → GitHub `plebsapps/offerte` (privat).
