#!/bin/bash
# The only workflow commands the unattended Claude orchestrator may run.
# Each maps to an existing SMN stage; production and the engine are read only
# through subscription_capture.py. Usage: claude_daily_tools.sh <command> [args]
set -euo pipefail
REPO=${SMN_CLAUDE_REPO:-/opt/smn-claude-daily/SMN}
PY=/home/flask/venv-smn-integrity-20260711T205457Z/bin/python
CLAUDE=/root/.local/bin/claude
export SMN_PLAYWRIGHT=/opt/smn-playwright/node_modules/playwright SMN_BROWSER_CHANNEL=bundled
cd "$REPO/blog"
cmd=${1:-}; shift || true
case "$cmd" in
  sync)    git -C "$REPO" fetch -q origin main && git -C "$REPO" merge -q --ff-only origin/main && git -C "$REPO" rev-parse HEAD ;;
  capture) exec "$PY" subscription_capture.py "$@" ;;
  daily)   exec "$PY" subscription_daily.py --provider claude --claude "$CLAUDE" "$@" ;;
  edition) action=${1:?action}; shift; exec "$PY" engine_edition_workflow.py "$action" --provider claude --claude "$CLAUDE" "$@" ;;
  layout)  exec node subscription_layout.cjs "$@" ;;
  publish) action=${1:?action}; shift
           extra=(); [ "$action" = activate ] && extra=(--node /usr/bin/node --playwright "$SMN_PLAYWRIGHT")
           exec "$PY" subscription_primary_publish.py "$action" --repo "$REPO" "${extra[@]}" "$@" ;;
  *) echo "usage: $0 sync|capture|daily|edition|layout|publish ..." >&2; exit 2 ;;
esac
