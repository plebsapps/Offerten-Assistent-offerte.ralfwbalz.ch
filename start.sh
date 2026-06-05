#!/bin/bash
set -e
cd "$(dirname "$0")"

# Lokaler Betrieb ohne Docker. WeasyPrint braucht Systembibliotheken
# (libpango, libcairo, libgdk-pixbuf, libffi) – diese ggf. via apt installieren.

# Daten-/PDF-Verzeichnis (Default abweichend vom Docker-Pfad /data)
export DATA_DIR="${DATA_DIR:-./data}"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8003 "$@"
