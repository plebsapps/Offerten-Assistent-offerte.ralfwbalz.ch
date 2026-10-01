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
- Self-Service-Zugang (E-Mail-Code/OTP): `OTP_TTL_MIN` (Default 10), `OTP_MAX_VERSUCHE`
  (Default 3), `OTP_RESEND_SEKUNDEN` (Default 60), `MAX_ZUGANG_CODES_PER_IP` (Default 5,
  gleitendes 1-Stunden-Fenster pro IP). Nur wirksam im Einladungsmodus und wenn im Admin
  eingeschaltet (Setting `selbst_zugang`, gelesen über `settings.selbst_zugang_aktiv()`).
- Google Ads: optional `GOOGLE_ADS_KONVERSION` – siehe *Google-Ads-Tag* weiter unten.

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
  (Modus-Toggle + Self-Service-Schalter + Einladungslinks anlegen/deaktivieren/verlängern, optional
  direkt per E-Mail versenden via `offer.send_invitation`; listet auch die Self-Service-Anmeldungen),
  `/admin/gespraeche` (+ Transkript), `/admin/offerten` (PDF-Download +
  Freigabe an Kunden via `offer.release_to_customer`), `/admin/einstellungen` (Limit-Schwellen +
  KI-Anbieter-Toggle pflegen).
- **`auth.py`** – bcrypt-Login + Session-Helfer (`anmelden`/`abmelden`/`ist_angemeldet`,
  `require_admin`, `NichtAngemeldet`). Zugangsdaten nur aus der Umgebung.
- **`settings.py`** – zentrale Laufzeit-Konfig: `zugangsmodus()` (`oeffentlich`/`einladung`),
  `selbst_zugang_aktiv()` (Self-Service-Schalter), `ki_anbieter()` (`claude`/`openai`),
  `basis_url()` (öffentliche Basis-URL mit Produktions-Fallback) und die Limit-Getter, gelesen aus
  der `einstellungen`-Tabelle mit Env-/Default-Fallback.
- **`agent.py`** – Dispatcher `stream_reply(..., wind_down=bool)`, der je nach
  `settings.ki_anbieter()` an die Claude- (`_stream_reply_claude`) oder OpenAI-Implementierung
  (`agent_openai.stream_reply`, lazy import) delegiert. Beide liefern dieselbe Event-Schnittstelle.
  Claude-Pfad: `claude-sonnet-4-6`, adaptive thinking (`effort: medium`), streaming. Geteilt
  werden `SYSTEM_PROMPT`,
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
- **`db.py`** – SQLite (`sessions` inkl. `tokens_in`/`tokens_out`/`thema`, `messages`, `offers`,
  `einstellungen`, `zugangslinks`, `zugang_codes`; WAL-Modus, eine Verbindung pro Aufruf;
  Spalten-Migration via `ALTER TABLE … / except OperationalError`). Die `session_id` wird browserseitig pro
  Seitenaufruf erzeugt (`crypto.randomUUID`, nicht persistiert) – ein Reload startet daher ein
  neues Gespräch.

### Zugangssteuerung & Kostenbremse
- **Zugangsmodus** (`settings.zugangsmodus()`, im Admin umschaltbar): `oeffentlich` (wie bisher,
  Default) oder `einladung`. Im Modus `einladung` verlangt **jeder** Besuch von `GET /` einen gültigen
  Token-Link `PUBLIC_BASE_URL/?z=<token>` (Ausnahme: `selbst_verifiziert`, s. u.); ein früher
  gesetztes `zugang_ok` allein zeigt die Chat-Seite nicht mehr. Ein gültiger Token setzt
  `request.session["zugang_ok"]`, das die Endpunkte `/chat*` eines bereits laufenden Gesprächs
  freischaltet; ohne gültigen Zugang liefert `/` die `einladung.html` und `/chat*`
  antworten 403. Die `einladung.html` geht bewusst mit **Status 200** raus (nicht 403, wie
  bis zum 28.09.2026): sie ist die reguläre Startseite für alle, die ohne Link kommen, und
  ein 403 gilt Crawlern als nicht erreichbare Seite — OpenAIs `OAI-AdsBot` lehnte deshalb
  die Zielseite einer ChatGPT-Anzeige ab. Siehe *Indexierung* weiter unten; die Statistik
  auf ralfwbalz.ch hing an diesem 403 und wurde mit umgestellt. `zugangslinks` sind **bewusst mehrfach nutzbar** (eine `session_id` entsteht pro Reload neu)
  und lassen sich deaktivieren bzw. zeitlich begrenzen (`gueltig_bis`). Optional speichert ein Link
  `empfaenger_email`, `anrede` (Frau/Herr/Firma) und `name` und kann direkt per E-Mail verschickt
  werden; die Link-URL baut sich aus `settings.basis_url()` (Produktions-Fallback → nie localhost
  nach aussen). Beim Eintritt über `?z=<token>` landen diese Daten als `request.session["kontakt"]`
  und werden an `agent.stream_reply(..., kontakt=…)` gegeben: `agent.build_system_prompt` hängt
  dann einen Hinweis an, damit der Agent persönlich mit Namen anspricht und die E-Mail (an die der
  Link ging) nur bestätigen lässt, statt danach zu fragen. `anrede` kann **Du-Form** (`männlich`/
  `weiblich` → „Hallo Vorname“) oder **Sie-Form** (`Herr`/`Frau`/`Firma` → „Guten Tag Herr Muster“)
  sein; `offer.ist_du_anrede` steuert Begrüssung und Ton in Mail und Agenten-Hinweis.
