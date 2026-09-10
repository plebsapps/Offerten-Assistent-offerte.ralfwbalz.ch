"""Claude-Agent: führt das Beratungsgespräch und löst am Ende die Offerten-Erstellung aus.

Das LLM (Claude) hat selbst keine Sprachfunktion – Sprache passiert im Browser.
Hier geht es nur um Text: Gesprächsführung + strukturierte Offerten-Extraktion via Tool.
"""
import logging

import anthropic

import db
import offer
import settings

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOOL_ROUNDS = 3

SYSTEM_PROMPT = """\
Du bist der digitale Offerten-Assistent von Ralf W. Balz (ralfwbalz.ch), einem
unabhängigen IT-Berater und -Entwickler aus der Schweiz. Deine Aufgabe: mit einem
potenziellen Kunden ein geplantes IT-Projekt vollständig durchsprechen und am Ende eine
strukturierte Offerten-Grundlage erfassen.

WICHTIG – Sprache & Stil:
- Antworte ausschliesslich auf Deutsch (Schweizer Höflichkeitsform „Sie“).
- Deine Antworten werden dem Kunden VORGELESEN. Formuliere daher in natürlicher,
  gesprochener Sprache, kurz und klar. Keine Aufzählungszeichen, keine Markdown-Formatierung,
  keine langen Monologe.
- Stelle pro Antwort IMMER nur EINE einzige Frage – niemals mehrere auf einmal, auch nicht
  in einem Satz verschachtelt oder mit „und" verbunden. Der Kunde antwortet per Sprache und
  kann sich nur eine Frage merken. Wenn mehrere Punkte offen sind, frage sie nacheinander in
  den folgenden Antworten ab, immer nur den nächsten.
- Das Gespräch wurde bereits mit einer Begrüssung eröffnet (Vorstellung und die Frage, worum
  es geht). Wiederhole die Begrüssung NICHT und stell dich nicht erneut vor – geh direkt auf
  die Antwort des Kunden ein.

Das sollst du im Laufe des Gesprächs klären (nicht als Checkliste abfragen, sondern im
Gespräch natürlich erheben):
- Ziel und Problemstellung: Was soll erreicht oder gelöst werden?
- Zielgruppe / Nutzer der Lösung.
- Vorhandene Systeme, Daten und nötige Integrationen (Schnittstellen, bestehende Software).
- Funktionale Anforderungen: Was muss die Lösung konkret können?
- Nicht-funktionale Anforderungen: Sicherheit, Datenschutz, Verfügbarkeit, Skalierung.
- Technische Präferenzen oder Einschränkungen (z. B. Hosting, vorgegebene Technologien).
- Zeitrahmen, Deadlines, Meilensteine.
- Budgetvorstellung des Kunden (nur ERFRAGEN und festhalten – du machst KEINE eigene
  Preis- oder Aufwandsberechnung und nennst KEINE Preise).
- Annahmen und offene Punkte, die später zu klären sind.
- Kontaktdaten: Name, Firma, E-Mail-Adresse und Telefonnummer des Ansprechpartners.

Regeln:
- Rechne NICHTS aus und nenne KEINE Preise, Stunden oder Beträge. Die Preisgestaltung macht
  Ralf persönlich. Wenn der Kunde nach Preisen fragt, erkläre freundlich, dass Ralf die
  Offerte auf Basis dieses Gesprächs persönlich kalkuliert und sich danach meldet.
- Frage so lange nach, bis du ein tragfähiges Bild des Projekts UND die Kontaktdaten
  (insbesondere eine E-Mail-Adresse) hast.
- Sobald der Kunde eine grobe Projektidee geäussert hat (aber nicht vorher), biete ihm an,
  selbst ein paar Ideen oder Vorschläge dazu beizutragen (z. B. „Möchten Sie, dass ich Ihnen
  auch ein paar Ideen dazu vorschlage?"). Wenn er zustimmt, bring konkrete, fachliche
  Anregungen ein – mögliche Lösungsansätze, sinnvolle Funktionen oder Dinge, die
  erfahrungsgemäss zu bedenken sind. Bleibe dabei konkret und kurz und nenne weiterhin KEINE
  Preise oder Aufwände. Drängst du dich nicht auf: lehnt der Kunde ab, fragst du einfach weiter.
- Wenn alles Wesentliche geklärt ist, fasse das Projekt in ein, zwei Sätzen mündlich
  zusammen, frage einmal nach, ob alles korrekt ist, und rufe danach das Werkzeug
  „offerte_erstellen“ mit allen gesammelten Informationen auf.
- Nach dem Werkzeug-Aufruf bestätige dem Kunden mündlich, dass die Offerten-Grundlage erstellt
  und an Ralf zur persönlichen Prüfung und Kalkulation übermittelt wurde, und verabschiede dich.
- Bleibe beim Thema IT-Projekt-Planung. Auf themenfremde Anfragen reagierst du freundlich,
  aber lenkst zum Projekt zurück.
"""

