"""Offerte: Datenmodell, PDF-Erzeugung (WeasyPrint) und SMTP-Versand.

Versand-Flow: Die fertige Offerte geht ZUERST nur an Ralf (CONTACT_EMAIL) inkl.
Chat-Transkript und einem tokenisierten Freigabe-Link. Erst wenn Ralf diesen Link
aufruft, geht die Offerte an den Auftraggeber.
"""
import os
import html as html_lib
import json
import smtplib
import secrets
import logging
import pathlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

import db
import settings
import themen

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")
OFFERS_DIR = os.path.join(DATA_DIR, "offers")
ROLE_LABEL = {"user": "Kunde", "assistant": "Assistent"}

ZEITZONE = ZoneInfo("Europe/Zurich")


def ortszeit(wert: str | None = None) -> datetime | None:
    """Zeit in Europe/Zurich. Die Datenbank speichert ohne Zone in der Zeit des Containers
    (UTC) – so bleibt, was gespeichert und verglichen wird, einheitlich; umgerechnet wird
    erst für die Anzeige. Ohne Argument: jetzt."""
    if wert is None:
        return datetime.now(timezone.utc).astimezone(ZEITZONE)
    try:
        zeit = datetime.fromisoformat(str(wert))
    except ValueError:
        return None
    if zeit.tzinfo is None:
        zeit = zeit.astimezone()        # als Zeit des Containers lesen
    return zeit.astimezone(ZEITZONE)


def zeit_text(wert: str | None, leer: str = "–") -> str:
    """ISO-Zeit aus der Datenbank als «TT.MM.JJJJ HH:MM» in Ortszeit."""
    if not wert:
        return leer
    zeit = ortszeit(wert)
    return zeit.strftime("%d.%m.%Y %H:%M") if zeit else str(wert)


_env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(["html"]),
)


def _render_pdf(data: dict) -> bytes:
    from weasyprint import HTML  # Import hier: schwere Abhängigkeit, nur bei Bedarf laden

    html = _env.get_template("offer_pdf.html").render(
        o=data,
        erstellt=ortszeit().strftime("%d.%m.%Y um %H:%M Uhr"),
    )
    return HTML(string=html, base_url=".").write_pdf()


def _smtp_send(recipient: str, subject: str, html: str, plain: str,
               pdf_bytes: bytes | None = None, pdf_name: str | None = None,
               reply_to: str | None = None) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    if reply_to:
        msg["Reply-To"] = reply_to

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    if pdf_bytes is not None:
        part = MIMEApplication(pdf_bytes, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment",
                        filename=pdf_name or "anhang.pdf")
        msg.attach(part)

    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        server.starttls()
        server.login(user, password)
        server.sendmail(user, recipient, msg.as_string())


def _transcript_text(history: list[dict]) -> str:
    lines = []
    for m in history:
        lines.append(f"{ROLE_LABEL.get(m['role'], m['role'])}: {m['content']}")
    return "\n\n".join(lines)