- **Self-Service-Zugang** (`settings.selbst_zugang_aktiv()`, im Admin schaltbar; nur im
  Einladungsmodus wirksam): statt der Sackgassen-Seite zeigt `einladung.html` ein Formular. Der
  Interessent gibt Anrede/Name/E-Mail an → `POST /zugang/code` erzeugt einen 6-stelligen Code
  (Tabelle `zugang_codes`, gültig `OTP_TTL_MIN`), versendet ihn via `offer.send_zugang_code` und
  drosselt pro IP (`MAX_ZUGANG_CODES_PER_IP`) sowie per E-Mail (`OTP_RESEND_SEKUNDEN`). `POST
  /zugang/verify` prüft den Code (max. `OTP_MAX_VERSUCHE`) und setzt bei Treffer `zugang_ok`,
  `selbst_verifiziert` (damit ein Reload von `/` nicht erneut die Hinweisseite liefert –
  Token-Links bleiben dagegen pro Besuch zu prüfen) und `kontakt`; eine Info-Mail geht via `offer.notify_selbst_zugang`
  an `CONTACT_EMAIL`. Honeypot-Feld `website` wie beim Chat.
- **Kurzanfrage** (`GET /kurzanfrage`, `templates/kurzanfrage.html`): der schnelle Weg ohne
  Gespräch, verlinkt von `einladung.html` (Button rechts neben LinkedIn, darüber ein grosses
  „ODER“; nur bei aktivem Self-Service). **Unabhängig vom Zugangsmodus** erreichbar, indexierbar,
  in der Sitemap. Thema (Dropdown aus `KURZANFRAGE_THEMEN` in `main.py`, „Allgemein“ zuerst,
  Rest alphabetisch; der Server prüft gegen dieselbe Liste), Freitext (max.
  `KURZANFRAGE_MAX_ZEICHEN`) und E-Mail → `POST /kurzanfrage/code` speichert alles in der Tabelle
  `kurzanfragen` und mailt einen Code (`offer.send_kurzanfrage_code`, nutzt
  `zugang_code_email.html` mit eigenem `titel`; der Text sagt, dass es eine Kurzanfrage ist und
  Ralf sich nach der Bestätigung meldet). `POST /kurzanfrage/bestaetigen` prüft wie
  `/zugang/verify` und schickt die Anfrage dann an `CONTACT_EMAIL` (`send_kurzanfrage_an_ralf`,
  Reply-To Besucher, Besuchertext im HTML-Teil `html.escape`d). Erst senden, dann
  `bestaetigt_am` setzen – ein SMTP-Fehler lässt den Code gültig. Gleiche Limits wie beim Zugang
  (`OTP_*`, `MAX_ZUGANG_CODES_PER_IP`, eigene Zählung), Honeypot `website`. **Eigene Tabelle
  statt `zugang_codes`**, damit ein Kurzanfrage-Code nie den Chat freischaltet.
- **KI-Anbieter** (`settings.ki_anbieter()`, im Admin umschaltbar): `claude` (Default) oder
  `openai` (ChatGPT `gpt-4.1`). `agent.stream_reply` dispatcht entsprechend.
