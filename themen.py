"""Anzeigenthemen: die Anzeigen verlinken auf ``/?adgroup=<schluessel>``.

Der Schlüssel aus der URL wird nur gegen diese Liste geprüft und als Schlüssel gemerkt
(Session-Cookie, Spalte ``sessions.thema``) – nie als freier Text. Was auf der Seite, in der
Begrüssung und im System-Prompt landet, stammt ausschliesslich von hier.

Je Thema:
- ``titel``       Name der Anzeige, so steht er in der Überschrift, im Admin und in der Mail
- ``gesprochen``  Form für die vorgelesene Begrüssung („Sie interessieren sich für …“),
                  ohne Schrägstrich und im Akkusativ
- ``stichworte``  worum es in solchen Gesprächen typischerweise geht (für den System-Prompt)
"""

THEMEN: dict[str, dict[str, str]] = {
    "mobile_app": {
        "titel": "Mobile App entwickeln",
        "gesprochen": "die Entwicklung einer mobilen App",
        "stichworte": (
            "Android-Apps, iOS-Apps, Firmen-Apps, interne Apps, Apps für Aussendienst, "
            "Produktion oder Service, mobile Datenerfassung, Offline-Funktionalität sowie die "
            "Modernisierung, Weiterentwicklung oder Übernahme bestehender Apps"
        ),
    },
    "web_app": {
        "titel": "Webanwendung entwickeln",
        "gesprochen": "die Entwicklung einer Webanwendung",
        "stichworte": (
            "interne Webanwendungen, Kundenportale, Mitarbeiterportale, Self-Service-Lösungen, "
            "browserbasierte Unternehmenssoftware oder die Erweiterung und Modernisierung "
            "bestehender Webanwendungen"
        ),
    },
    "database_app": {
        "titel": "Datenbank-Anwendung entwickeln",
        "gesprochen": "die Entwicklung einer Datenbank-Anwendung",
        "stichworte": (
            "Produktionsdatenbanken, Auftragsdatenbanken, Stammdatenverwaltung, SQL- oder "
            "PostgreSQL-Anwendungen, Datenbanken mit Web-Oberfläche sowie die Ablösung oder "
            "Modernisierung bestehender Datenbanklösungen"
        ),
    },
    "excel_processes": {
        "titel": "Excel / manuelle Prozesse ablösen",
        "gesprochen": "die Ablösung von Excel und manuellen Prozessen",
        "stichworte": (
            "geschäftskritische Abläufe mit Excel, Tabellen, Papier oder E-Mail, die Ablösung "
            "von Excel-Lösungen, Automatisierung manueller Prozesse, zentrale Datenverwaltung, "
            "Vermeidung von Medienbrüchen und eine passende Web- oder Datenbank-Anwendung"
        ),
    },
    "software_modernization": {
        "titel": "Bestehende Software modernisieren",
        "gesprochen": "die Modernisierung bestehender Software",
        "stichworte": (
            "Legacy-Software, alte Desktop-Anwendungen, veraltete Technologien, technische "
            "Schulden, schwer wartbare Systeme oder die Migration auf moderne Web-, Datenbank- "
            "oder Cloud-Architekturen"
        ),
    },
    "software_maintenance": {
        "titel": "Bestehende Software übernehmen / weiterentwickeln",
        "gesprochen": "die Übernahme und Weiterentwicklung bestehender Software",
        "stichworte": (
            "die Übernahme bestehender Projekte, neue Funktionen, Fehlerbehebung, Wartung, "
            "Weiterentwicklung, fehlende Entwickler oder Software, deren ursprünglicher "
            "Entwickler nicht mehr verfügbar ist"
        ),
    },
    "production_process": {
        "titel": "Produktions- und Prozesssoftware",
        "gesprochen": "Produktions- und Prozesssoftware",
        "stichworte": (
            "Produktionsdatenerfassung, Arbeitsabläufe, Prozessschritte, Qualitätsdaten, "
            "Rückverfolgbarkeit, digitale Arbeitsanweisungen, Shopfloor-Anwendungen und "
            "individuelle Software für Produktion und Fertigung"
        ),
    },
    "custom_business_software": {
        "titel": "Individuelle Unternehmenssoftware",
        "gesprochen": "individuelle Unternehmenssoftware",
        "stichworte": (
            "Software nach Mass, Fachanwendungen, interne Unternehmenssoftware, individuelle "
            "Geschäftsanwendungen und Lösungen, für die Standardsoftware nicht ausreicht"
        ),
    },
    "order_management": {
        "titel": "Auftrags- und Verwaltungssoftware",
        "gesprochen": "Auftrags- und Verwaltungssoftware",
        "stichworte": (
            "individuelle Auftragsverwaltung, Projektverwaltung, Angebotssoftware, "
            "ERP-Light-Lösungen, Status- und Workflow-Systeme sowie zentrale Kunden- und "
            "Auftragsdaten"
        ),
    },
    "system_integration": {
        "titel": "Schnittstellen / Systeme verbinden",
        "gesprochen": "Schnittstellen und die Verbindung bestehender Systeme",
        "stichworte": (
            "APIs, ERP-Schnittstellen, Datenaustausch, automatische Datenübertragung, "
            "REST-Schnittstellen oder die Verbindung von Excel-, Datenbank-, Web- und "
            "bestehenden Unternehmenssystemen"
        ),
    },
    "data_migration": {
        "titel": "Datenmigration / Systemwechsel",
        "gesprochen": "Datenmigration und Systemwechsel",
        "stichworte": (
            "Datenbankmigration, MSSQL zu PostgreSQL, Access-Migration, Excel-Datenübernahme, "
            "die Ablösung alter Software und die sichere Überführung bestehender Datenbestände"
        ),
    },
    "backend_api": {
        "titel": "Backend / API Entwicklung",
        "gesprochen": "Backend- und API-Entwicklung",
        "stichworte": (
            "REST APIs, FastAPI, Java Backend, .NET Backend, Datenbank-Backends, Schnittstellen "
            "oder die Erweiterung und Modernisierung bestehender Backend-Systeme"
        ),
    },
    "prototype_mvp": {
        "titel": "Prototyp / MVP entwickeln",
        "gesprochen": "die Entwicklung eines Prototyps oder MVP",
        "stichworte": (
            "MVPs, Prototypen, Proof of Concept, technische Machbarkeit, schnelle erste "
            "Versionen und die strukturierte Weiterentwicklung einer Softwareidee"
        ),
    },
    "messdaten_pruefberichte": {
        "titel": "Messdaten / Prüfberichte digitalisieren",
        "gesprochen": "die Digitalisierung von Messdaten und Prüfberichten",
        "stichworte": (
            "Messdatenerfassung von Prüfständen und Messgeräten, Import aus CSV-, Excel- oder "
            "herstellereigenen Formaten, automatische und reproduzierbare Auswertung, "
            "Kennlinien und Diagramme, standardisierte Prüfberichte als PDF oder Word, "
            "Rückverfolgbarkeit von Rohdaten sowie die Ablösung manueller Auswertungen in "
            "Excel und Word"
        ),
    },
}

# Ohne Anzeige (z. B. über die Navigation von ralfwbalz.ch): allgemein ein Softwareprojekt.
# „titel“ steht hinter „Ich kann Ihnen helfen bei:“, darum der Dativ.
STANDARD: dict[str, str] = {
    "schluessel": "",
    "titel": "Ihrem Softwareprojekt",
    "gesprochen": "",
    "stichworte": "",
}


def bekannt(schluessel: str) -> bool:
    return schluessel in THEMEN


def fuer_anzeige(schluessel: str | None) -> dict[str, str]:
    """Thema zum Schlüssel einer Anzeige; unbekannt oder leer → ``STANDARD``."""
    eintrag = THEMEN.get(schluessel or "")
    if eintrag is None:
        return STANDARD
    return {"schluessel": schluessel, **eintrag}


def titel(schluessel: str | None) -> str:
    """Name der Anzeige für Admin und Mail; ``""``, wenn es keine gab."""
    eintrag = THEMEN.get(schluessel or "")
    return eintrag["titel"] if eintrag else ""
