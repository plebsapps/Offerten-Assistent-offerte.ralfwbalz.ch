"""FastAPI-App für den Offerten-Assistenten (offerte.ralfwbalz.ch).

Sprache läuft serverseitig über OpenAI (Whisper-STT + neuronales TTS, siehe
``voice.py``). Hier: Auslieferung der UI, der SSE-Chat-Endpunkt zum Streamen der
Claude-Antworten, die Audio-Endpunkte ``/chat/stt`` und ``/chat/tts`` sowie der
Freigabe-Endpunkt, über den Ralf die fertige Offerte an den Auftraggeber freigibt.
"""
import os
import json
import asyncio
import re
import hmac
import secrets
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, Response, StreamingResponse,
                               RedirectResponse, FileResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

import db
import agent
import offer
import voice
import auth
import settings
import routes_admin

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "unsicher-bitte-setzen"),
    same_site="lax",
    https_only=os.environ.get("PUBLIC_BASE_URL", "").startswith("https"),
)


class HeadWieGet:
    """Beantwortet HEAD wie GET, aber ohne Rumpf.

    FastAPI registriert bei ``@app.get(...)`` wirklich nur GET – anders als reines
    Starlette, das HEAD automatisch mitnimmt. Ohne diese Middleware antwortet jeder
    Pfad auf HEAD mit 405. Browser stört das nicht, Link-Prüfer, Monitoring und
    Crawler, die die Erreichbarkeit vorab mit HEAD testen, schon – und die Startseite
    ist die Zielseite einer Anzeige. Gleiche Klasse liegt in ralfwbalz/main.py; die
    beiden Projekte teilen bewusst keinen Code.

    Die Methode wird nur in einer **Kopie** des Scope auf GET gesetzt, damit uvicorn
    weiterhin HEAD sieht und seine eigene Rumpf-Unterdrückung greift. Die Kopfzeilen
    der GET-Antwort bleiben unverändert (inklusive Content-Length), wie es die
    Spezifikation für HEAD verlangt; nur der Rumpf wird verworfen.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def ohne_rumpf(nachricht):
            if nachricht["type"] == "http.response.body":
                # Zwischenstücke verwerfen, am Ende genau einen leeren Rumpf senden.
                if not nachricht.get("more_body", False):
                    await send({"type": "http.response.body", "body": b"",
                                "more_body": False})
                return
            await send(nachricht)

        await self.app(dict(scope, method="GET"), receive, ohne_rumpf)


app.add_middleware(HeadWieGet)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
app.include_router(routes_admin.router)

MAX_SESSIONS_PER_IP = int(os.environ.get("MAX_SESSIONS_PER_IP", "5"))
RATE_WINDOW_SECONDS = 3600  # 1 Stunde
HOMEPAGE_URL = os.environ.get("HOMEPAGE_URL", "https://ralfwbalz.ch")
# Das Logo in der Kopfzeile zeigt auf die Hauptseite. Als Jinja-Global steht der
# Wert allen Templates zur Verfügung, auch impressum/einladung/freigabe, die
# sonst keinen eigenen Kontext dafür bekämen.
templates.env.globals["homepage_url"] = HOMEPAGE_URL

# Google Ads: das Tag steht in templates/_google_tag.html und lädt erst nach
# der Zustimmung im Banner (static/js/einwilligung.js). GOOGLE_ADS_KONVERSION ist
# das vollständige send_to der Conversion-Aktion "Offerte angefordert"
# (AW-…/Label); leer = das Tag läuft, meldet aber keine Conversion.
GOOGLE_ADS_ID = "AW-18481174898"
templates.env.globals["google_ads_id"] = GOOGLE_ADS_ID
templates.env.globals["google_ads_konversion"] = os.environ.get("GOOGLE_ADS_KONVERSION", "").strip()

# Self-Service-Zugang (E-Mail-Code/OTP)
OTP_TTL_MIN = int(os.environ.get("OTP_TTL_MIN", "10"))          # Gültigkeit des Codes
OTP_MAX_VERSUCHE = int(os.environ.get("OTP_MAX_VERSUCHE", "3"))  # Eingabeversuche je Code
OTP_RESEND_SEKUNDEN = int(os.environ.get("OTP_RESEND_SEKUNDEN", "60"))  # Sperre erneuter Anfragen
MAX_ZUGANG_CODES_PER_IP = int(os.environ.get("MAX_ZUGANG_CODES_PER_IP", "5"))  # je Stunde/IP

# Kurzanfrage (/kurzanfrage): Themen fürs Dropdown, "Allgemein" zuerst, dann alphabetisch.
# Die Seite rendert die Liste, der Server prüft den gewählten Wert dagegen.
KURZANFRAGE_THEMEN = (
    "Allgemein",
    "App-Entwicklung (Mobile)",
    "Automatisierung von Abläufen",
    "Beratung / Zweitmeinung",
    "Datenbank",
    "Datenmigration",
    "Excel / Office-Lösungen",
    "IT-Projekt",
    "KI-Integration",
    "Schnittstellen / Integration",
    "Wartung / Support bestehender Software",
    "Webanwendung",
    "Website",
)
KURZANFRAGE_MAX_ZEICHEN = 2000


# Personendaten (Gespräche, Offerten samt PDF, Zugangscodes, unbenutzte
# Einladungslinks) werden nach 12 Monaten gelöscht – so steht es im Impressum.
AUFBEWAHRUNG_TAGE = 365
AUFRAEUMEN_INTERVALL = 24 * 3600  # einmal täglich, erster Lauf beim Start


def _aufraeumen() -> None:
    try:
        anzahl = db.alte_daten_loeschen(AUFBEWAHRUNG_TAGE)
        if any(anzahl.values()):
            logger.info("Aufbewahrung: gelöscht %s", anzahl)
    except Exception as e:  # noqa: BLE001 – Aufräumen darf den Betrieb nie stören
        logger.error("Aufbewahrung fehlgeschlagen: %s", e)


async def _aufraeumen_schleife() -> None:
    loop = asyncio.get_running_loop()
    while True:
        await loop.run_in_executor(None, _aufraeumen)
        await asyncio.sleep(AUFRAEUMEN_INTERVALL)


@app.on_event("startup")
async def _startup() -> None:
    db.init()
    # Referenz halten, sonst kann der Garbage Collector die Aufgabe einsammeln.
    app.state.aufraeumen = asyncio.create_task(_aufraeumen_schleife())


@app.exception_handler(auth.NichtAngemeldet)
async def _nicht_angemeldet(request: Request, exc: auth.NichtAngemeldet):
    return RedirectResponse(url="/admin/login", status_code=303)


def _zugang_erlaubt(request: Request) -> bool:
    """True, wenn der Besucher den Chat nutzen darf.

    Im Modus 'oeffentlich' immer; im Modus 'einladung' nur mit gesetztem
    ``zugang_ok`` im Session-Cookie (per gültigem ``?z=<token>`` auf ``GET /``)."""
    if settings.zugangsmodus() == "oeffentlich":
        return True
    return bool(request.session.get("zugang_ok"))


def _client_ip(request: Request) -> str:
    """Echte Client-IP hinter nginx.

    X-Real-IP zuerst: nginx setzt es auf $remote_addr und überschreibt dabei, was der
    Client mitschickt. Beim X-Forwarded-For hängt nginx ($proxy_add_x_forwarded_for) nur
    hinten an – der **erste** Eintrag stammt vom Client und ist frei wählbar, damit liesse
    sich jedes Limit pro IP umgehen. Darum dort den letzten Eintrag nehmen."""
    ip = request.headers.get("x-real-ip")
    if ip:
        return ip.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unbekannt"


def _zugangslink_gueltig(row: dict | None) -> bool:
    if row is None or row.get("deaktiviert"):
        return False
    gueltig_bis = row.get("gueltig_bis")
    if gueltig_bis and gueltig_bis < db._now():
        return False
    return True


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if settings.zugangsmodus() == "einladung":
        # Bei JEDEM Besuch der Startseite ist ein gültiger Einladungslink (?z=<token>) nötig.
        # Ein früher gesetztes zugang_ok genügt allein nicht mehr, um die Chat-Seite zu sehen –
        # ohne gültigen Token gibt es immer die Hinweisseite. (zugang_ok bleibt im Cookie, damit
        # die Endpunkte eines bereits laufenden Gesprächs im offenen Tab nicht abbrechen.)
        token = (request.query_params.get("z") or "").strip()
        link = db.get_zugangslink(token) if token else None
        if token and _zugangslink_gueltig(link):
            request.session["zugang_ok"] = True
            # Empfängerdaten in die Session: der Agent spricht persönlich an und fragt
            # nicht erneut nach der E-Mail (an die der Link ging).
            request.session["kontakt"] = {
                "anrede": link.get("anrede") or "",
                "name": link.get("name") or "",
                "email": link.get("empfaenger_email") or "",
            }
            db.touch_zugangslink(token)
        elif request.session.get("selbst_verifiziert"):
            # Per Self-Service (E-Mail-Code) freigeschalteter Besucher: bleibt für diese
            # Session zugelassen, ohne bei jedem Reload erneut einen Token zu brauchen.
            pass
        else:
            # Bewusst Status 200: die Hinweisseite ist die reguläre Startseite für alle,
            # die ohne Link kommen (mit Self-Service-Formular), kein Fehlerfall. Ein 403
            # gilt Crawlern als nicht erreichbare Seite – OAI-AdsBot lehnt die Zielseite
            # einer ChatGPT-Anzeige dann ab.
            return templates.TemplateResponse(
                "einladung.html",
                {"request": request, "selbst_zugang": settings.selbst_zugang_aktiv()},
            )
    kontakt = request.session.get("kontakt") or {}
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "homepage_url": HOMEPAGE_URL,
         "begruessung": agent.begruessung(kontakt)},
    )


@app.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request):
    return templates.TemplateResponse("impressum.html", {"request": request})


# Browser fragen /favicon.ico und iOS /apple-touch-icon.png unabhängig von den
# <link>-Tags an der Wurzel an. Beide liegen wie robots.txt vor dem Zugangs-Gate.
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/img/favicon.ico", media_type="image/x-icon")


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon():
    return FileResponse("static/img/apple-touch-icon.png", media_type="image/png")


_ROBOTS_REGELN = "Allow: /\nDisallow: /chat\nDisallow: /freigabe\nDisallow: /admin\n"
# OpenAI verlangt für ChatGPT Ads eine eigene Gruppe für OAI-AdsBot und empfiehlt
# zusätzlich OAI-SearchBot. Eine benannte Gruppe ersetzt die *-Gruppe vollständig –
# darum stehen die Regeln je Gruppe, sonst hätten die beiden Zugriff auf /chat & Co.
_ROBOTS_AGENTEN = ("OAI-AdsBot", "OAI-SearchBot", "*")


@app.get("/robots.txt", response_class=Response)
async def robots():
    gruppen = "\n".join(f"User-agent: {a}\n{_ROBOTS_REGELN}" for a in _ROBOTS_AGENTEN)
    content = f"{gruppen}\nSitemap: https://offerte.ralfwbalz.ch/sitemap.xml\n"
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
async def sitemap():
    # Nur die öffentlichen Seiten: /chat, /freigabe und /admin sind in der
    # robots.txt gesperrt, die Chat-Seite selbst ist ohne Zugang gar nicht erreichbar.
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://offerte.ralfwbalz.ch/</loc>
    <changefreq>monthly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://offerte.ralfwbalz.ch/kurzanfrage</loc>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://offerte.ralfwbalz.ch/impressum</loc>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
</urlset>
"""
    return Response(content=content, media_type="application/xml")


