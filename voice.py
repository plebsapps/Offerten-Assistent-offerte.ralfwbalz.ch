"""Sprach-Ein-/Ausgabe über OpenAI (Whisper-STT + neuronales TTS).

Der Browser nimmt Audio per ``MediaRecorder`` auf und schickt es an ``/chat/stt``;
die KI-Antwort wird über ``/chat/tts`` in natürliche Sprache umgewandelt. Beides
läuft serverseitig gegen die OpenAI-API – die Web Speech API des Browsers wird
nicht mehr gebraucht (funktioniert dadurch auch in Firefox/Safari).

Der Offerten-Assistent ist durchgängig deutsch; Modell und Stimme sind über die
Umgebung konfigurierbar, Default-Stimme ist ``nova``. Audio kommt als MP3 zurück.
"""
import os

import httpx

STT_MODEL = os.environ.get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "nova")
# Sprechgeschwindigkeit (0.25–4.0, 1.0 = normal).
TTS_SPEED = float(os.environ.get("OPENAI_TTS_SPEED", "1.2"))

# OpenAI-TTS hat ein Eingabelimit (~4096 Zeichen) – defensiv kürzen.
MAX_TTS_ZEICHEN = 4000


def _key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY fehlt")
    return key


def transcribe(audio: bytes, filename: str) -> str:
    """Audio → Text (Spracherkennung, deutsch)."""
    with httpx.Client(timeout=60) as c:
        r = c.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {_key()}"},
            files={"file": (filename or "aufnahme.webm", audio)},
            data={"model": STT_MODEL,
                  "language": "de",
                  "response_format": "json"},
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip()


def synthesize(text: str) -> bytes:
    """Text → MP3-Audio (Sprachausgabe, deutsch)."""
    with httpx.Client(timeout=60) as c:
        r = c.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {_key()}",
                     "Content-Type": "application/json"},
            json={"model": TTS_MODEL,
                  "voice": TTS_VOICE,
                  "input": text[:MAX_TTS_ZEICHEN],
                  "speed": TTS_SPEED,
                  "response_format": "mp3"},
        )
        r.raise_for_status()
        return r.content
