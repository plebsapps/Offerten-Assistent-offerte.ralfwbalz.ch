"""OpenAI-Variante des Beratungs-Agenten (ChatGPT, Modell gpt-4.1).

Spiegelt die Event-Schnittstelle von ``agent.stream_reply`` (token / offer_created /
done) und teilt sich System-Prompt, Wind-Down-Hinweis und Tool-Schema mit ``agent``.
Wird über ``settings.ki_anbieter() == "openai"`` aus ``agent.stream_reply`` aufgerufen.
"""
import json
import logging

import openai

import db
import offer
import agent  # bereits geladen, wenn dieser Dispatch greift – kein Zirkelbezug

logger = logging.getLogger(__name__)

MODEL = "gpt-4.1"

# Tool-Schema von Claude wiederverwenden, nur in das OpenAI-Function-Format hüllen.
OPENAI_TOOL = {
    "type": "function",
    "function": {
        "name": agent.OFFER_TOOL["name"],
        "description": agent.OFFER_TOOL["description"],
        "parameters": agent.OFFER_TOOL["input_schema"],
    },
}

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI()  # liest OPENAI_API_KEY aus der Umgebung
    return _client


def stream_reply(session_id: str, history: list[dict], wind_down: bool = False):
    """Streamt die Agenten-Antwort als Event-Dicts (gleiche Schnittstelle wie agent)."""
    system_prompt = (agent.SYSTEM_PROMPT + agent.WIND_DOWN_HINWEIS) if wind_down else agent.SYSTEM_PROMPT
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]

    final_text_parts: list[str] = []
    tokens_in = tokens_out = 0

    for _ in range(agent.MAX_TOOL_ROUNDS + 1):
        round_text: list[str] = []
        # tool_calls über die Chunks hinweg zusammensetzen (Index → {id, name, args}).
        tool_calls: dict[int, dict] = {}
        finish_reason = None

        stream = _get_client().chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=[OPENAI_TOOL],
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                tokens_in += getattr(chunk.usage, "prompt_tokens", 0) or 0
                tokens_out += getattr(chunk.usage, "completion_tokens", 0) or 0
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(delta, "content", None):
                round_text.append(delta.content)
                yield {"type": "token", "text": delta.content}
            for tc in (getattr(delta, "tool_calls", None) or []):
                slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason

        final_text_parts.extend(round_text)

        if finish_reason != "tool_calls" or not tool_calls:
            break

        # Werkzeug-Aufruf(e) abarbeiten und Ergebnis zurückgeben.
        ordered = [tool_calls[i] for i in sorted(tool_calls)]
        messages.append({
            "role": "assistant",
            "content": "".join(round_text) or None,
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                for c in ordered
            ],
        })
        for c in ordered:
            if c["name"] != agent.OFFER_TOOL["name"]:
                result_text = "Unbekanntes Werkzeug."
            else:
                try:
                    args = json.loads(c["args"] or "{}")
                    offer.create_and_send(session_id, args)
                    yield {"type": "offer_created"}
                    result_text = "Offerte wurde erstellt und an Ralf zur persönlichen Prüfung gesendet."
                except Exception as e:  # noqa: BLE001
                    logger.error("Offerten-Erstellung fehlgeschlagen: %s", e)
                    result_text = "Die Offerte konnte technisch nicht erstellt werden."
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result_text})
        # Schleife läuft weiter, damit der Agent die mündliche Abschlussbestätigung gibt.

    if tokens_in or tokens_out:
        db.add_session_usage(session_id, tokens_in, tokens_out)

    final_text = "".join(final_text_parts).strip()
    if final_text:
        db.add_message(session_id, "assistant", final_text)
    yield {"type": "done"}
