#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run this script with sudo.\n' >&2
  exit 1
fi

systemctl disable --now linkscribe-api linkscribe-worker linkscribe-certbot-renew.timer \
  2>/dev/null || true
rm -f /etc/systemd/system/linkscribe-api.service /etc/systemd/system/linkscribe-worker.service
rm -f /etc/systemd/system/linkscribe-certbot-renew.service \
  /etc/systemd/system/linkscribe-certbot-renew.timer
rm -f /etc/nginx/sites-enabled/linkscribe /etc/nginx/sites-available/linkscribe
systemctl daemon-reload
if command -v nginx >/dev/null 2>&1; then
  nginx -t && systemctl reload nginx
fi

printf 'Services and Nginx configuration removed.\n'
printf 'Data remains in /var/lib/linkscribe and secrets remain in /etc/linkscribe.\n'
printf 'Remove those directories manually only when you no longer need them.\n'