def _selbst_zugang_offen() -> bool:
    """Self-Service ist nur im Einladungsmodus und bei aktivem Schalter nutzbar."""
    return settings.zugangsmodus() == "einladung" and settings.selbst_zugang_aktiv()


# Bewusst eng: keine Leerzeichen, Zeilenumbrüche, Anführungszeichen oder spitzen Klammern.
# Die Adresse landet in Mail-Kopfzeilen (To/Reply-To) und im SMTP-Dialog; Pythons
# email-Paket würde eine eingeschleuste Kopfzeile zwar abweisen, aber erst beim Senden.
_EMAIL_MUSTER = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def _email_plausibel(email: str) -> bool:
    return len(email) <= 254 and _EMAIL_MUSTER.fullmatch(email) is not None


def _neuer_code() -> str:
    """6-stelliger Code aus dem kryptografischen Zufallsgenerator (nicht vorhersagbar)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_stimmt(eingabe: str, erwartet: str) -> bool:
    return hmac.compare_digest(eingabe.encode(), erwartet.encode())


@app.post("/zugang/code")
async def zugang_code(request: Request, anrede: str = Form(""), name: str = Form(""),
                      email: str = Form(""), website: str = Form("")):
    """Self-Service: fordert einen 6-stelligen Zugangscode per E-Mail an."""
    if not _selbst_zugang_offen():
        return JSONResponse({"error": "Self-Service-Zugang ist nicht aktiv."}, status_code=403)
    # Honeypot: still als Erfolg quittieren, aber nichts senden.
    if website.strip():
        return JSONResponse({"ok": True})

    email = email.strip().lower()
    name, anrede = name.strip(), anrede.strip()
    if not _email_plausibel(email) or not name:
        return JSONResponse(
            {"error": "Bitte gültige E-Mail-Adresse und Namen angeben."}, status_code=400)

    ip = _client_ip(request)
    if db.count_recent_zugang_codes_for_ip(ip, RATE_WINDOW_SECONDS) >= MAX_ZUGANG_CODES_PER_IP:
        return JSONResponse(
            {"error": "Zu viele Anfragen von dieser Verbindung. Bitte später erneut versuchen."},
            status_code=429,
        )
    letzter = db.letzter_code_zeitpunkt(email)
    if letzter and (datetime.now() - datetime.fromisoformat(letzter)).total_seconds() < OTP_RESEND_SEKUNDEN:
        return JSONResponse(
            {"error": "Es wurde gerade ein Code gesendet. Bitte kurz warten."}, status_code=429)

    code = _neuer_code()
    gueltig_bis = (datetime.now() + timedelta(minutes=OTP_TTL_MIN)).isoformat(timespec="seconds")
    db.create_zugang_code(email, anrede, name, code, ip, gueltig_bis)
    try:
        offer.send_zugang_code(email, code, anrede, name, ttl_min=OTP_TTL_MIN)
    except Exception as e:  # noqa: BLE001
        logger.error("Zugangscode-Mail fehlgeschlagen: %s", e)
        return JSONResponse(
            {"error": "Der Code konnte nicht versendet werden. Bitte später erneut versuchen."},
            status_code=502,
        )
    return JSONResponse({"ok": True})


@app.post("/zugang/verify")
async def zugang_verify(request: Request, email: str = Form(""), code: str = Form("")):
    """Self-Service: prüft den Code und schaltet die Session frei."""
    if not _selbst_zugang_offen():
        return JSONResponse({"error": "Self-Service-Zugang ist nicht aktiv."}, status_code=403)

    email = email.strip().lower()
    code = code.strip()
    eintrag = db.get_aktiver_zugang_code(email)
    if eintrag is None:
        return JSONResponse(
            {"error": "Kein gültiger Code. Bitte fordern Sie einen neuen an."}, status_code=400)
    if eintrag["gueltig_bis"] < db._now():
        return JSONResponse(
            {"error": "Der Code ist abgelaufen. Bitte fordern Sie einen neuen an."}, status_code=400)
    if eintrag["versuche"] >= OTP_MAX_VERSUCHE:
        db.mark_zugang_code_verifiziert(eintrag["id"])  # verbraucht/invalidiert
        return JSONResponse(
            {"error": "Zu viele Fehlversuche. Bitte fordern Sie einen neuen Code an."},
            status_code=400,
        )
    if not _code_stimmt(code, eintrag["code"]):
        db.increment_zugang_code_versuche(eintrag["id"])
        rest = OTP_MAX_VERSUCHE - (eintrag["versuche"] + 1)
        if rest <= 0:
            return JSONResponse(
                {"error": "Zu viele Fehlversuche. Bitte fordern Sie einen neuen Code an."},
                status_code=400,
            )
        return JSONResponse(
            {"error": f"Falscher Code. Noch {rest} Versuch(e)."}, status_code=400)

    # Treffer: Code verbrauchen, Session freischalten, Kontakt für die Ansprache merken.
    db.mark_zugang_code_verifiziert(eintrag["id"])
    request.session["zugang_ok"] = True
    request.session["selbst_verifiziert"] = True
    request.session["kontakt"] = {
        "anrede": eintrag.get("anrede") or "",
        "name": eintrag.get("name") or "",
        "email": email,
    }
    try:
        offer.notify_selbst_zugang(email, eintrag.get("anrede") or "", eintrag.get("name") or "")
    except Exception as e:  # noqa: BLE001
        logger.error("Info-Mail Self-Service-Zugang fehlgeschlagen: %s", e)
    return JSONResponse({"ok": True, "redirect": "/"})


# ------------------------------------------------------------ Kurzanfrage -----
# Unabhängig vom Zugangsmodus erreichbar. Die Anfrage geht erst an Ralf, wenn der
# Besucher seine E-Mail-Adresse per Code bestätigt hat; der Code schaltet den Chat
# nicht frei (eigene Tabelle kurzanfragen, keine Session-Flags).

@app.get("/kurzanfrage", response_class=HTMLResponse)
async def kurzanfrage(request: Request):
    return templates.TemplateResponse(
        "kurzanfrage.html",
        {"request": request, "themen": KURZANFRAGE_THEMEN,
         "max_zeichen": KURZANFRAGE_MAX_ZEICHEN},
    )


@app.post("/kurzanfrage/code")
async def kurzanfrage_code(request: Request, thema: str = Form(""), text: str = Form(""),
                           email: str = Form(""), website: str = Form("")):
    """Speichert die Kurzanfrage und schickt den Bestätigungscode an den Besucher."""
    # Honeypot: still als Erfolg quittieren, aber nichts senden.
    if website.strip():
        return JSONResponse({"ok": True})

    email = email.strip().lower()
    thema, text = thema.strip(), text.strip()
    if thema not in KURZANFRAGE_THEMEN:
        return JSONResponse({"error": "Bitte ein Thema wählen."}, status_code=400)
    if not text:
        return JSONResponse({"error": "Bitte beschreiben Sie kurz, was Sie suchen."},
                            status_code=400)
    if len(text) > KURZANFRAGE_MAX_ZEICHEN:
        return JSONResponse(
            {"error": f"Bitte fassen Sie sich kürzer (max. {KURZANFRAGE_MAX_ZEICHEN} Zeichen)."},
            status_code=400)
    if not _email_plausibel(email):
        return JSONResponse({"error": "Bitte eine gültige E-Mail-Adresse angeben."},
                            status_code=400)

    ip = _client_ip(request)
    if db.count_recent_kurzanfragen_for_ip(ip, RATE_WINDOW_SECONDS) >= MAX_ZUGANG_CODES_PER_IP:
        return JSONResponse(
            {"error": "Zu viele Anfragen von dieser Verbindung. Bitte später erneut versuchen."},
            status_code=429,
        )
    letzter = db.letzte_kurzanfrage_zeitpunkt(email)
    if letzter and (datetime.now() - datetime.fromisoformat(letzter)).total_seconds() < OTP_RESEND_SEKUNDEN:
        return JSONResponse(
            {"error": "Es wurde gerade ein Code gesendet. Bitte kurz warten."}, status_code=429)

    code = _neuer_code()
    gueltig_bis = (datetime.now() + timedelta(minutes=OTP_TTL_MIN)).isoformat(timespec="seconds")
    db.create_kurzanfrage(thema, text, email, code, ip, gueltig_bis)
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, offer.send_kurzanfrage_code, email, code, OTP_TTL_MIN)
    except Exception as e:  # noqa: BLE001
        logger.error("Kurzanfrage-Code-Mail fehlgeschlagen: %s", e)
        return JSONResponse(
            {"error": "Der Code konnte nicht versendet werden. Bitte später erneut versuchen."},
            status_code=502,
        )
    return JSONResponse({"ok": True})


@app.post("/kurzanfrage/bestaetigen")
async def kurzanfrage_bestaetigen(email: str = Form(""), code: str = Form("")):
    """Prüft den Code und leitet die Kurzanfrage an Ralf weiter."""
    email = email.strip().lower()
    code = code.strip()
    anfrage = db.get_offene_kurzanfrage(email)
    if anfrage is None:
        return JSONResponse(
            {"error": "Kein gültiger Code. Bitte fordern Sie einen neuen an."}, status_code=400)
    if anfrage["gueltig_bis"] < db._now():
        return JSONResponse(
            {"error": "Der Code ist abgelaufen. Bitte fordern Sie einen neuen an."}, status_code=400)
    if anfrage["versuche"] >= OTP_MAX_VERSUCHE:
        return JSONResponse(
            {"error": "Zu viele Fehlversuche. Bitte fordern Sie einen neuen Code an."},
            status_code=400,
        )
    if not _code_stimmt(code, anfrage["code"]):
        db.increment_kurzanfrage_versuche(anfrage["id"])
        rest = OTP_MAX_VERSUCHE - (anfrage["versuche"] + 1)
        if rest <= 0:
            return JSONResponse(
                {"error": "Zu viele Fehlversuche. Bitte fordern Sie einen neuen Code an."},
                status_code=400,
            )
        return JSONResponse(
            {"error": f"Falscher Code. Noch {rest} Versuch(e)."}, status_code=400)

    # Erst senden, dann als bestätigt markieren: scheitert SMTP, bleibt der Code gültig
    # und der Besucher kann es gleich nochmals versuchen, ohne alles neu einzugeben.
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, offer.send_kurzanfrage_an_ralf, anfrage["thema"], anfrage["text"], email)
    except Exception as e:  # noqa: BLE001
        logger.error("Kurzanfrage an Ralf fehlgeschlagen: %s", e)
        return JSONResponse(
            {"error": "Die Anfrage konnte nicht übermittelt werden. Bitte gleich nochmals versuchen."},
            status_code=502,
        )
    db.mark_kurzanfrage_bestaetigt(anfrage["id"])
    return JSONResponse({"ok": True})


@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    session_id = (data.get("session_id") or "").strip()
    text = (data.get("text") or "").strip()
    honeypot = (data.get("website") or "").strip()
    # Button „Gespräch beenden“: Offerte mit dem bisherigen Stand erzwingen.
    beenden = data.get("beenden") is True

    # Honeypot: still als Erfolg quittieren, aber nichts tun.
    if honeypot:
        return StreamingResponse(_single_event({"type": "done"}), media_type="text/event-stream")

    if not session_id or not text:
        return JSONResponse({"error": "session_id und text erforderlich."}, status_code=400)

    if not _zugang_erlaubt(request):
        return JSONResponse({"error": "Kein Zugang."}, status_code=403)

    ip = _client_ip(request)
    is_new = not db.session_exists(session_id)

    if beenden:
        # Beenden setzt ein laufendes Gespräch voraus (sonst gibt es nichts zusammenzufassen)
        # und erzeugt höchstens eine Offerte pro Gespräch.
        if is_new:
            return JSONResponse({"error": "Es läuft noch kein Gespräch."}, status_code=400)
        if db.get_offer_by_session(session_id):
            return StreamingResponse(_single_event({"type": "done"}),
                                     media_type="text/event-stream")

    if is_new:
        if db.count_recent_sessions_for_ip(ip, RATE_WINDOW_SECONDS) >= MAX_SESSIONS_PER_IP:
            return JSONResponse(
                {"error": "Zu viele Gespräche von dieser Verbindung. Bitte später erneut versuchen."},
                status_code=429,
            )
        db.create_session(session_id, ip)
        # Die feste Eröffnungs-Begrüssung (clientseitig bereits angezeigt und vorgelesen) als
        # erste Assistenten-Nachricht ablegen, damit der Agent sie nicht wiederholt und nahtlos
        # an die Antwort des Kunden anschliesst.
        db.add_message(session_id, "assistant", agent.begruessung(request.session.get("kontakt") or {}))

    # Kombinierte Kostenbremse: Turns ODER Token. Was zuerst die Hard-Schwelle erreicht,
    # beendet das Gespräch; die Soft-Schwelle lässt den Agenten sanft zum Abschluss überleiten.
    turns = db.count_messages(session_id, "user")
    tokens_in, tokens_out = db.get_session_usage(session_id)
    tokens_gesamt = tokens_in + tokens_out
    # Beenden darf auch nach erreichtem Hard-Limit noch eine Runde: sonst ginge ein langes
    # Gespräch, das am Limit abbricht, ohne Offerte verloren.
    if not beenden and (turns >= settings.max_turns() or tokens_gesamt >= settings.max_tokens()):
        return StreamingResponse(
            _single_event({
                "type": "limit",
                "text": "Das Gespräch hat die maximale Länge erreicht. "
                        "Bitte laden Sie die Seite neu, um ein neues Gespräch zu beginnen.",
            }),
            media_type="text/event-stream",
        )
    wind_down = turns >= settings.soft_turns() or tokens_gesamt >= settings.soft_tokens()

    db.add_message(session_id, "user", text)
    history = db.get_history(session_id)
    kontakt = request.session.get("kontakt") or {}

    def event_stream():
        try:
            for ev in agent.stream_reply(session_id, history, wind_down=wind_down,
                                         kontakt=kontakt, beenden=beenden):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.error("Chat-Stream-Fehler: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Es ist ein Fehler aufgetreten.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/freigabe/{token}", response_class=HTMLResponse)
async def freigabe(request: Request, token: str):
    row = db.get_offer_by_token(token)
    if row is None:
        return templates.TemplateResponse(
            "freigabe.html",
            {"request": request, "status": "unbekannt"},
            status_code=404,
        )

    already = bool(row.get("released_at"))
    try:
        offer.release_to_customer(token)
        status = "bereits" if already else "gesendet"
    except Exception as e:  # noqa: BLE001
        logger.error("Freigabe-Versand fehlgeschlagen: %s", e)
        status = "fehler"

    data = json.loads(row["data_json"])
    return templates.TemplateResponse(
        "freigabe.html",
        {"request": request, "status": status, "kunde": data.get("kunde", {}),
         "titel": data.get("projekt_titel", "")},
    )


@app.post("/chat/stt")
async def chat_stt(request: Request, session_id: str = Form(""), audio: UploadFile = File(...)):
    """Sprache → Text (OpenAI Whisper). Nur für bereits gestartete Gespräche –
    so hängen die kostenpflichtigen Audio-Calls am selben Rate-/Turn-Schutz wie der Chat."""
    if not _zugang_erlaubt(request):
        return JSONResponse({"error": "zugang"}, status_code=403)
    if not session_id or not db.session_exists(session_id):
        return JSONResponse({"error": "session"}, status_code=403)
    audio_bytes = await audio.read()
    try:
        text = voice.transcribe(audio_bytes, audio.filename or "aufnahme.webm")
    except Exception as e:  # noqa: BLE001
        logger.error("STT fehlgeschlagen: %s", e)
        return JSONResponse({"error": "stt"}, status_code=502)
    return JSONResponse({"text": text})


@app.post("/chat/tts")
async def chat_tts(request: Request):
    """Text → MP3-Audio (OpenAI TTS). Nur für bereits gestartete Gespräche."""
    if not _zugang_erlaubt(request):
        return JSONResponse({"error": "zugang"}, status_code=403)
    data = await request.json()
    session_id = (data.get("session_id") or "").strip()
    text = (data.get("text") or "").strip()
    if not session_id or not db.session_exists(session_id):
        return JSONResponse({"error": "session"}, status_code=403)
    if not text:
        return JSONResponse({"error": "leer"}, status_code=400)
    try:
        audio = voice.synthesize(text)
    except Exception as e:  # noqa: BLE001
        logger.error("TTS fehlgeschlagen: %s", e)
        return JSONResponse({"error": "tts"}, status_code=502)
    return Response(content=audio, media_type="audio/mpeg")


_begruessung_audio_cache: dict[str, bytes] = {}


@app.get("/begruessung.mp3")
async def begruessung_audio(request: Request):
    """Vorab-Synthese der festen Eröffnungs-Begrüssung, damit die Stimme beim Gesprächsstart
    ohne LLM-Latenz sofort einsetzt. Wie /chat/tts an den Zugang gebunden; das Ergebnis wird je
    Begrüssungstext gecacht, sodass wiederholte Aufrufe keine zusätzlichen TTS-Kosten erzeugen."""
    if not _zugang_erlaubt(request):
        return JSONResponse({"error": "zugang"}, status_code=403)
    kontakt = request.session.get("kontakt") or {}
    text = agent.begruessung(kontakt)
    audio = _begruessung_audio_cache.get(text)
    if audio is None:
        try:
            audio = voice.synthesize(text)
        except Exception as e:  # noqa: BLE001
            logger.error("Begrüssungs-TTS fehlgeschlagen: %s", e)
            return JSONResponse({"error": "tts"}, status_code=502)
        if len(_begruessung_audio_cache) < 50:
            _begruessung_audio_cache[text] = audio
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


def _single_event(ev: dict):
    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
