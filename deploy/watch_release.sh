#!/usr/bin/env bash
# Runs on the laptop. Watches the git remote for release tags and redeploys ONLY the API container.
#
#   release-*   -> deploy the tagged commit with ENGINE=service   (cutover)
#   rollback-*  -> deploy the tagged commit with ENGINE=legacy    (rollback)
#
# The release bot creates an annotated tag after the release manager approves in Slack.
# If a release fails its health or smoke check, the watcher puts ENGINE=legacy back by itself.
# Run this from a dedicated clone, not the folder you edit in:
#   git clone <repo-url> ~/deploy/loan-modernisation && cd ~/deploy/loan-modernisation
#   bash deploy/watch_release.sh
#
# Settings (environment): INTERVAL seconds between checks (10), API_URL (http://localhost:8000),
#   SLACK_WEBHOOK_URL (optional) a Slack incoming webhook; if set, every deploy result is posted there,
#   DB_RESET_CMD command that re-creates the smoke-test loan (default: deploy/reset_demo.sh)
#
# The whole script sits inside main() and ends with `exit`, so bash has read all of it before
# `git checkout` replaces this file underneath it.
main() {
  set -uo pipefail
  cd "$(dirname "$0")/.."
  local INTERVAL="${INTERVAL:-10}"
  local API_URL="${API_URL:-http://localhost:8000}"
  local RESET_CMD="${DB_RESET_CMD:-bash deploy/reset_demo.sh}"
  local STATE=deploy/.last_deployed LOG=deploy/deploy.log RESULT=deploy/last_result.json

  log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

  notify() {   # optional: post to a Slack incoming webhook so the result shows up in the channel
    [ -n "${SLACK_WEBHOOK_URL:-}" ] || return 0
    curl -fsS -X POST -H 'content-type: application/json' --data "{\"text\":\"$1\"}" \
      "$SLACK_WEBHOOK_URL" >/dev/null 2>&1 || log "slack notification failed"
  }

  write_result() {   # tag engine status detail
    local detail="${4//\"/\'}"
    printf '{"tag":"%s","engine":"%s","status":"%s","detail":"%s","at":"%s"}\n' \
      "$1" "$2" "$3" "$detail" "$(date -u +%FT%TZ)" > "$RESULT"
    notify "Deploy of $1 (ENGINE=$2): $3. $detail"
  }

  revert_to_legacy() {   # a failed release must not leave the broken engine serving traffic
    log "reverting to ENGINE=legacy"
    ENGINE=legacy docker compose up -d --build api >/dev/null 2>&1 || { log "revert failed, fix by hand"; return 1; }
    local i h=""
    for i in $(seq 1 30); do
      h="$(curl -fsS "$API_URL/health" 2>/dev/null || true)"
      echo "$h" | grep -q '"engine":"legacy"' && { log "legacy engine is serving again"; return 0; }
      sleep 2
    done
    log "legacy engine did not come back, fix by hand"
  }

  deploy_tag() {
    local tag="$1" engine
    case "$tag" in
      release-*)  engine=service ;;
      rollback-*) engine=legacy ;;
      *) return 0 ;;
    esac
    log "deploying $tag with ENGINE=$engine"

    if ! git checkout --quiet "tags/$tag"; then
      log "checkout failed for $tag"; write_result "$tag" "$engine" failed "checkout failed"; return 1
    fi
    if ! ENGINE="$engine" docker compose up -d --build api; then
      log "docker compose failed"; write_result "$tag" "$engine" failed "docker compose failed"; return 1
    fi

    local i health=""
    for i in $(seq 1 30); do
      health="$(curl -fsS "$API_URL/health" 2>/dev/null || true)"
      [ -n "$health" ] && break
      sleep 2
    done
    if [ -z "$health" ]; then
      log "API did not become healthy"; write_result "$tag" "$engine" failed "no health response, reverted to legacy"
      [ "$engine" = service ] && revert_to_legacy; return 1
    fi
    if ! echo "$health" | grep -q "\"engine\":\"$engine\""; then
      log "health says $health but expected engine=$engine"; write_result "$tag" "$engine" failed "wrong engine: $health"
      [ "$engine" = service ] && revert_to_legacy; return 1
    fi

    $RESET_CMD >/dev/null 2>&1 || { log "could not reset smoke loan"; write_result "$tag" "$engine" failed "smoke reset failed"; return 1; }
    local code
    code="$(curl -s -o /tmp/smoke_out.json -w '%{http_code}' -X POST "$API_URL/loans/9002/eod" \
            -H 'content-type: application/json' -d '{"as_of":"2024-03-31"}')"
    if [ "$code" != "200" ]; then
      log "smoke call returned HTTP $code: $(cat /tmp/smoke_out.json)"
      write_result "$tag" "$engine" failed "smoke HTTP $code, reverted to legacy"
      [ "$engine" = service ] && revert_to_legacy; return 1
    fi

    echo "$tag" > "$STATE"
    log "deployed $tag, ENGINE=$engine, smoke call OK"
    write_result "$tag" "$engine" deployed "smoke call OK"
  }

  log "watching for release-* and rollback-* tags every ${INTERVAL}s"
  while true; do
    git fetch --tags --quiet origin 2>/dev/null || log "git fetch failed"
    local latest
    latest="$(git tag --list 'release-*' 'rollback-*' --sort=-creatordate | head -n1)"
    if [ -n "$latest" ] && [ "$latest" != "$(cat "$STATE" 2>/dev/null || true)" ]; then
      deploy_tag "$latest" || true
      # a failed tag is not retried in a loop; fix the problem and tag again with a new name
      echo "$latest" > "$STATE"
    fi
    [ "${ONCE:-0}" = "1" ] && return 0
    sleep "$INTERVAL"
  done
}
main "$@"
exit $?