- **Kostenbremse** in `POST /chat` (vor dem Agentenaufruf): geprüft werden Turns **und** Token
  (`turns`/`tokens_in+tokens_out`). Erreicht eines die **Hard**-Schwelle (`max_turns`/`max_tokens`),
  gibt es das bestehende `limit`-Event (Stopp). Erreicht eines die **Soft**-Schwelle
  (`soft_turns`/`soft_tokens`), läuft der Agent mit `wind_down=True` und leitet hörbar zum
  Abschluss über. Token werden in `agent.stream_reply` aus `final.usage` summiert.
- **Anzeigenthema** (`themen.py`): die 13 Anzeigen verlinken auf `/?adgroup=<schluessel>`
  (z. B. `data_migration`). `GET /` prüft den Wert gegen die Whitelist `themen.THEMEN` und legt
  nur den **Schlüssel** in `request.session["thema"]` – nie freien Text aus der URL, denn der
  Titel landet im HTML, in der Begrüssung und im System-Prompt. Weil es im Sitzungs-Cookie
  liegt, bleibt das Thema erhalten, wenn jemand sich umsieht (ralfwbalz.ch, Impressum) und ohne
  Parameter zurückkommt, und es übersteht den Redirect nach `/zugang/verify` sowie
  `/zugang/zuruecksetzen`. Eine neue gültige Anzeige überschreibt, ein Aufruf ohne oder mit
  unbekanntem Wert lässt den alten Wert stehen. Ohne Anzeige gilt `themen.STANDARD`
  („Ihrem Softwareprojekt“, leerer Schlüssel). `main._thema(request)` ist die einzige Quelle
  für: Überschrift in `einladung.html` („Ich kann Ihnen helfen bei: <Titel>“, auch `<title>`)
  und `index.html`, `agent.begruessung(kontakt, thema)` an ihren **drei** Aufrufstellen
  (Template, erste DB-Nachricht, `/begruessung.mp3` – sie müssen denselben Text liefern) und
  `agent.build_system_prompt(..., thema)`, das über `_thema_hinweis` den Anlass anhängt. Je
  Thema gibt es `titel` (Anzeigenname), `gesprochen` (Form für die vorgelesene Begrüssung, ohne
  Schrägstrich) und `stichworte` (Hintergrund für den Prompt). Der Schlüssel wird beim ersten
  `/chat` in `sessions.thema` geschrieben und erscheint als „Anzeige“ in der Admin-
  Gesprächsliste, im Transkript und in der Mail an Ralf. Eine neue Anzeige heisst: Eintrag in
  `themen.py`, sonst nichts. Das Impressum nennt das Thema beim Sitzungs-Cookie – das Cookie
  entsteht damit schon beim ersten Aufruf über eine Anzeige. Die Besucherstatistik auf
  ralfwbalz.ch liest `adgroup` aus dem nginx-Log dieses vhosts und weist Besucher je Anzeige
  aus; die Anzeigenamen dort (`STATISTIK_ANZEIGEN` in `ralfwbalz/main.py`) sind eine Kopie der
  Titel hier – bei einer neuen Anzeige dort nachtragen, sonst erscheint nur der Schlüssel.
- **Chat-Startseite** (`index.html`, vor dem Start): fette Zeile „100 % unverbindlich für Sie
  und mich“ und neben „Gespräch starten“ der Button „Gespräch doch nicht starten“. Er schickt
  `POST /zugang/zuruecksetzen`: das entfernt `zugang_ok`, `selbst_verifiziert` und `kontakt` aus
  der Session (eine Admin-Anmeldung im selben Cookie bleibt) und leitet mit 303 auf `/`, wo dann
  wieder `einladung.html` erscheint. Im Modus `oeffentlich` gäbe das eine Schleife (dort zeigt
  `/` sofort wieder den Chat), darum geht es dann auf `HOMEPAGE_URL`.
- **Button „Gespräch beenden“** (`#endBtn` unter dem Chat, `static/js/app.js`): nach einer
  Bestätigung schickt der Browser „Ich möchte das Gespräch jetzt beenden.“ mit `beenden: true`
  an `POST /chat`. Der Server lehnt das für ein noch nicht begonnenes Gespräch ab, antwortet bei
  schon vorhandener Offerte nur mit `done` (höchstens eine pro Gespräch) und lässt es auch nach
  erreichtem Hard-Limit noch eine Runde zu. `agent.stream_reply(..., beenden=True)` hängt
  `BEENDEN_HINWEIS` an und **erzwingt** in Runde 1 `offerte_erstellen` per `tool_choice`. Bei
  Claude läuft das ohne Thinking – erzwungenes Werkzeug und Thinking vertragen sich nicht, und die
  Folgerunde müsste sonst Thinking-Blöcke vorweisen. Bei OpenAI meldet ein erzwungener Aufruf
  `finish_reason = "stop"`, darum prüft `agent_openai` nur, ob `tool_calls` vorliegen. Danach
  läuft der normale Abschluss (Overlay, Countdown). Ohne eigene Kundennachricht führt der Button
  nur zur Homepage.
