# nginx-vhost-Vorlage für offerte.ralfwbalz.ch
# Ablage z. B. unter /etc/nginx/sites-available/offerte.ralfwbalz.ch,
# dann nach sites-enabled verlinken und mit certbot TLS aktivieren:
#   sudo certbot --nginx -d offerte.ralfwbalz.ch
# Voraussetzung: DNS-A-Record offerte.ralfwbalz.ch -> Server-IP.

server {
    listen 80;
    listen [::]:80;
    server_name offerte.ralfwbalz.ch;

    # Eigenes Zugriffsprotokoll statt des gemeinsamen /var/log/nginx/access.log:
    # dort schreiben alle vhosts hinein und das Format führt kein $host-Feld, die
    # Domain wäre also nur über Heuristik zu trennen. Aus dieser Datei trägt
    # ralfwbalz/werkzeuge/offerte_import.sh die Besuche stündlich in die
    # Besucherstatistik unter ralfwbalz.ch/statistik nach.
    access_log /var/log/nginx/offerte.access.log;

    location / {
        proxy_pass http://127.0.0.1:8003;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Server-Sent Events (Chat-Stream): Pufferung aus, lange Timeouts
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
