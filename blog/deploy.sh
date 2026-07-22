#!/usr/bin/env bash
# ============================================================================
# SMN deploy  -  dev/staging -> production
#
#   ./deploy.sh <ref> [--dry-run]      e.g.  ./deploy.sh reconcile
#   ./deploy.sh --rollback <release>   e.g.  ./deploy.sh --rollback 20260722T174500Z
#   ./deploy.sh --list
#
# Formalises the releases/ convention that already existed (predeploy snapshot
# + SHA256SUMS manifest) and adds the two steps that were being done from
# memory and got missed:
#
#   1. RESTART THE SERVICES. article_processor and blog_queue are long-running
#      and hold imported modules in memory. On 2026-07-21 the processor ran
#      stale code for hours after a deploy because nothing restarted it, and
#      an angle job silently fell through to the old code path.
#   2. Verify AFTER restarting, not before.
#
# Production has no GitHub key, so the ref must already be present locally
# (see BUNDLE below). Nothing here fetches from the internet.
# ============================================================================
set -euo pipefail

REPO=/home/flask
BLOG="$REPO/blog"
RELEASES="$REPO/releases"
SERVICES="article_processor.service blog_queue.service"
VENV=/home/flask/venv/bin/python3
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

c_ok()   { printf '  \033[32m%s\033[0m\n' "$*"; }
c_warn() { printf '  \033[33m%s\033[0m\n' "$*"; }
c_err()  { printf '  \033[31m%s\033[0m\n' "$*"; }
die()    { c_err "$*"; exit 1; }

# ---------------------------------------------------------------- list
if [ "${1:-}" = "--list" ]; then
  echo "Available releases (newest first):"
  ls -1t "$RELEASES" 2>/dev/null | grep -E '^[0-9]{8}T[0-9]{6}Z' | head -20
  exit 0
fi

# ---------------------------------------------------------------- rollback
if [ "${1:-}" = "--rollback" ]; then
  REL="${2:-}"
  [ -n "$REL" ] || die "usage: deploy.sh --rollback <release>   (see --list)"
  SNAP="$RELEASES/$REL/blog"
  [ -d "$SNAP" ] || die "no snapshot at $SNAP"
  echo "==> Rolling back to $REL"
  # Restore only tracked code; never touch data dirs (audit/, article_ideas/...)
  find "$SNAP" -maxdepth 1 -name '*.py' -exec cp -p {} "$BLOG/" \;
  [ -d "$SNAP/templates" ] && cp -p "$SNAP"/templates/* "$BLOG/templates/" 2>/dev/null || true
  c_ok "files restored from $REL"
  systemctl restart $SERVICES
  sleep 3
  for s in $SERVICES; do
    [ "$(systemctl is-active "$s")" = active ] && c_ok "$s active" || die "$s FAILED to start"
  done
  c_ok "rollback complete"
  exit 0
fi

# ---------------------------------------------------------------- deploy
REF="${1:-}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1
[ -n "$REF" ] || die "usage: deploy.sh <ref> [--dry-run]   |  --rollback <rel>  |  --list"

cd "$REPO"
git rev-parse --verify "$REF^{commit}" >/dev/null 2>&1 \
  || die "ref '$REF' not found locally. Production has no GitHub key - ship it from dev first:
     dev:   git bundle create /tmp/smn.bundle --all && scp -P 4369 /tmp/smn.bundle root@PROD:/tmp/
     prod:  git fetch /tmp/smn.bundle 'refs/heads/*:refs/remotes/origin/*'"

TARGET=$(git rev-parse --short "$REF")
CURRENT=$(git rev-parse --short HEAD)
echo "==> SMN deploy   $CURRENT  ->  $TARGET   ($REF)"

# refuse to clobber uncommitted work on the box
DIRTY=$(git status --porcelain -- blog/ | wc -l)
[ "$DIRTY" -eq 0 ] || {
  git status --short -- blog/ | head -10
  die "working tree has $DIRTY uncommitted change(s). Commit or stash first."
}

echo "==> Changes to be applied:"
git diff --stat "HEAD..$REF" -- blog/ | tail -20
[ "$DRY" -eq 1 ] && { c_warn "dry run - stopping here"; exit 0; }

# ---- 1. predeploy snapshot (the existing releases/ convention) ----
echo "==> 1. Snapshot current state"
mkdir -p "$RELEASES/$STAMP/blog/templates"
find "$BLOG" -maxdepth 1 -name '*.py' -exec cp -p {} "$RELEASES/$STAMP/blog/" \;
cp -p "$BLOG"/templates/* "$RELEASES/$STAMP/blog/templates/" 2>/dev/null || true
( cd "$RELEASES/$STAMP/blog" && sha256sum *.py > "../PREDEPLOY.SHA256SUMS" )
echo "$CURRENT" > "$RELEASES/$STAMP/FROM_COMMIT"
c_ok "snapshot -> releases/$STAMP  (rollback: deploy.sh --rollback $STAMP)"

# ---- 2. checkout ----
echo "==> 2. Checkout $REF"
git checkout -q "$REF" -- blog/
git symbolic-ref -q HEAD "refs/heads/$REF" 2>/dev/null || git checkout -q "$REF" 2>/dev/null || true
c_ok "code updated"

# ---- 3. compile ----
echo "==> 3. Byte-compile"
"$VENV" -m compileall -q "$BLOG" >/dev/null 2>&1 \
  || die "compile FAILED - rolling back: deploy.sh --rollback $STAMP"
c_ok "all modules compile"

# ---- 4. tests ----
echo "==> 4. Test suite"
if [ -d "$BLOG/tests" ]; then
  set +e
  ( cd "$BLOG" && set -a; . /etc/tradewave/secrets.env 2>/dev/null; . /etc/SMN/secrets.env 2>/dev/null; set +a
    "$VENV" -m unittest discover -s tests ) > /tmp/deploy_tests.log 2>&1
  rc=$?
  set -e
  tail -3 /tmp/deploy_tests.log | sed 's/^/     /'
  [ $rc -eq 0 ] || die "tests FAILED - roll back with: deploy.sh --rollback $STAMP"
  c_ok "tests pass"
else
  c_warn "no tests/ directory - skipping"
fi

# ---- 5. RESTART SERVICES (the step that keeps getting missed) ----
echo "==> 5. Restart services"
systemctl restart $SERVICES
sleep 3
for s in $SERVICES; do
  [ "$(systemctl is-active "$s")" = active ] \
    && c_ok "$s active (started $(systemctl show "$s" -p ActiveEnterTimestamp --value))" \
    || die "$s FAILED to start - roll back: deploy.sh --rollback $STAMP"
done

# ---- 6. record what shipped ----
echo "==> 6. Record"
( cd "$BLOG" && sha256sum *.py > "$RELEASES/$STAMP-DEPLOYED.SHA256SUMS" )
git rev-parse HEAD > "$RELEASES/current-release"
c_ok "manifest -> releases/$STAMP-DEPLOYED.SHA256SUMS"

echo
c_ok "DEPLOYED $TARGET"
echo "  verify : journalctl -u article_processor.service -n 40 --no-pager"
echo "  rollback: $0 --rollback $STAMP"