- **Abschluss-Sprung**: nach `offer_created` zeigt `static/js/app.js` ein Overlay (`index.html`)
  mit ~8-s-Countdown und Button und leitet auf `HOMEPAGE_URL` weiter.

### Sprache
Das LLM hat **keine** eigene Sprachfunktion. STT und TTS laufen serverseitig über die
OpenAI-API (`voice.py`): der Browser nimmt Audio per `MediaRecorder` auf und schickt es an
`/chat/stt` (Whisper, `gpt-4o-mini-transcribe`); die Antwort wird über `/chat/tts`
(`gpt-4o-mini-tts`, Stimme `nova`) als MP3 vorgelesen – **satzweise während des Streams**
(`static/js/app.js` schneidet Sätze aus dem Token-Strom, synthetisiert sie vorab und spielt
sie als Queue in Reihenfolge ab). Das funktioniert
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

### Kopfzeile, Navigation & Footer
Die fünf Besucherseiten (`index.html`, `einladung.html`, `kurzanfrage.html`, `impressum.html`, `freigabe.html`)
tragen die **gleiche Navigation wie ralfwbalz.ch**: weisse Leiste, animiertes Canvas-Logo
(`static/js/logo.js`, `#navLogo`), „Home" (`{{ homepage_url }}`; das Logo selbst führt auf `https://ralfwbalz.ch/logo`, ebenso das kleine Logo in der Fussleiste), Links auf
`https://ralfwbalz.ch/#…` plus „Offerte", „Kurzanfrage" (`/kurzanfrage`)
(`aria-current="page"`) und „Kontakt" als blauer CTA, dazu Hamburger und `.nav-mobile`-
Schublade (Toggle ebenfalls in `logo.js`). Anders als auf der Hauptseite steht die Leiste
**im Fluss statt `position: fixed`** – die Chat-Seite rechnet mit `.messages { height: 56dvh }`
und einer klebenden `.composer`. Das Markup der Kopfzeile ist wie bisher je Seite dupliziert
(kein `extends` auf der öffentlichen Seite), eine CSS-Version (`?v=…`) also in **allen fünf** Besucher-Templates zu
erhöhen (die beiden Admin-Templates führen eine eigene Nummer).

Alle Links auf die Hauptseite zeigen auf die **nackte** Domain, nicht auf `www.` – dort
leitet nginx seit dem 28.09.2026 mit 301 um, und kanonisch ist `ralfwbalz.ch` (so stehen es
deren sitemap.xml, robots.txt und canonical-Tags). Nicht auf `www.` zurückstellen: das wäre
auf jedem Klick ein unnötiger Umweg. `HOMEPAGE_URL` (Default und `.env`) zeigt ebenfalls
dorthin.

### Fussleiste (identisch mit ralfwbalz.ch)
`templates/_footer.html` ist eine **Byte-für-Byte-Kopie** von
`~/ralfwbalz/templates/_footer.html` und wird von allen fünf Besucherseiten per `include`
eingebunden: Logo, „© 2026 Ralf W. Balz, Individualsoftware und Prozessdigitalisierung für
KMU", dann Bücher, Impressum, Datenschutz, LinkedIn, Cookie-Einstellungen. **Vorlage ist
ralfwbalz** – dort ändern, dann `cp ~/ralfwbalz/templates/_footer.html ~/offerte/templates/`,
beide Container neu bauen, mit `diff` prüfen. Der einzige Unterschied der Projekte, der
Logo-Pfad, kommt als Jinja-Global `fuss_logo` aus `main.py`. „Impressum" und „Datenschutz"
sind relativ und führen bewusst auf das **eigene** Impressum (`#datenschutz` sitzt an der
ersten Datenschutz-Überschrift), „Bücher" zeigt absolut auf ralfwbalz.ch.