def create_and_send(session_id: str, data: dict) -> None:
    """Rendert die Offerte als PDF, speichert sie und mailt sie an Ralf (mit Freigabe-Link)."""
    pathlib.Path(OFFERS_DIR).mkdir(parents=True, exist_ok=True)

    # Die Offerte geht nur an die bestätigte Adresse des Gesprächs – gleichgültig, was das
    # Modell ins Werkzeug geschrieben hat oder worum im Gespräch gebeten wurde.
    bestaetigt = db.get_session_kontakt_email(session_id)
    if bestaetigt:
        if not isinstance(data.get("kunde"), dict):
            data["kunde"] = {}
        data["kunde"]["email"] = bestaetigt

    token = secrets.token_urlsafe(24)
    history = db.get_history(session_id)
    pdf_bytes = _render_pdf(data)
    # Kam der Kunde mit einer Schätzung aus dem Aufwandsrechner, wird sie hinten angefügt:
    # ein PDF für Ralfs Mail, den Admin und die spätere Freigabe an den Kunden.
    schaetzung = db.get_session_schaetzung(session_id)
    mit_schaetzung = False
    if schaetzung:
        pdf_bytes, mit_schaetzung = _zusammenfuegen(pdf_bytes, schaetzung[0])
    # Bei der Offerte vermerkt, damit die Freigabe-Seite sagt, was wirklich im PDF steckt.
    data["anhang_aufwandsschaetzung"] = mit_schaetzung

    pdf_path = os.path.join(OFFERS_DIR, f"offerte-{session_id}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    db.save_offer(session_id, json.dumps(data, ensure_ascii=False), pdf_path, token)
    _send_to_ralf(data, history, pdf_bytes, token,
                  anzeige=themen.titel(db.get_session_thema(session_id)),
                  mit_schaetzung=mit_schaetzung)


def _zusammenfuegen(offerte_pdf: bytes, schaetzung_pdf: bytes) -> tuple[bytes, bool]:
    """Hängt die Aufwandsschätzung aus dem Rechner hinten an die Offerten-Grundlage.

    Gibt (PDF, angefügt?) zurück. Lässt sich das PDF des Rechners nicht lesen, bleibt es
    bei der Offerte allein – wegen des Anhangs geht nie eine Offerte verloren."""
    try:
        import io
        from pypdf import PdfReader, PdfWriter   # nur hier gebraucht

        ziel = PdfWriter()
        for quelle in (offerte_pdf, schaetzung_pdf):
            for seite in PdfReader(io.BytesIO(quelle)).pages:
                ziel.add_page(seite)
        puffer = io.BytesIO()
        ziel.write(puffer)
        return puffer.getvalue(), True
    except Exception as e:  # noqa: BLE001
        logger.error("Aufwandsschätzung liess sich nicht anfügen: %s", e)
        return offerte_pdf, False


def _send_to_ralf(data: dict, history: list[dict], pdf_bytes: bytes, token: str,
                  anzeige: str = "", mit_schaetzung: bool = False) -> None:
    recipient = os.environ["CONTACT_EMAIL"]
    freigabe_url = f"{settings.basis_url()}/freigabe/{token}"

    kunde = data.get("kunde", {})
    titel = data.get("projekt_titel", "IT-Projekt")
    transcript = _transcript_text(history)

    html = _env.get_template("offer_email.html").render(
        o=data,
        freigabe_url=freigabe_url,
        transcript=transcript,
        anzeige=anzeige,
        mit_schaetzung=mit_schaetzung,
        erstellt=ortszeit().strftime("%d.%m.%Y um %H:%M Uhr"),
    )
    plain = (
        f"Neue Offerten-Grundlage: {titel}\n\n"
        f"Kunde:   {kunde.get('name', '–')} ({kunde.get('firma', '–')})\n"
        f"E-Mail:  {kunde.get('email', '–')}\n"
        f"Telefon: {kunde.get('telefon', '–')}\n"
        f"Anzeige: {anzeige or '–'}\n\n"
        f"Zusammenfassung:\n{data.get('zusammenfassung', '')}\n\n"
        f"--- Offerte prüfen, Text ergänzen und an den Auftraggeber senden: ---\n{freigabe_url}\n\n"
        f"(Die Offerte wurde NICHT automatisch an den Kunden gesendet. Der Link öffnet eine "
        f"Seite mit Ihrem Text an den Kunden; gesendet wird erst dort.)\n\n"
        + ("Das PDF im Anhang enthält hinten die Aufwandsschätzung aus dem Aufwandsrechner; "
           "der Kunde bekommt genau dieses PDF mit der Freigabe.\n\n"
           if mit_schaetzung else "")
        + f"===== Gesprächsprotokoll =====\n{transcript}\n"
    )
    _smtp_send(
        recipient=recipient,
        subject=f"Neue Offerten-Grundlage: {titel}",
        html=html,
        plain=plain,
        pdf_bytes=pdf_bytes,
        pdf_name="offerte-entwurf.pdf",
        reply_to=kunde.get("email") or None,
    )


def adresse_bestaetigt(session_id: str, adresse: str) -> bool:
    """True, wenn die Offerte an diese Adresse gehen darf: sie ist die für das Gespräch
    bestätigte (Code oder von Ralf verschickter Einladungslink). Gespräche aus der Zeit
    vor dieser Regel (``None``) bleiben freigebbar – Ralf gibt sie von Hand frei."""
    bestaetigt = db.get_session_kontakt_email(session_id)
    if bestaetigt is None:
        return True
    return bool(bestaetigt) and bestaetigt == (adresse or "").strip().lower()


NACHRICHT_HOECHSTENS = 5000


def freigabe_standardtext(data: dict) -> str:
    """Der Text an den Kunden, wie er ohne Änderung hinausginge. Die Freigabe-Seite zeigt
    ihn im Textfeld, Ralf ergänzt oder ersetzt ihn dort."""
    kunde = data.get("kunde") or {}
    titel = data.get("projekt_titel", "IT-Projekt")
    return (
        f"Guten Tag {kunde.get('name', '')}".rstrip() + "\n\n"
        "vielen Dank für das Gespräch. Im Anhang finden Sie die Offerten-Grundlage zu Ihrem "
        f"Projekt „{titel}“. Ich melde mich für die weiteren Schritte persönlich bei Ihnen.\n\n"
        "Freundliche Grüsse\nRalf W. Balz\nralfwbalz.ch"
    )


def release_to_customer(token: str, nachricht: str | None = None) -> dict | None:
    """Sendet die freigegebene Offerte an den Auftraggeber. Idempotent (einmalig einlösbar).

    ``nachricht``: Ralfs Text an den Kunden von der Freigabe-Seite; leer = Standardtext.
    Rückgabe: das Offer-Dict bei Erfolg, None wenn Token unbekannt.
    """
    row = db.get_offer_by_token(token)
    if row is None:
        return None
    if row.get("released_at"):
        return row  # bereits freigegeben – nicht erneut senden

    data = json.loads(row["data_json"])
    kunde = data.get("kunde", {})
    customer_email = (kunde.get("email") or "").strip()
    if not customer_email:
        logger.error("Offerte %s hat keine Kunden-E-Mail.", token)
        return {**row, "nicht_bestaetigt": True}
    if not adresse_bestaetigt(row["session_id"], customer_email):
        # Kein Versand an eine Adresse, die niemand per Code bestätigt hat.
        logger.error("Offerte %s: Adresse nicht bestätigt, kein Versand.", token)
        return {**row, "nicht_bestaetigt": True}

    pdf_path = row.get("pdf_path")
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    else:
        pdf_bytes = _render_pdf(data)

    titel = data.get("projekt_titel", "IT-Projekt")
    plain = (nachricht or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:NACHRICHT_HOECHSTENS]
    if not plain:
        plain = freigabe_standardtext(data)
    # Für die HTML-Fassung maskiert: aus dem Textfeld gelangt kein Markup in die Mail.
    html = ('<div style="font-family:Arial,sans-serif;max-width:600px;color:#333">'
            + "".join(f"<p>{html_lib.escape(absatz).replace(chr(10), '<br>')}</p>"
                      for absatz in plain.split("\n\n") if absatz.strip())
            + "</div>")
    _smtp_send(
        recipient=customer_email,
        subject=f"Ihre Offerten-Grundlage: {titel} – ralfwbalz.ch",
        html=html,
        plain=plain,
        pdf_bytes=pdf_bytes,
        pdf_name="offerte.pdf",
        reply_to=os.environ.get("CONTACT_EMAIL"),
    )
    db.mark_released(token, plain)
    return row


# Anreden mit Vornamen + Du-Form (persönlich), im Gegensatz zu Frau/Herr/Firma (Sie + Nachname).
DU_ANREDEN = ("männlich", "weiblich")


def ist_du_anrede(anrede: str) -> bool:
    """True bei persönlicher Du-Anrede (männlich/weiblich), sonst förmliches Sie."""
    return (anrede or "").strip().lower() in DU_ANREDEN


def _begruessung(anrede: str = "", name: str = "") -> str:
    """Persönliche Anrede für die Einladungs-Mail.

    - männlich/weiblich → „Hallo {Vorname}“ (Du)
    - Frau/Herr → „Guten Tag {Anrede} {Name}“ (Sie, Nachname)
    - sonst Name/neutral.
    """
    anrede = (anrede or "").strip()
    name = (name or "").strip()
    if ist_du_anrede(anrede) and name:
        return f"Hallo {name}"
    if anrede in ("Frau", "Herr") and name:
        return f"Guten Tag {anrede} {name}"
    if name:
        return f"Guten Tag {name}"
    return "Guten Tag"


def send_invitation(empfaenger_email: str, link_url: str, notiz: str = "",
                    anrede: str = "", name: str = "") -> None:
    """Sendet einen Einladungslink (ohne Anhang) an den Empfänger.

    Wirft bei Fehlern (fehlende SMTP-Konfiguration o. ä.) – die aufrufende Route
    fängt das ab und meldet es im Admin zurück.
    """
    begruessung = _begruessung(anrede, name)
    du = ist_du_anrede(anrede)
    if du:
        einladung_text = (
            "Ralf W. Balz lädt dich ein, dein IT-Projekt im Gespräch mit dem digitalen "
            "Offerten-Assistenten zu planen. Du kannst sprechen oder schreiben – am Ende "
            "entsteht eine strukturierte Offerten-Grundlage, die Ralf persönlich für dich "
            "kalkuliert."
        )
        link_hinweis = "Falls der Button nicht funktioniert, kopiere diesen Link in deinen Browser:"
        persoenlich_hinweis = "Der Link ist persönlich für dich bestimmt. Bitte nicht weitergeben."
    else:
        einladung_text = (
            "Ralf W. Balz lädt Sie ein, Ihr IT-Projekt im Gespräch mit dem digitalen "
            "Offerten-Assistenten zu planen. Sie können sprechen oder schreiben – am Ende "
            "entsteht eine strukturierte Offerten-Grundlage, die Ralf persönlich für Sie "
            "kalkuliert."
        )
        link_hinweis = "Falls der Button nicht funktioniert, kopieren Sie diesen Link in Ihren Browser:"
        persoenlich_hinweis = "Der Link ist persönlich für Sie bestimmt. Bitte nicht weitergeben."
    html = _env.get_template("invitation_email.html").render(
        link_url=link_url,
        notiz=notiz,
        begruessung=begruessung,
        einladung_text=einladung_text,
        link_hinweis=link_hinweis,
        persoenlich_hinweis=persoenlich_hinweis,
    )
    start_satz = ("Über den folgenden Link startest du das Gespräch:" if du
                  else "Über den folgenden Link starten Sie das Gespräch:")
    plain = (
        f"{begruessung}\n\n"
        f"{einladung_text}\n\n"
        f"{start_satz}\n\n"
        f"{link_url}\n\n"
        "Freundliche Grüsse\nRalf W. Balz\nralfwbalz.ch"
    )
    _smtp_send(
        recipient=empfaenger_email,
        subject="Ihr Zugang zum Offerten-Assistenten – ralfwbalz.ch",
        html=html,
        plain=plain,
        reply_to=os.environ.get("CONTACT_EMAIL"),
    )


def send_zugang_code(empfaenger_email: str, code: str, anrede: str = "",
                     name: str = "", ttl_min: int = 10) -> None:
    """Sendet einen 6-stelligen Zugangscode (Self-Service) an den Interessenten.

    Wirft bei SMTP-Fehlern – der aufrufende Endpunkt fängt das ab.
    """
    begruessung = _begruessung(anrede, name)
    du = ist_du_anrede(anrede)
    if du:
        einleitung = (
            "du möchtest den Offerten-Assistenten von Ralf W. Balz nutzen. Mit dem "
            "folgenden Code schaltest du das Gespräch frei:"
        )
        hinweis = "Gib den Code im Browser ein, in dem du den Zugang angefragt hast."
    else:
        einleitung = (
            "Sie möchten den Offerten-Assistenten von Ralf W. Balz nutzen. Mit dem "
            "folgenden Code schalten Sie das Gespräch frei:"
        )
        hinweis = "Geben Sie den Code im Browser ein, in dem Sie den Zugang angefragt haben."
    html = _env.get_template("zugang_code_email.html").render(
        begruessung=begruessung,
        einleitung=einleitung,
        hinweis=hinweis,
        code=code,
        ttl_min=ttl_min,
    )
    plain = (
        f"{begruessung}\n\n"
        f"{einleitung}\n\n"
        f"    {code}\n\n"
        f"Der Code ist {ttl_min} Minuten gültig. {hinweis}\n\n"
        "Freundliche Grüsse\nRalf W. Balz\nralfwbalz.ch"
    )
    _smtp_send(
        recipient=empfaenger_email,
        subject=f"Ihr Zugangscode: {code} – Offerten-Assistent",
        html=html,
        plain=plain,
        reply_to=os.environ.get("CONTACT_EMAIL"),
    )


KURZANFRAGE_HINWEIS = (
    "Dies ist eine Kurzanfrage. Sobald Sie Ihre E-Mail-Adresse mit dem Code bestätigt "
    "haben, erhalte ich Ihre Anfrage und melde mich bei Ihnen."
)


def send_kurzanfrage_code(empfaenger_email: str, code: str, ttl_min: int = 10) -> None:
    """Sendet den Bestätigungscode für eine Kurzanfrage an den Besucher.

    Wirft bei SMTP-Fehlern – der aufrufende Endpunkt fängt das ab.
    """
    hinweis = "Geben Sie den Code auf der Seite ein, auf der Sie die Kurzanfrage gestellt haben."
    html = _env.get_template("zugang_code_email.html").render(
        titel="Ihre Kurzanfrage",
        begruessung="Guten Tag",
        einleitung=KURZANFRAGE_HINWEIS,
        hinweis=hinweis,
        code=code,
        ttl_min=ttl_min,
    )
    plain = (
        "Guten Tag\n\n"
        f"{KURZANFRAGE_HINWEIS}\n\n"
        f"    {code}\n\n"
        f"Der Code ist {ttl_min} Minuten gültig. {hinweis} Falls Sie keine Kurzanfrage "
        "gestellt haben, ignorieren Sie diese E-Mail.\n\n"
        "Freundliche Grüsse\nRalf W. Balz\nralfwbalz.ch"
    )
    _smtp_send(
        recipient=empfaenger_email,
        subject=f"Ihre Kurzanfrage – Bestätigungscode {code}",
        html=html,
        plain=plain,
        reply_to=os.environ.get("CONTACT_EMAIL"),
    )


def send_kurzanfrage_an_ralf(thema: str, text: str, email: str) -> None:
    """Bestätigte Kurzanfrage an Ralf (CONTACT_EMAIL), Reply-To der Besucher."""
    zeitpunkt = ortszeit().strftime("%d.%m.%Y um %H:%M Uhr")
    plain = (
        "Neue Kurzanfrage über offerte.ralfwbalz.ch (E-Mail-Adresse bestätigt):\n\n"
        f"Thema:     {thema}\n"
        f"E-Mail:    {email}\n"
        f"Zeitpunkt: {zeitpunkt}\n\n"
        f"Was gesucht wird:\n{text}\n"
    )
    # Besuchereingaben escapen: sonst käme getipptes Markup als echtes Markup im Postfach an.
    e = html_lib.escape
    html = (
        "<p>Neue Kurzanfrage über offerte.ralfwbalz.ch (E-Mail-Adresse bestätigt):</p>"
        f"<p><strong>Thema:</strong> {e(thema)}<br>"
        f"<strong>E-Mail:</strong> {e(email)}<br>"
        f"<strong>Zeitpunkt:</strong> {zeitpunkt}</p>"
        "<p><strong>Was gesucht wird:</strong></p>"
        f"<p style=\"white-space:pre-wrap\">{e(text)}</p>"
    )
    _smtp_send(
        recipient=os.environ["CONTACT_EMAIL"],
        subject=f"Kurzanfrage: {thema}",
        html=html,
        plain=plain,
        reply_to=email,
    )


def notify_uebergabe(empfaenger_email: str, anrede: str, name: str, kontext: str) -> None:
    """Info-Mail an Ralf, wenn jemand mit einer Schätzung aus dem Aufwandsrechner in den
    Chat kommt. Die Adresse ist dabei **nicht** per Code bestätigt."""
    empfaenger = os.environ.get("CONTACT_EMAIL")
    if not empfaenger:
        return
    person = " ".join(t for t in (anrede, name) if t).strip() or "—"
    zeitpunkt = ortszeit().strftime("%d.%m.%Y um %H:%M Uhr")
    plain = (
        "Jemand ist mit einer Schätzung aus dem Aufwandsrechner in den Offerten-Assistenten "
        "gekommen:\n\n"
        f"Name/Anrede: {person}\n"
        f"E-Mail:      {empfaenger_email} (nicht per Code bestätigt)\n"
        f"Zeitpunkt:   {zeitpunkt}\n\n"
        "SCHÄTZUNG\n"
        f"{kontext}\n"
    )
    html = (
        "<p>Jemand ist mit einer Schätzung aus dem Aufwandsrechner in den "
        "Offerten-Assistenten gekommen:</p>"
        f"<p><strong>Name/Anrede:</strong> {html_lib.escape(person)}<br>"
        f"<strong>E-Mail:</strong> {html_lib.escape(empfaenger_email)} (nicht per Code bestätigt)<br>"
        f"<strong>Zeitpunkt:</strong> {zeitpunkt}</p>"
        f"<pre style=\"font-family:Arial,sans-serif;white-space:pre-wrap\">{html_lib.escape(kontext)}</pre>"
    )
    _smtp_send(
        recipient=empfaenger,
        subject=f"Aufwandsrechner → Offerten-Assistent: {person}",
        html=html,
        plain=plain,
        reply_to=empfaenger_email,
    )


def notify_selbst_zugang(empfaenger_email: str, anrede: str = "", name: str = "") -> None:
    """Kurze Info-Mail an Ralf, wenn sich jemand per Self-Service freigeschaltet hat."""
    empfaenger = os.environ.get("CONTACT_EMAIL")
    if not empfaenger:
        return
    person = " ".join(t for t in (anrede, name) if t).strip() or "—"
    zeitpunkt = ortszeit().strftime("%d.%m.%Y um %H:%M Uhr")
    plain = (
        "Neue Self-Service-Anmeldung am Offerten-Assistenten:\n\n"
        f"Name/Anrede: {person}\n"
        f"E-Mail:      {empfaenger_email}\n"
        f"Zeitpunkt:   {zeitpunkt}\n"
    )
    html = (
        "<p>Neue Self-Service-Anmeldung am Offerten-Assistenten:</p>"
        f"<p><strong>Name/Anrede:</strong> {person}<br>"
        f"<strong>E-Mail:</strong> {empfaenger_email}<br>"
        f"<strong>Zeitpunkt:</strong> {zeitpunkt}</p>"
    )
    _smtp_send(
        recipient=empfaenger,
        subject=f"Self-Service-Zugang: {person}",
        html=html,
        plain=plain,
        reply_to=empfaenger_email,
    )
