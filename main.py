"""FastAPI-App für den Offerten-Assistenten (offerte.ralfwbalz.ch).

Sprache läuft serverseitig über OpenAI (Whisper-STT + neuronales TTS, siehe
``voice.py``). Hier: Auslieferung der UI, der SSE-Chat-Endpunkt zum Streamen der
Claude-Antworten, die Audio-Endpunkte ``/chat/stt`` und ``/chat/tts`` sowie der
Freigabe-Endpunkt, über den Ralf die fertige Offerte an den Auftraggeber freigibt.
"""
import os
import json
import logging

from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import db
import agent
import offer
import voice

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

MAX_SESSIONS_PER_IP = int(os.environ.get("MAX_SESSIONS_PER_IP", "5"))
MAX_TURNS_PER_SESSION = int(os.environ.get("MAX_TURNS_PER_SESSION", "40"))
RATE_WINDOW_SECONDS = 3600  # 1 Stunde


@app.on_event("startup")
def _startup() -> None:
    db.init()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unbekannt"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request):
    return templates.TemplateResponse("impressum.html", {"request": request})


@app.get("/robots.txt", response_class=Response)
async def robots():
    content = "User-agent: *\nAllow: /\nDisallow: /chat\nDisallow: /freigabe\n"
    return Response(content=content, media_type="text/plain")


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

    ip = _client_ip(request)
    is_new = not db.session_exists(session_id)

    if is_new:
        if db.count_recent_sessions_for_ip(ip, RATE_WINDOW_SECONDS) >= MAX_SESSIONS_PER_IP:
            return JSONResponse(
                {"error": "Zu viele Gespräche von dieser Verbindung. Bitte später erneut versuchen."},
                status_code=429,
            )
        db.create_session(session_id, ip)

    if db.count_messages(session_id, "user") >= MAX_TURNS_PER_SESSION:
        return StreamingResponse(
            _single_event({
                "type": "limit",
                "text": "Das Gespräch hat die maximale Länge erreicht. "
                        "Bitte laden Sie die Seite neu, um ein neues Gespräch zu beginnen.",
            }),
            media_type="text/event-stream",
        )

    db.add_message(session_id, "user", text)
    history = db.get_history(session_id)

    def event_stream():
        try:
            for ev in agent.stream_reply(session_id, history):
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
async def chat_stt(session_id: str = Form(""), audio: UploadFile = File(...)):
    """Sprache → Text (OpenAI Whisper). Nur für bereits gestartete Gespräche –
    so hängen die kostenpflichtigen Audio-Calls am selben Rate-/Turn-Schutz wie der Chat."""
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
