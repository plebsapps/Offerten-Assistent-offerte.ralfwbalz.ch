"""Offerte: Datenmodell, PDF-Erzeugung (WeasyPrint) und SMTP-Versand.

Versand-Flow: Die fertige Offerte geht ZUERST nur an Ralf (CONTACT_EMAIL) inkl.
Chat-Transkript und einem tokenisierten Freigabe-Link. Erst wenn Ralf diesen Link
aufruft, geht die Offerte an den Auftraggeber.
"""
import os
import json
import smtplib
import secrets
import logging
import pathlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

import db

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")
OFFERS_DIR = os.path.join(DATA_DIR, "offers")
ROLE_LABEL = {"user": "Kunde", "assistant": "Assistent"}

_env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(["html"]),
)


def _render_pdf(data: dict) -> bytes:
    from weasyprint import HTML  # Import hier: schwere Abhängigkeit, nur bei Bedarf laden

    html = _env.get_template("offer_pdf.html").render(
        o=data,
        erstellt=datetime.now().strftime("%d.%m.%Y um %H:%M Uhr"),
    )
    return HTML(string=html, base_url=".").write_pdf()


def _smtp_send(recipient: str, subject: str, html: str, plain: str,
               pdf_bytes: bytes, pdf_name: str, reply_to: str | None = None) -> None:
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

    part = MIMEApplication(pdf_bytes, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=pdf_name)
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

    token = secrets.token_urlsafe(24)
    history = db.get_history(session_id)
    pdf_bytes = _render_pdf(data)

    pdf_path = os.path.join(OFFERS_DIR, f"offerte-{session_id}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    db.save_offer(session_id, json.dumps(data, ensure_ascii=False), pdf_path, token)
    _send_to_ralf(data, history, pdf_bytes, token)


def _send_to_ralf(data: dict, history: list[dict], pdf_bytes: bytes, token: str) -> None:
    recipient = os.environ["CONTACT_EMAIL"]
    base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    freigabe_url = f"{base_url}/freigabe/{token}"

    kunde = data.get("kunde", {})
    titel = data.get("projekt_titel", "IT-Projekt")
    transcript = _transcript_text(history)

    html = _env.get_template("offer_email.html").render(
        o=data,
        freigabe_url=freigabe_url,
        transcript=transcript,
        erstellt=datetime.now().strftime("%d.%m.%Y um %H:%M Uhr"),
    )
    plain = (
        f"Neue Offerten-Grundlage: {titel}\n\n"
        f"Kunde:   {kunde.get('name', '–')} ({kunde.get('firma', '–')})\n"
        f"E-Mail:  {kunde.get('email', '–')}\n"
        f"Telefon: {kunde.get('telefon', '–')}\n\n"
        f"Zusammenfassung:\n{data.get('zusammenfassung', '')}\n\n"
        f"--- Diese Offerte an den Auftraggeber freigeben/senden: ---\n{freigabe_url}\n\n"
        f"(Die Offerte wurde NICHT automatisch an den Kunden gesendet.)\n\n"
        f"===== Gesprächsprotokoll =====\n{transcript}\n"
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


def release_to_customer(token: str) -> dict | None:
    """Sendet die freigegebene Offerte an den Auftraggeber. Idempotent (einmalig einlösbar).

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
        return row

    pdf_path = row.get("pdf_path")
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    else:
        pdf_bytes = _render_pdf(data)

    titel = data.get("projekt_titel", "IT-Projekt")
    plain = (
        f"Guten Tag {kunde.get('name', '')}\n\n"
        f"vielen Dank für das Gespräch. Im Anhang finden Sie die Offerten-Grundlage zu Ihrem "
        f"Projekt „{titel}“. Ralf W. Balz meldet sich für die weiteren Schritte persönlich bei Ihnen.\n\n"
        f"Freundliche Grüsse\nRalf W. Balz\nralfwbalz.ch"
    )
    html = (
        f'<div style="font-family:Arial,sans-serif;max-width:600px;color:#333">'
        f'<p>Guten Tag {kunde.get("name", "")}</p>'
        f'<p>vielen Dank für das Gespräch. Im Anhang finden Sie die Offerten-Grundlage zu Ihrem '
        f'Projekt &bdquo;{titel}&ldquo;. Ralf W. Balz meldet sich für die weiteren Schritte '
        f'persönlich bei Ihnen.</p>'
        f'<p>Freundliche Grüsse<br>Ralf W. Balz<br>'
        f'<a href="https://ralfwbalz.ch">ralfwbalz.ch</a></p></div>'
    )
    _smtp_send(
        recipient=customer_email,
        subject=f"Ihre Offerten-Grundlage: {titel} – ralfwbalz.ch",
        html=html,
        plain=plain,
        pdf_bytes=pdf_bytes,
        pdf_name="offerte.pdf",
        reply_to=os.environ.get("CONTACT_EMAIL"),
    )
    db.mark_released(token)
    return row