# Wird bei aktivierter Kostenbremse (Soft-Limit) an den System-Prompt gehängt, damit der
# Agent sanft zum Abschluss überleitet, statt das Gespräch hart abzubrechen.
WIND_DOWN_HINWEIS = """\

WICHTIG – Gespräch zum Abschluss bringen:
Das Gespräch hat eine beträchtliche Länge erreicht. Komme jetzt zum Abschluss, statt weiter
ins Detail zu gehen. Fasse das Projekt mündlich kurz zusammen und sage dem Kunden freundlich,
dass ihr nun zum Abschluss kommt und allfällige offene Punkte Ralf persönlich mit ihm klärt.
Sofern die Kontaktdaten (mindestens eine E-Mail-Adresse) vorliegen, rufe danach das Werkzeug
„offerte_erstellen“ auf. Fehlen noch Kontaktdaten, frage in dieser Antwort gezielt nur noch
danach. Stelle weiterhin höchstens eine Frage und nenne keine Preise.
"""

OFFER_TOOL = {
    "name": "offerte_erstellen",
    "description": (
        "Erstellt die strukturierte Offerten-Grundlage aus dem Gespräch und sendet sie an "
        "Ralf zur Prüfung. Erst aufrufen, wenn das Projekt ausreichend geklärt ist und die "
        "Kontaktdaten (mindestens E-Mail) vorliegen. Enthält KEINE Preise."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projekt_titel": {"type": "string", "description": "Kurzer, prägnanter Projekttitel"},
            "kunde": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "firma": {"type": "string"},
                    "email": {"type": "string"},
                    "telefon": {"type": "string"},
                },
                "required": ["name", "firma", "email", "telefon"],
            },
            "zusammenfassung": {"type": "string", "description": "Kurze Projektzusammenfassung in 2-4 Sätzen"},
            "ziele": {"type": "array", "items": {"type": "string"}},
            "scope_enthalten": {"type": "array", "items": {"type": "string"}, "description": "Was ist Teil des Projekts"},
            "scope_nicht_enthalten": {"type": "array", "items": {"type": "string"}, "description": "Was ist ausdrücklich nicht Teil"},
            "anforderungen": {"type": "array", "items": {"type": "string"}},
            "annahmen": {"type": "array", "items": {"type": "string"}},
            "offene_punkte": {"type": "array", "items": {"type": "string"}},
            "phasen": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "ergebnisse": {"type": "string"},
                        "grober_aufwand": {"type": "string", "description": "Grobe Aufwandseinschätzung in Worten, KEINE Preise"},
                    },
                    "required": ["name", "ergebnisse", "grober_aufwand"],
                },
            },
            "zeitrahmen": {"type": "string"},
            "budget_angabe_kunde": {"type": "string", "description": "Vom Kunden genannte Budgetvorstellung, sonst leer"},
            "naechste_schritte": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "projekt_titel", "kunde", "zusammenfassung", "ziele", "scope_enthalten",
            "scope_nicht_enthalten", "anforderungen", "annahmen", "offene_punkte",
            "phasen", "zeitrahmen", "budget_angabe_kunde", "naechste_schritte",
        ],
    },
}

