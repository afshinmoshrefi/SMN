#!/usr/bin/env bash
# Install / update (or undo) the SMN publishing dashboard on the DEV box.
#   install or update:  bash install_pub_dashboard.sh
#   undo:               bash install_pub_dashboard.sh --rollback
# LAN/loopback only (127.0.0.1:7172, 192.168.1.180:7172). No login yet.
# Also updates blog_queue.py and publish_article.py (Rec Hero file, portfolio
# delete targeting, portfolio publish/unpublish routes).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/flask/blog
STATE=/var/lib/smn-dashboard
UNITS="pub_dashboard.service pub_dashboard_sweep.service pub_dashboard_sweep.timer"
FILES="pin_store.py article_index.py schedule_store.py dashboard_auth.py pub_dashboard.py pin_sweeper.py templates/pub_dashboard.html"
PATCHED="rebuild_news_home.py blog_queue.py publish_article.py"
PY=/home/flask/venv-smn-integrity-20260711T205457Z/bin/python
ENVFILE=/etc/SMN/dashboard.env
SNIPPET=/etc/nginx/snippets/smn_dashboard.conf
SITE=/etc/nginx/sites-available/smn.conf

# Public web address /dashboard/: open ONLY when login is required and
# SMN_DASHBOARD_PUBLIC=1.  Otherwise the path answers 404 (LAN port 7172 only).
write_nginx_snippet() {
  local public=0
  if grep -qx 'SMN_DASHBOARD_PUBLIC=1' "$ENVFILE" 2>/dev/null \
     && ! grep -qx 'SMN_DASHBOARD_AUTH=off' "$ENVFILE" 2>/dev/null \
     && grep -q '^SMN_DASHBOARD_TW_PUBKEY=' "$ENVFILE" 2>/dev/null; then public=1; fi
  mkdir -p "$(dirname "$SNIPPET")"
  if [[ $public == 1 ]]; then
    cat > "$SNIPPET" <<'NGX'
# SMN publishing dashboard (login required). Managed by install_pub_dashboard.sh
location = /dashboard { return 301 /dashboard/; }
location /dashboard/ {
    proxy_pass http://127.0.0.1:7172/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /dashboard;
    proxy_read_timeout 900s;
    add_header Cache-Control "no-store" always;
    add_header X-Frame-Options "DENY" always;
}
NGX
  else
    printf '# SMN publishing dashboard is not public (login off or not configured).\nlocation /dashboard/ { return 404; }\n' > "$SNIPPET"
  fi
  if ! grep -q 'include snippets/smn_dashboard.conf;' "$SITE"; then
    cp -p "$SITE" "$SITE.bak-dashboard-$(date -u +%Y%m%dT%H%M%SZ)"
    # first server block: add the include after its server_name line
    sed -i '0,/server_name .*;/s//&\n    include snippets\/smn_dashboard.conf;/' "$SITE"
  fi
  nginx -t -q && systemctl reload nginx && echo "OK  nginx /dashboard/ public=$public"
}

restore_latest() {  # $1 = file name
  local last
  last=$(ls -1t "$LIVE/$1".bak-dashboard-* 2>/dev/null | head -1 || true)
  [[ -n "$last" ]] && cp -p "$last" "$LIVE/$1" && echo "restored $last"
}

if [[ "${1:-}" == "--rollback" ]]; then
  systemctl disable --now pub_dashboard_sweep.timer pub_dashboard.service 2>/dev/null || true
  for u in $UNITS; do rm -f "/etc/systemd/system/$u"; done
  systemctl daemon-reload
  for f in $PATCHED; do restore_latest "$f"; done
  for f in $FILES; do rm -f "$LIVE/$f"; done
  systemctl restart blog_queue.service
  if [[ -f "$SNIPPET" ]]; then
    printf 'location /dashboard/ { return 404; }\n' > "$SNIPPET"; nginx -t -q && systemctl reload nginx
  fi
  echo "Rolled back. Pins and audit log kept in $STATE (delete by hand if unwanted)."
  exit 0
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
BQ_CHANGED=0
for f in blog_queue.py publish_article.py; do cmp -s "$SRC/$f" "$LIVE/$f" || BQ_CHANGED=1; done
for f in $PATCHED; do cp -p "$LIVE/$f" "$LIVE/$f.bak-dashboard-$TS"; done
for f in $FILES $PATCHED; do install -m 644 "$SRC/$f" "$LIVE/$f"; done
mkdir -p "$STATE" && chmod 750 "$STATE"
for u in $UNITS; do install -m 644 "$SRC/systemd/$u" /etc/systemd/system/; done
systemctl daemon-reload
if [[ ! -f "$ENVFILE" ]]; then     # first install: login off, private network only
  mkdir -p "$(dirname "$ENVFILE")"
  printf 'SMN_DASHBOARD_ENV=dev\nSMN_DASHBOARD_AUTH=off\nSMN_DASHBOARD_PUBLIC=0\n' > "$ENVFILE"
  chmod 640 "$ENVFILE"
fi

(cd "$LIVE" && "$PY" -c "import pub_dashboard, schedule_store, dashboard_auth, rebuild_news_home, publish_article, blog_queue") \
  || { echo "IMPORT FAILED - rolling back"; bash "$0" --rollback; exit 1; }

systemctl enable pub_dashboard.service pub_dashboard_sweep.timer
# Restart blog_queue (the live publishing API) only if its code changed.
if [[ $BQ_CHANGED == 1 ]]; then systemctl restart blog_queue.service; fi
systemctl restart pub_dashboard.service
systemctl start pub_dashboard_sweep.timer
sleep 4
write_nginx_snippet || echo "WARN nginx not updated (config test failed); dashboard still on LAN port 7172"
dash_ok=0; bq_ok=0
curl -fsS http://127.0.0.1:7172/api/health >/dev/null && dash_ok=1
systemctl is-active --quiet blog_queue.service && curl -s -o /dev/null http://127.0.0.1:7171/ && bq_ok=1
if [[ $dash_ok == 1 && $bq_ok == 1 ]]; then
  echo "OK  dashboard:  http://192.168.1.180:7172/"
  echo "OK  agent docs: http://192.168.1.180:7172/llms.txt"
  [[ $BQ_CHANGED == 1 ]] && echo "OK  blog_queue restarted (its code changed)" \
                         || echo "OK  blog_queue unchanged, not restarted"
  echo "Undo with: bash $0 --rollback"
else
  echo "HEALTH CHECK FAILED (dashboard=$dash_ok blog_queue=$bq_ok) - rolling back"
  journalctl -u pub_dashboard -u blog_queue -n 30 --no-pager
  bash "$0" --rollback; exit 1
fi