Die Footer-Regeln in `style.css` haben dieselben Werte wie auf der Hauptseite (1100 px breit,
Umbruch in eine Spalte ab 600 px); zusätzlich stehen nur `margin: 0`, `line-height: 1.6` und
`display: block`, die dort aus dem globalen Reset kommen, und `flex-shrink: 0` für das
Spalten-Layout hier. Es gibt **keinen Login-Link** mehr im Footer – der Admin-Bereich ist nur
noch direkt über `/admin/login` erreichbar. „Cookie-Einstellungen" steht fest im Markup
(`data-cookie-einstellungen`, `href="/impressum#google-ads"`): `einwilligung.js` bindet daran
das Banner, auf Seiten ohne Google-Tag (`freigabe.html`) führt der Link ins Impressum.

### Google-Ads-Tag & Einwilligungs-Banner
Das Google-Ads-Tag (`AW-18481174898`, Konstante `GOOGLE_ADS_ID` in `main.py`) steht in
`templates/_google_tag.html`, eingebunden direkt nach `<head>` in `index.html`,
`einladung.html`, `kurzanfrage.html` und `impressum.html` – **nicht** in `freigabe.html` (Token-Seite für
Auftraggeber, keine Anzeigen-Zielseite) und nicht im Admin. Dasselbe Tag, dasselbe Konto
und dasselbe Banner wie auf ralfwbalz.ch, aber bewusst als eigene Kopie.

Consent Mode im **Basis-Modus**: `gtag.js` wird erst nach „Zustimmen" geladen, eine Ablehnung
heisst also gar keine Anfrage an Google. Die Wahl liegt im Cookie `werbe_einwilligung`
(`ja`/`nein`, 12 Monate, `Domain=ralfwbalz.ch`) und gilt damit **gemeinsam mit ralfwbalz.ch** –
wer dort gewählt hat, wird hier nicht erneut gefragt. Der Bannertext nennt deshalb beide
Domains und muss mit `ralfwbalz/static/js/main.js` übereinstimmen. Lesen und Schreiben stehen
in `_google_tag.html` (`googleAds.einwilligung()` / `.einwilligungMerken()`), das bis auf den
Kommentar mit der Kopie in `ralfwbalz/` identisch ist; `Domain` wird nur auf dem echten Host
gesetzt, lokal bleibt es ein Host-Cookie. Bis 01.10.2026 lag die Wahl pro Domain in
`localStorage`: ein dortiges `nein` wird ins Cookie übernommen (und sticht ein `ja`), ein `ja`
verfällt, weil es nur für eine Website gegeben wurde. Banner, Footer-Link
„Cookie-Einstellungen" (Widerruf: löscht `_gcl*`/`_ga*`, auch auf `.ralfwbalz.ch`, wo gtag
sie tatsächlich setzt, und lädt neu) und `window.googleAdsKonversion()` liegen in
`static/js/einwilligung.js`.

Die Conversion feuert in `app.js` beim Stream-Ereignis `offer_created`, nur mit Zustimmung
und nur, wenn `GOOGLE_ADS_KONVERSION` gesetzt ist – das vollständige `send_to` der
Conversion-Aktion „Offerte angefordert" (`AW-18481174898/<label>`), beim Import gelesen und
als Jinja-Global an die Templates gegeben. Das Impressum hat den passenden Abschnitt
`#google-ads`, auf den das Banner verlinkt.

### Aufbewahrung (12 Monate)
`db.alte_daten_loeschen(tage)` löscht, was älter als `AUFBEWAHRUNG_TAGE` (365, `main.py`) ist:
Gespräche samt Nachrichten, Offerten und PDF-Datei (gemessen an `sessions.created_at`),
Zugangscodes und Kurzanfragen (je `created_at`) und Einladungslinks, die so lange **nicht benutzt** wurden
(`COALESCE(letzte_nutzung, created_at)` – ein aktiv genutzter Link bleibt). Läuft beim Start
und danach täglich als asyncio-Task (`_aufraeumen_schleife`, der eigentliche Lauf im Executor);
Fehler werden nur geloggt. Das Impressum sagt dasselbe zu – beides zusammen ändern.
Nicht erfasst: die Offerten-Mails mit Transkript im Postfach von `CONTACT_EMAIL`.

### HEAD-Anfragen
`HeadWieGet` (ASGI-Middleware oben in `main.py`) beantwortet `HEAD` wie `GET`, nur ohne
Rumpf. Ohne sie antwortet jeder Pfad mit **405**, weil FastAPIs `@app.get(...)` wirklich nur
`GET` registriert – anders als reines Starlette. Relevant für Link-Prüfer, Monitoring und
Crawler, die die Erreichbarkeit vorab mit `HEAD` testen; die Startseite ist die Zielseite
einer Anzeige. Die Methode wird nur in einer **Kopie** des Scope auf `GET` gesetzt, damit
uvicorn weiterhin `HEAD` sieht. Gleiche Klasse in `ralfwbalz/main.py` – die Projekte teilen
bewusst keinen Code. Routen, die es nur als `POST` gibt (`/chat`, `/zugang/*`), antworten auf
`HEAD` weiterhin korrekt mit 405.

