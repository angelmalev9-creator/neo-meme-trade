#!/usr/bin/env bash
# NEO Meme Trade — pull-based deploy for the VPS checkout.
#
# Run by neo-auto-deploy.timer. When origin/main has a new commit it:
#   1. tests that exact commit in a throwaway worktree (the live tree is untouched),
#   2. archives the target PAPER ledger before any engine restart,
#   3. fast-forwards the checkout,
#   4. restarts only the services whose code changed,
#   5. checks health, that systemd really owns the running engine, and that
#      the ledger identity survived,
#   6. rolls back to the previous commit if any of that fails.
#
# It never resets an account, never touches the central engine or the labs
# under /root/neomemecoins, and never discards local changes: a dirty or
# diverged checkout stops the deploy instead.
#
# The whole script is one function so bash has parsed it completely before it
# runs; the installed copy can then be replaced safely while it executes.

main() {
  set -euo pipefail

  local REPO="${NEO_DEPLOY_REPO:-/root/neo-meme-trade}"
  local REMOTE="${NEO_DEPLOY_REMOTE:-origin}"
  local BRANCH="${NEO_DEPLOY_BRANCH:-main}"
  local STATE_DIR="${NEO_DEPLOY_STATE_DIR:-/var/lib/neo-market/deploy}"
  local DISABLE_FLAG="${NEO_DEPLOY_DISABLE_FLAG:-/etc/neo/auto-deploy.disabled}"
  local ENGINE_UNIT="${NEO_DEPLOY_ENGINE_UNIT:-neo-user-angel-paper.service}"
  local GATEWAY_UNIT="${NEO_DEPLOY_GATEWAY_UNIT:-neo-user-gateway.service}"
  local LAB_UNIT="${NEO_DEPLOY_LAB_UNIT:-neo-strategy-lab.service}"
  local ENGINE_URL="${NEO_DEPLOY_ENGINE_URL:-http://127.0.0.1:18804}"
  local GATEWAY_URL="${NEO_DEPLOY_GATEWAY_URL:-http://127.0.0.1:8789}"
  local ENGINE_STATE_PATH="${NEO_DEPLOY_ENGINE_STATE_PATH:-}"
  local HEALTH_TIMEOUT="${NEO_DEPLOY_HEALTH_TIMEOUT_SECONDS:-90}"
  local SETTLE_SECONDS="${NEO_DEPLOY_SETTLE_SECONDS:-5}"
  local KEEP_ARCHIVES="${NEO_DEPLOY_KEEP_ARCHIVES:-10}"
  # Gateway-spawned account engines only pick up new engine code when the
  # gateway restarts. Set to 1 to restart the gateway on every engine change.
  local GATEWAY_ON_ENGINE_CHANGE="${NEO_DEPLOY_RESTART_GATEWAY_ON_ENGINE_CHANGE:-0}"
  local SYSTEMCTL="${NEO_DEPLOY_SYSTEMCTL:-systemctl}"
  local PYTHON="${NEO_DEPLOY_PYTHON:-python3}"
  local VERIFY_CMD="${NEO_DEPLOY_VERIFY_CMD:-}"
  local INSTALLED_COPY="${NEO_DEPLOY_INSTALLED_COPY:-/usr/local/sbin/neo-auto-deploy}"

  # Never hand the deploy lock (fd 9) to anything systemctl starts.
  sc() { "$SYSTEMCTL" "$@" 9>&-; }

  log() { printf '%s neo-auto-deploy: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

  write_status() {  # result, detail
    mkdir -p "$STATE_DIR"
    RESULT="$1" DETAIL="$2" FROM="${current:-}" TO="${target:-}" SERVICES="${restarted:-}" \
      "$PYTHON" - "$STATE_DIR/status.json" <<'PY'
import json, os, sys, tempfile, time
path = sys.argv[1]
data = {'checked_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'result': os.environ['RESULT'], 'detail': os.environ['DETAIL'],
        'from_commit': os.environ['FROM'], 'to_commit': os.environ['TO'],
        'restarted_services': os.environ['SERVICES'].split()}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, 'w') as handle:
    json.dump(data, handle, indent=2)
os.replace(tmp, path)
PY
  }

  fail() {  # result, message
    log "ERROR: $2"
    write_status "$1" "$2"
    exit 1
  }

  http_ok() {  # url -> exit 0 when it answers HTTP 200 with JSON
    "$PYTHON" - "$1" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
    json.load(response)
    sys.exit(0 if response.status == 200 else 1)
PY
  }

  wait_healthy() {  # url
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
    until http_ok "$1"; do
      [ "$(date +%s)" -ge "$deadline" ] && return 1
      sleep 2
    done
  }

  # "history_count|session_id|starting_balance|paper_only|strategy", or empty when unreachable.
  engine_facts() {
    "$PYTHON" - "$ENGINE_URL/state" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=20) as response:
    data = json.load(response)
stats, config = data.get('stats') or {}, data.get('config') or {}
print('|'.join(str(x) for x in (
    len(data.get('history') or []), stats.get('demo_session_id'),
    stats.get('demo_starting_balance_usd'), config.get('paper_only'), config.get('signal_strategy'))))
PY
  }

  engine_state_path() {
    if [ -n "$ENGINE_STATE_PATH" ]; then printf '%s\n' "$ENGINE_STATE_PATH"; return; fi
    local pid
    pid="$(sc show "$ENGINE_UNIT" -p MainPID --value 2>/dev/null || true)"
    if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -r "/proc/$pid/environ" ]; then
      tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^NEO_MARKET_STATE_PATH=//p' | head -n 1
      return
    fi
    sc show "$ENGINE_UNIT" -p Environment --value 2>/dev/null \
      | tr ' ' '\n' | sed -n 's/^NEO_MARKET_STATE_PATH=//p' | head -n 1
  }

  archive_ledger() {  # state.json path -> prints the archive directory
    local state_file="$1" account_dir archive name
    account_dir="$(dirname "$state_file")"
    archive="$account_dir/archives/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)-${target:0:12}"
    mkdir -p "$archive"
    for name in "$(basename "$state_file")" audit.jsonl all_time_history.json; do
      [ -f "$account_dir/$name" ] && cp -p "$account_dir/$name" "$archive/$name"
    done
    [ -f "$archive/$(basename "$state_file")" ] || return 1
    ( cd "$archive" && sha256sum -- * > SHA256SUMS )
    # Keep only the newest pre-deploy archives; other archives are never touched.
    find "$account_dir/archives" -maxdepth 1 -type d -name 'pre-deploy-*' | sort \
      | head -n -"$KEEP_ARCHIVES" | while IFS= read -r old; do rm -rf -- "$old"; done
    printf '%s\n' "$archive"
  }

  # A unit can answer on its port while systemd's own process has died (for
  # example when another process grabbed the port first). Health alone is not
  # proof, so the unit must also still be active a few seconds later.
  unit_settled() {  # unit
    sleep "$SETTLE_SECONDS"
    sc is-active --quiet "$1"
  }

  # One `restart` keeps the gap short: while the engine is down the gateway
  # could otherwise start its own copy on the same port. The engine goes
  # first so the gateway finds it healthy and reuses it.
  restart_changed_services() {
    if [ "$engine_changed" = 1 ]; then
      sc restart "$ENGINE_UNIT" || return 1
      wait_healthy "$ENGINE_URL/health" || return 1
      unit_settled "$ENGINE_UNIT" || return 1
    fi
    if [ "$gateway_changed" = 1 ]; then
      sc restart "$GATEWAY_UNIT" || return 1
      wait_healthy "$GATEWAY_URL/user/health" || return 1
      unit_settled "$GATEWAY_UNIT" || return 1
    fi
    if [ "$lab_changed" = 1 ]; then   # the lab has no HTTP endpoint; it must stay active
      sc restart "$LAB_UNIT" || return 1
      unit_settled "$LAB_UNIT" || return 1
    fi
  }

  rollback() {  # reason
    log "ROLLBACK: $1 — returning to $current"
    git -C "$REPO" reset --hard --quiet "$current"
    printf '%s\n' "$target" > "$STATE_DIR/failed_commit"
    if restart_changed_services; then
      fail rolled_back "$1; previous commit ${current:0:12} restored and healthy"
    fi
    fail rollback_unhealthy "$1; previous commit ${current:0:12} restored but services are NOT healthy"
  }

  # ---- preconditions ------------------------------------------------------
  [ -e "$DISABLE_FLAG" ] && { log "disabled by $DISABLE_FLAG"; exit 0; }
  mkdir -p "$STATE_DIR"
  exec 9>"$STATE_DIR/lock"
  flock -n 9 || { log "another deploy is running"; exit 0; }

  local current target restarted=""
  git -C "$REPO" fetch --quiet "$REMOTE" "$BRANCH"
  current="$(git -C "$REPO" rev-parse HEAD)"
  target="$(git -C "$REPO" rev-parse FETCH_HEAD)"
  [ "$current" = "$target" ] && exit 0

  if [ -f "$STATE_DIR/failed_commit" ] && [ "$(cat "$STATE_DIR/failed_commit")" = "$target" ]; then
    log "skipping ${target:0:12}: it already failed; waiting for a newer commit"
    exit 0
  fi
  if ! git -C "$REPO" diff --quiet || ! git -C "$REPO" diff --cached --quiet; then
    fail blocked_dirty_checkout "tracked files in $REPO have local changes; commit or discard them first"
  fi
  if ! git -C "$REPO" merge-base --is-ancestor "$current" "$target"; then
    fail blocked_diverged "$REPO is not behind $REMOTE/$BRANCH (local commits or a rewritten branch); refusing to deploy"
  fi

  local changed engine_changed=0 gateway_changed=0
  changed="$(git -C "$REPO" diff --name-only "$current" "$target")"
  # Any backend module restarts the engine unless it is known to belong to
  # another process, so a new engine module can never be silently skipped.
  if printf '%s\n' "$changed" | grep -E '^backend/[^/]+\.py$' \
      | grep -Ev '^backend/(user_gateway|live_tape|strategy_lab|lab_activity|lab_paired_[a-z_]+|astra[a-z0-9_]*|fomo_monitor|main_replay)\.py$' \
      | grep -q .; then engine_changed=1; fi
  if printf '%s\n' "$changed" | grep -Eq '^backend/(user_gateway|engine_runtime)\.py$'; then gateway_changed=1; fi
  if [ "$engine_changed" = 1 ] && [ "$GATEWAY_ON_ENGINE_CHANGE" = 1 ]; then gateway_changed=1; fi
  # The Strategy Lab is restarted only when its unit really runs this checkout;
  # a lab running from another tree is left alone.
  local lab_changed=0
  if printf '%s\n' "$changed" | grep -Eq '^backend/(strategy_lab|lab_activity|astra_lab_bridge|lab_paired_bridge|lab_dashboard_projection|pair_price_integrity|compat_file_lock)\.py$'; then
    if sc show "$LAB_UNIT" -p ExecStart --value 2>/dev/null | grep -Fq "$REPO/"; then
      lab_changed=1
    else
      log "lab code changed, but $LAB_UNIT does not run from $REPO; it is not restarted"
    fi
  fi
  log "new commit ${current:0:12} -> ${target:0:12}; engine_changed=$engine_changed gateway_changed=$gateway_changed lab_changed=$lab_changed"

  # ---- 1. test the exact commit before touching the live tree ---------------
  local worktree
  worktree="$(mktemp -d "${TMPDIR:-/tmp}/neo-deploy-verify.XXXXXX")"
  cleanup_worktree() {
    git -C "$REPO" worktree remove --force "$worktree" >/dev/null 2>&1 || true
    rm -rf -- "$worktree"
    git -C "$REPO" worktree prune >/dev/null 2>&1 || true
  }
  trap cleanup_worktree EXIT
  git -C "$REPO" worktree add --quiet --detach "$worktree" "$target"
  local verify_log="$STATE_DIR/verify-${target:0:12}.log" verified=0
  if [ -n "$VERIFY_CMD" ]; then
    ( cd "$worktree" && bash -c "$VERIFY_CMD" ) >"$verify_log" 2>&1 && verified=1
  else
    ( cd "$worktree" \
        && "$PYTHON" -m py_compile backend/*.py \
        && "$PYTHON" scripts/verify_strategy_lock.py \
        && "$PYTHON" scripts/run_python_checks.py ) >"$verify_log" 2>&1 && verified=1
  fi
  cleanup_worktree
  trap - EXIT
  if [ "$verified" != 1 ]; then
    printf '%s\n' "$target" > "$STATE_DIR/failed_commit"
    tail -n 25 "$verify_log" || true
    fail verification_failed "commit ${target:0:12} failed its checks; nothing was deployed (log: $verify_log)"
  fi
  find "$STATE_DIR" -maxdepth 1 -name 'verify-*.log' ! -name "verify-${target:0:12}.log" -delete
  log "commit ${target:0:12} passed its checks"

  # ---- 2. archive the ledger while the old engine still runs ------------------
  # state.json is replaced atomically and audit.jsonl is append-only, so a copy
  # taken from the running engine is consistent. The ledger itself is not touched.
  local before="" state_file="" archive=""
  if [ "$engine_changed" = 1 ]; then
    before="$(engine_facts)"
    state_file="$(engine_state_path)"
    [ -n "$state_file" ] && [ -f "$state_file" ] \
      || fail blocked_no_ledger_path "cannot locate the engine ledger (set NEO_DEPLOY_ENGINE_STATE_PATH); refusing to restart the engine without an archive"
    archive="$(archive_ledger "$state_file")" \
      || fail blocked_archive_failed "could not archive the ledger; nothing was deployed"
    restarted="$ENGINE_UNIT"
    log "ledger archived to $archive (before: ${before:-engine was not reachable})"
  fi
  [ "$gateway_changed" = 1 ] && restarted="${restarted:+$restarted }$GATEWAY_UNIT"
  [ "$lab_changed" = 1 ] && restarted="${restarted:+$restarted }$LAB_UNIT"

  # ---- 3-5. fast-forward, restart, verify ------------------------------------
  git -C "$REPO" merge --quiet --ff-only "$target" || rollback "fast-forward to ${target:0:12} failed"
  restart_changed_services || rollback "services did not become healthy on ${target:0:12}"

  if [ "$engine_changed" = 1 ]; then
    local after
    after="$(engine_facts)"
    [ -n "$after" ] || rollback "engine state is unreadable after the restart"
    local b_hist b_sess b_start a_hist a_sess a_start a_paper a_strategy _
    IFS='|' read -r a_hist a_sess a_start a_paper a_strategy <<<"$after"
    [ "$a_paper" = True ] || rollback "engine does not report paper_only=true"
    if [ -n "$before" ]; then
      IFS='|' read -r b_hist b_sess b_start _ _ <<<"$before"
      [ "$a_sess" = "$b_sess" ] || rollback "PAPER session changed ($b_sess -> $a_sess)"
      [ "$a_start" = "$b_start" ] || rollback "starting balance changed ($b_start -> $a_start)"
      [ "$a_hist" -ge "$b_hist" ] || rollback "history shrank ($b_hist -> $a_hist)"
    fi
    log "engine healthy: strategy=$a_strategy history=$a_hist session=$a_sess paper_only=$a_paper"
  fi

  rm -f "$STATE_DIR/failed_commit"
  write_status deployed "now at ${target:0:12}"
  log "deployed ${target:0:12}; restarted: ${restarted:-nothing (no service code changed)}"

  # Keep the installed copy of this script current (atomic replace).
  if [ -n "$INSTALLED_COPY" ] && [ -f "$INSTALLED_COPY" ] \
      && ! cmp -s "$REPO/deploy/vps-auto-deploy.sh" "$INSTALLED_COPY"; then
    install -m 0755 "$REPO/deploy/vps-auto-deploy.sh" "$INSTALLED_COPY.new" && mv -f "$INSTALLED_COPY.new" "$INSTALLED_COPY"
    log "updated $INSTALLED_COPY"
  fi
}

main "$@"; exit