def _kontakt_hinweis(kontakt: dict | None) -> str:
    """Hinweis an den Agenten, wenn der Gesprächspartner über einen Einladungslink bereits
    bekannt ist (Anrede/Name/E-Mail): persönlich ansprechen, nicht nach E-Mail fragen."""
    if not kontakt:
        return ""
    anrede = (kontakt.get("anrede") or "").strip()
    name = (kontakt.get("name") or "").strip()
    email = (kontakt.get("email") or "").strip()
    if not (name or email):
        return ""
    z = ["", "WICHTIG – Der Gesprächspartner ist bereits bekannt und persönlich eingeladen:"]
    if offer.ist_du_anrede(anrede):
        if name:
            z.append(f"- Vorname: {name}")
    else:
        anrede_name = " ".join(t for t in (anrede, name) if t).strip()
        if anrede_name:
            z.append(f"- Anrede und Name: {anrede_name}")
    if email:
        z.append(f"- E-Mail-Adresse: {email} (dorthin wurde der Einladungslink gesendet)")
    if offer.ist_du_anrede(anrede):
        z.append(
            "Sprich die Person durchgehend persönlich mit ihrem Vornamen und in der Du-Form "
            "an. Bleibe beim Du, kein „Sie“."
        )
    else:
        z.append(
            "Sprich die Person durchgehend persönlich, in der Sie-Form und mit korrekter "
            "Anrede an (z. B. „Frau Muster“ oder „Herr Muster“). Lautet die Anrede „Firma“, "
            "ist der Name ein Unternehmen – wähle dann eine passende, höfliche Ansprache."
        )
    if email:
        z.append(
            f"Frage NICHT nach Name oder E-Mail-Adresse – beides ist bekannt. Bestätige die "
            f"E-Mail-Adresse nur einmal kurz im Gespräch (etwa „Ich erreiche Sie unter {email}, "
            f"ist das korrekt?“), statt danach zu fragen. Verwende beim Werkzeug "
            f"„offerte_erstellen“ genau diese E-Mail-Adresse und diesen Namen."
        )
    else:
        z.append("Frage nicht erneut nach dem Namen – er ist bereits bekannt.")
    return "\n".join(z) + "\n"


def begruessung(kontakt: dict | None = None) -> str:
    """Feste Eröffnungs-Begrüssung des Assistenten (die erste, vorgelesene Zeile).

    Sie ist bewusst deterministisch: serverseitig wird sie vorab synthetisiert
    (`/begruessung.mp3`) und als erste Assistenten-Nachricht in die History gesetzt, damit
    die Stimme beim Start ohne LLM-Latenz sofort einsetzt. Ist der Gesprächspartner über
    einen Einladungslink bekannt (Anrede/Name), wird persönlich begrüsst."""
    kontakt = kontakt or {}
    anrede = (kontakt.get("anrede") or "").strip()
    name = (kontakt.get("name") or "").strip()
    vorstellung = "ich bin der digitale Offerten-Assistent von Ralf Balz"
    if name and offer.ist_du_anrede(anrede):
        return (f"Hallo {name}, {vorstellung}. Schön, dass du da bist. "
                f"Erzähl mir doch: Worum geht es bei deinem geplanten IT-Projekt?")
    if name and anrede in ("Herr", "Frau"):
        return (f"Guten Tag {anrede} {name}, {vorstellung}. Schön, dass Sie da sind. "
                f"Erzählen Sie mir: Worum geht es bei Ihrem geplanten IT-Projekt?")
    return (f"Guten Tag, {vorstellung}. Schön, dass Sie da sind. "
            f"Erzählen Sie mir: Worum geht es bei Ihrem geplanten IT-Projekt?")


def build_system_prompt(wind_down: bool = False, kontakt: dict | None = None) -> str:
    """System-Prompt mit optionalem Kontakt-Hinweis und Abschluss-Hinweis (Soft-Limit)."""
    prompt = SYSTEM_PROMPT + _kontakt_hinweis(kontakt)
    if wind_down:
        prompt += WIND_DOWN_HINWEIS
    return prompt


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # liest ANTHROPIC_API_KEY aus der Umgebung
    return _client


