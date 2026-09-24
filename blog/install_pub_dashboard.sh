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
FILES="pin_store.py article_index.py schedule_store.py pub_dashboard.py pin_sweeper.py templates/pub_dashboard.html"
PATCHED="rebuild_news_home.py blog_queue.py publish_article.py"
PY=/home/flask/venv-smn-integrity-20260711T205457Z/bin/python

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

(cd "$LIVE" && "$PY" -c "import pub_dashboard, schedule_store, rebuild_news_home, publish_article, blog_queue") \
  || { echo "IMPORT FAILED - rolling back"; bash "$0" --rollback; exit 1; }

systemctl enable pub_dashboard.service pub_dashboard_sweep.timer
# Restart blog_queue (the live publishing API) only if its code changed.
if [[ $BQ_CHANGED == 1 ]]; then systemctl restart blog_queue.service; fi
systemctl restart pub_dashboard.service
systemctl start pub_dashboard_sweep.timer
sleep 4
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
