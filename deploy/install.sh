#!/usr/bin/env bash
# Installation auf dem Raspberry Pi (Raspberry Pi OS). Aufruf aus dem Repo-Ordner:
#   sudo ./deploy/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Bitte mit sudo ausführen." >&2; exit 1; fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/anreise
DATA=/var/lib/anreise
ENVFILE=/etc/anreise.env

apt-get update
apt-get install -y python3-venv sqlite3 rsync

id anreise &>/dev/null || useradd --system --home "$DATA" --shell /usr/sbin/nologin anreise
mkdir -p "$APP" "$DATA/backup"
rsync -a --delete --exclude .git --exclude .venv --exclude '*.sqlite3' "$SRC/" "$APP/"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R anreise:anreise "$DATA"

if [[ ! -f $ENVFILE ]]; then
  read -rsp "Neues Verwaltungspasswort: " PW; echo
  cat > "$ENVFILE" <<EOF
ANREISE_DB=$DATA/anreise.sqlite3
ANREISE_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
ANREISE_ADMIN_PASSWORD=$PW
ANREISE_PORT=8080
EOF
  chmod 600 "$ENVFILE"
fi

# Tägliche Sicherung der Datenbank (14 Tage aufbewahren)
cat > /etc/cron.daily/anreise-backup <<EOF
#!/bin/sh
sqlite3 $DATA/anreise.sqlite3 ".backup '$DATA/backup/anreise-\$(date +%F).sqlite3'"
find $DATA/backup -name 'anreise-*.sqlite3' -mtime +14 -delete
EOF
chmod 755 /etc/cron.daily/anreise-backup

cp "$APP/deploy/anreise.service" /etc/systemd/system/anreise.service
systemctl daemon-reload
systemctl enable --now anreise
systemctl restart anreise

echo "Fertig. Erreichbar unter http://$(hostname -I | awk '{print $1}'):8080/"