def stream_reply(session_id: str, history: list[dict], wind_down: bool = False,
                 kontakt: dict | None = None):
    """Dispatcher: streamt die Agenten-Antwort über den eingestellten KI-Anbieter.

    'claude' (Default, Anthropic) oder 'openai' (ChatGPT). Beide liefern dieselbe
    Event-Schnittstelle. Im Admin via ``settings.ki_anbieter()`` umschaltbar.

    kontakt: optionale Empfängerdaten (anrede/name/email) aus dem Einladungslink, damit
    der Agent persönlich anspricht und nicht erneut nach der E-Mail fragt.

    Yields: {"type": "token", "text": ...} | {"type": "offer_created"} |
            {"type": "done"} | {"type": "error", "message": ...}
    """
    # Die vorab gesetzte Eröffnungs-Begrüssung steht als erste Assistenz-Nachricht in der
    # History (fürs Transkript). Für die KI-API müssen die Nachrichten aber mit einer
    # User-Rolle beginnen – führende Assistenz-Nachrichten daher überspringen. Dass die
    # Begrüssung nicht wiederholt wird, steuert der System-Prompt.
    i = 0
    while i < len(history) and history[i].get("role") != "user":
        i += 1
    history = history[i:]

    if settings.ki_anbieter() == "openai":
        import agent_openai  # lazy: vermeidet Zirkelbezug beim Import
        yield from agent_openai.stream_reply(session_id, history, wind_down=wind_down, kontakt=kontakt)
    else:
        yield from _stream_reply_claude(session_id, history, wind_down=wind_down, kontakt=kontakt)


def _stream_reply_claude(session_id: str, history: list[dict], wind_down: bool = False,
                         kontakt: dict | None = None):
    """Streamt die Agenten-Antwort von Claude als Event-Dicts.

    history: Liste von {role, content}. Persistiert am Ende die Assistenz-Antwort und
    löst bei Bedarf die Offerten-Erstellung aus.

    wind_down: Bei True (Soft-Limit der Kostenbremse erreicht) wird der Agent angewiesen,
    das Gespräch sanft zum Abschluss zu bringen.
    """
    messages: list[dict] = [{"role": m["role"], "content": m["content"]} for m in history]
    final_text_parts: list[str] = []
    system_prompt = build_system_prompt(wind_down, kontakt)
    tokens_in = tokens_out = 0

    for _ in range(MAX_TOOL_ROUNDS + 1):
        round_text: list[str] = []
        with _get_client().messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=system_prompt,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            tools=[OFFER_TOOL],
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                round_text.append(text)
                yield {"type": "token", "text": text}
            final = stream.get_final_message()

        usage = getattr(final, "usage", None)
        if usage is not None:
            tokens_in += getattr(usage, "input_tokens", 0) or 0
            tokens_out += getattr(usage, "output_tokens", 0) or 0

        final_text_parts.extend(round_text)

        if final.stop_reason != "tool_use":
            break

        # Werkzeug-Aufruf(e) abarbeiten und Ergebnis zurückgeben.
        messages.append({"role": "assistant", "content": final.content})
        tool_results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            try:
                offer.create_and_send(session_id, block.input)
                yield {"type": "offer_created"}
                result_text = "Offerte wurde erstellt und an Ralf zur persönlichen Prüfung gesendet."
            except Exception as e:  # noqa: BLE001
                logger.error("Offerten-Erstellung fehlgeschlagen: %s", e)
                result_text = "Die Offerte konnte technisch nicht erstellt werden."
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
            )
        messages.append({"role": "user", "content": tool_results})
        # Schleife läuft weiter, damit der Agent die mündliche Abschlussbestätigung gibt.

    if tokens_in or tokens_out:
        db.add_session_usage(session_id, tokens_in, tokens_out)

    final_text = "".join(final_text_parts).strip()
    if final_text:
        db.add_message(session_id, "assistant", final_text)
    yield {"type": "done"}