### Favicon
`static/img/favicon.ico` (16/32/48), `favicon.svg` und `apple-touch-icon.png` (180 px, Grund
`#0a0a0a`) sind **Kopien aus dem Hauptprojekt** – erzeugt von
`ralfwbalz/werkzeuge/favicon_erzeugen.py` aus dem Logo, nicht von Hand gezeichnet. Wer sie
ändern will, ändert sie dort und kopiert sie erneut herüber; Pillow ist hier keine
Abhängigkeit. `main.py` liefert `/favicon.ico` und `/apple-touch-icon.png` per `FileResponse`
an der Wurzel aus, weil Browser und iOS diese Pfade unabhängig von den `<link>`-Tags
anfragen. Beide liegen wie robots.txt vor dem Zugangs-Gate. `logo-ralfwbalz.svg` bleibt
unangetastet und dient weiterhin als Footer-Logo und OG-Bild.

### Indexierung & Crawler
`GET /robots.txt` und `GET /sitemap.xml` werden in `main.py` inline erzeugt (Domain
hart verdrahtet) und liegen **vor** dem Zugangs-Gate. Die robots.txt gibt drei Gruppen aus –
`OAI-AdsBot`, `OAI-SearchBot` und `*` –, weil OpenAI für ChatGPT Ads eine eigene Gruppe für
`OAI-AdsBot` verlangt. Dabei gilt: eine **benannte** User-agent-Gruppe ersetzt die
`*`-Gruppe vollständig, die Regeln müssen also je Gruppe wiederholt werden; darum stehen sie
einmal in `_ROBOTS_REGELN` und werden über `_ROBOTS_AGENTEN` ausgegeben. Gesperrt bleiben
überall `/chat`, `/freigabe` und `/admin`.

Die Meta-Robots-Tags sind **nicht** einheitlich, das ist Absicht:
`einladung.html`, `kurzanfrage.html` und `impressum.html` stehen auf `index, follow` und sind in der Sitemap –
`einladung.html` ist die Zielseite der Anzeige und trägt zusätzlich `description`,
`canonical` und OG-/Twitter-Tags. `index.html` (Chat), `freigabe.html` (Token-URL) und die
beiden Admin-Templates behalten `noindex`. Dass `GET /` je nach Zugang zwei verschiedene
Templates rendert, ist dabei unkritisch: ein Crawler sieht nur `einladung.html`.

Der Admin-Bereich behält seine eigene `.admin-topbar` (`admin/layout.html`); `admin/login.html`
nutzt sie neu ebenfalls (vorher eine kaputte `.topbar`-Variante mit undefinierter `.subbrand`).
`static/img/logo-ralfwbalz.svg` ist aus dem Hauptprojekt kopiert und dient als Footer-Logo
und Favicon.

### Chat-Stream
`POST /chat` liefert Server-Sent Events. `agent.stream_reply` yieldet Events
(`token`, `offer_created`, `done`, `error`, `limit`); `static/js/app.js` parst den Stream,
zeigt das Transkript live und liest die Antwort satzweise per TTS vor (siehe „Sprache“).

## Deployment

Docker-Compose-Service `web` (Image/Container `offerte`), bindet nur `127.0.0.1:8003`,
Volume `./data:/data`. nginx auf dem Host terminiert Let's-Encrypt-TLS und proxyt weiter
(`setup/offerte.ralfwbalz.ch`; SSE: `proxy_buffering off`). Voraussetzung: DNS-A-Record
`offerte.ralfwbalz.ch`. Eigenes Git-Repo → GitHub `plebsapps/offerte` (privat).

Der vhost schreibt sein Zugriffsprotokoll bewusst nach `/var/log/nginx/offerte.access.log`
statt in das gemeinsame `access.log` aller vhosts: aus dieser Datei trägt das
Nachbarprojekt (`~/ralfwbalz/werkzeuge/offerte_import.sh`) die Besuche stündlich in die
Besucherstatistik unter ralfwbalz.ch/statistik nach. Diese App selbst bleibt davon
unberührt – sie erfasst nichts und kennt jenes Projekt nicht. Wird die `access_log`-Zeile
entfernt, bleibt die Statistik dort still stehen.
