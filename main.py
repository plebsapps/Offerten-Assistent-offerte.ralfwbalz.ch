"""FastAPI-App für den Offerten-Assistenten (offerte.ralfwbalz.ch).

Sprache läuft serverseitig über OpenAI (Whisper-STT + neuronales TTS, siehe
``voice.py``). Hier: Auslieferung der UI, der SSE-Chat-Endpunkt zum Streamen der
Claude-Antworten, die Audio-Endpunkte ``/chat/stt`` und ``/chat/tts`` sowie der
Freigabe-Endpunkt, über den Ralf die fertige Offerte an den Auftraggeber freigibt.
"""
import os
import json
import random
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse, RedirectResponse
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
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
app.include_router(routes_admin.router)

MAX_SESSIONS_PER_IP = int(os.environ.get("MAX_SESSIONS_PER_IP", "5"))
RATE_WINDOW_SECONDS = 3600  # 1 Stunde
HOMEPAGE_URL = os.environ.get("HOMEPAGE_URL", "https://ralfwbalz.ch")

# Self-Service-Zugang (E-Mail-Code/OTP)
OTP_TTL_MIN = int(os.environ.get("OTP_TTL_MIN", "10"))          # Gültigkeit des Codes
OTP_MAX_VERSUCHE = int(os.environ.get("OTP_MAX_VERSUCHE", "3"))  # Eingabeversuche je Code
OTP_RESEND_SEKUNDEN = int(os.environ.get("OTP_RESEND_SEKUNDEN", "60"))  # Sperre erneuter Anfragen
MAX_ZUGANG_CODES_PER_IP = int(os.environ.get("MAX_ZUGANG_CODES_PER_IP", "5"))  # je Stunde/IP


@app.on_event("startup")
def _startup() -> None:
    db.init()


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
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
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
            return templates.TemplateResponse(
                "einladung.html",
                {"request": request, "selbst_zugang": settings.selbst_zugang_aktiv()},
                status_code=403,
            )
    return templates.TemplateResponse(
        "index.html", {"request": request, "homepage_url": HOMEPAGE_URL}
    )


@app.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request):
    return templates.TemplateResponse("impressum.html", {"request": request})


@app.get("/robots.txt", response_class=Response)
async def robots():
    content = "User-agent: *\nAllow: /\nDisallow: /chat\nDisallow: /freigabe\nDisallow: /admin\n"
    return Response(content=content, media_type="text/plain")


def _selbst_zugang_offen() -> bool:
    """Self-Service ist nur im Einladungsmodus und bei aktivem Schalter nutzbar."""
    return settings.zugangsmodus() == "einladung" and settings.selbst_zugang_aktiv()


def _email_plausibel(email: str) -> bool:
    return "@" in email and "." in email.split("@")[-1] and len(email) <= 254


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

    code = f"{random.randint(0, 999999):06d}"
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
    if code != eintrag["code"]:
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


@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    session_id = (data.get("session_id") or "").strip()
    text = (data.get("text") or "").strip()
    honeypot = (data.get("website") or "").strip()

    # Honeypot: still als Erfolg quittieren, aber nichts tun.
    if honeypot:
        return StreamingResponse(_single_event({"type": "done"}), media_type="text/event-stream")

    if not session_id or not text:
        return JSONResponse({"error": "session_id und text erforderlich."}, status_code=400)

    if not _zugang_erlaubt(request):
        return JSONResponse({"error": "Kein Zugang."}, status_code=403)

    ip = _client_ip(request)
    is_new = not db.session_exists(session_id)

    if is_new:
        if db.count_recent_sessions_for_ip(ip, RATE_WINDOW_SECONDS) >= MAX_SESSIONS_PER_IP:
            return JSONResponse(
                {"error": "Zu viele Gespräche von dieser Verbindung. Bitte später erneut versuchen."},
                status_code=429,
            )
        db.create_session(session_id, ip)

    # Kombinierte Kostenbremse: Turns ODER Token. Was zuerst die Hard-Schwelle erreicht,
    # beendet das Gespräch; die Soft-Schwelle lässt den Agenten sanft zum Abschluss überleiten.
    turns = db.count_messages(session_id, "user")
    tokens_in, tokens_out = db.get_session_usage(session_id)
    tokens_gesamt = tokens_in + tokens_out
    if turns >= settings.max_turns() or tokens_gesamt >= settings.max_tokens():
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
            for ev in agent.stream_reply(session_id, history, wind_down=wind_down, kontakt=kontakt):
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


def _single_event(ev: dict):
    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
