"""Admin-Bereich: Login, Dashboard, Zugang/Einladungslinks, Gespräche, Offerten, Einstellungen.

Alle Routen außer Login/Logout hängen an ``require_admin``. Bei fehlender Anmeldung
löst die Dependency ``NichtAngemeldet`` aus → Redirect auf /admin/login (Handler in main).
Datenhaltung über die schlanke SQLite-Schicht in ``db.py`` (kein PostgreSQL wie im
Schwesterprojekt bewerbung-ralfwbalz, an dem sich Aufbau und Templates orientieren).
"""
import os
import json
import secrets
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

import db
import auth
import offer
import settings

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


# ----------------------------------------------------------------- Login -----

@router.get("/admin/login", response_class=HTMLResponse)
async def login_form(request: Request, fehler: str | None = None):
    if auth.ist_angemeldet(request):
        return _redirect("/admin")
    return templates.TemplateResponse(
        "admin/login.html", {"request": request, "fehler": fehler})


@router.post("/admin/login")
async def login(request: Request, benutzer: str = Form(...), passwort: str = Form(...)):
    if auth.pruefe_login(benutzer, passwort):
        auth.anmelden(request)
        return _redirect("/admin")
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "fehler": "Benutzername oder Passwort falsch."},
        status_code=401)


@router.get("/admin/logout")
async def logout(request: Request):
    auth.abmelden(request)
    return _redirect("/admin/login")


# ------------------------------------------------------------- Dashboard -----

@router.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request, _: None = Depends(auth.require_admin)):
    sessions = db.list_sessions()
    offers = db.list_offers()
    heute = datetime.now().date().isoformat()
    kennzahlen = {
        "gespraeche_gesamt": len(sessions),
        "gespraeche_heute": sum(1 for s in sessions if (s["created_at"] or "").startswith(heute)),
        "offerten_gesamt": len(offers),
        "offerten_offen": sum(1 for o in offers if not o.get("released_at")),
        "tokens_gesamt": sum((s["tokens_in"] or 0) + (s["tokens_out"] or 0) for s in sessions),
    }
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "kennzahlen": kennzahlen,
        "zugangsmodus": settings.zugangsmodus(),
    })


# ---------------------------------------------------------------- Zugang -----

@router.get("/admin/zugang", response_class=HTMLResponse)
async def zugang(request: Request, _: None = Depends(auth.require_admin)):
    return templates.TemplateResponse("admin/zugang.html", {
        "request": request,
        "zugangsmodus": settings.zugangsmodus(),
        "links": db.list_zugangslinks(),
        "basis_url": PUBLIC_BASE_URL,
    })


@router.post("/admin/zugang/modus")
async def zugang_modus(request: Request, _: None = Depends(auth.require_admin),
                       modus: str = Form(...)):
    if modus in settings.ZUGANGSMODI:
        db.set_setting("zugangsmodus", modus)
    return _redirect("/admin/zugang")


@router.post("/admin/zugang/neu")
async def zugang_neu(request: Request, _: None = Depends(auth.require_admin),
                     notiz: str = Form(""), gueltig_tage: str = Form("")):
    token = secrets.token_urlsafe(24)
    gueltig_bis = None
    try:
        tage = int(gueltig_tage)
        if tage > 0:
            gueltig_bis = (datetime.now() + timedelta(days=tage)).isoformat(timespec="seconds")
    except ValueError:
        pass
    db.create_zugangslink(token, notiz.strip(), gueltig_bis)
    return _redirect("/admin/zugang")


@router.post("/admin/zugang/{link_id}/deaktivieren")
async def zugang_deaktivieren(request: Request, link_id: int,
                              _: None = Depends(auth.require_admin),
                              aktiv: str = Form("0")):
    # aktiv=1 → wieder aktivieren, sonst deaktivieren.
    db.set_zugangslink_deaktiviert(link_id, aktiv != "1")
    return _redirect("/admin/zugang")


# ------------------------------------------------------------- Gespräche -----

@router.get("/admin/gespraeche", response_class=HTMLResponse)
async def gespraeche(request: Request, _: None = Depends(auth.require_admin)):
    return templates.TemplateResponse("admin/gespraeche.html", {
        "request": request, "gespraeche": db.list_sessions()})


@router.get("/admin/gespraeche/{session_id}", response_class=HTMLResponse)
async def gespraech_detail(request: Request, session_id: str,
                           _: None = Depends(auth.require_admin)):
    history = db.get_history(session_id)
    tokens_in, tokens_out = db.get_session_usage(session_id)
    return templates.TemplateResponse("admin/gespraech.html", {
        "request": request,
        "session_id": session_id,
        "history": history,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "offerte": db.get_offer_by_session(session_id),
    })


# -------------------------------------------------------------- Offerten -----

@router.get("/admin/offerten", response_class=HTMLResponse)
async def offerten(request: Request, _: None = Depends(auth.require_admin)):
    rows = db.list_offers()
    offerten = []
    for r in rows:
        try:
            daten = json.loads(r["data_json"])
        except (ValueError, TypeError):
            daten = {}
        offerten.append({**r, "daten": daten})
    return templates.TemplateResponse("admin/offerten.html", {
        "request": request, "offerten": offerten})


@router.get("/admin/offerten/{session_id}/pdf")
async def offerte_pdf(request: Request, session_id: str,
                      _: None = Depends(auth.require_admin)):
    row = db.get_offer_by_session(session_id)
    if row and row.get("pdf_path") and os.path.exists(row["pdf_path"]):
        return FileResponse(row["pdf_path"], media_type="application/pdf",
                            filename=f"offerte-{session_id}.pdf")
    return _redirect("/admin/offerten")


@router.post("/admin/offerten/{token}/freigeben")
async def offerte_freigeben(request: Request, token: str,
                            _: None = Depends(auth.require_admin)):
    try:
        offer.release_to_customer(token)  # idempotent (released_at)
    except Exception as e:  # noqa: BLE001
        logger.error("Freigabe aus Admin fehlgeschlagen: %s", e)
    return _redirect("/admin/offerten")


# ------------------------------------------------------------ Einstellungen --

@router.get("/admin/einstellungen", response_class=HTMLResponse)
async def einstellungen_form(request: Request, _: None = Depends(auth.require_admin)):
    werte = {
        "max_turns": settings.max_turns(),
        "soft_turns": settings.soft_turns(),
        "max_tokens": settings.max_tokens(),
        "soft_tokens": settings.soft_tokens(),
    }
    return templates.TemplateResponse("admin/einstellungen.html", {
        "request": request, "werte": werte})


@router.post("/admin/einstellungen")
async def einstellungen_speichern(request: Request,
                                  _: None = Depends(auth.require_admin)):
    form = await request.form()
    for schluessel in ("max_turns", "soft_turns", "max_tokens", "soft_tokens"):
        wert = (form.get(schluessel) or "").strip()
        if wert.isdigit():
            db.set_setting(schluessel, wert)
    return _redirect("/admin/einstellungen")
