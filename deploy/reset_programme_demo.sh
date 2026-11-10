#!/usr/bin/env bash
# Reset laptop + programme artefacts for a fresh Slack playbook replay.
# By default deletes every local and remote branch except main (mod/*, cursor/*, evidence/*, …).
# With --api, also strips module conversion outputs from main and pushes (see --replay-baseline).
# Does not delete Docker volumes.
#
#   bash deploy/reset_programme_demo.sh              # local + delete all non-main branches on origin
#   bash deploy/reset_programme_demo.sh --api        # above + replay baseline on main + ENGINE=legacy api
#   bash deploy/reset_programme_demo.sh --no-git     # skip git branch cleanup (laptop only)
#   bash deploy/reset_programme_demo.sh --api --no-git
#   bash deploy/reset_programme_demo.sh --api --no-replay-baseline   # do not strip/push main
#   bash deploy/reset_programme_demo.sh --replay-baseline            # strip main without other steps' git pull order change
#
# Order matters: main is stripped (git rm, commit, push) before the local working files are wiped,
# so the tree is not left dirty when the strip runs.
#
set -euo pipefail
cd "$(dirname "$0")/.."

reset_api=false
reset_git=true
replay_baseline=false
replay_baseline_explicit=false
for arg in "$@"; do
  case "$arg" in
    --api) reset_api=true ;;
    --no-git) reset_git=false ;;
    --replay-baseline) replay_baseline=true; replay_baseline_explicit=true ;;
    --no-replay-baseline) replay_baseline=false; replay_baseline_explicit=true ;;
    *) echo "unknown option: $arg (try --api, --no-git, --replay-baseline, --no-replay-baseline)" >&2; exit 2 ;;
  esac
done

# B5 bot replay needs origin/main without finished conversions when the API is reset.
if $reset_api && ! $replay_baseline_explicit; then
  replay_baseline=true
fi

echo "== demo loans 9001–9004 =="
bash deploy/reset_demo.sh

if $reset_git; then
  echo "== git branches (delete all except main, local + origin) =="
  git fetch --prune origin 2>/dev/null || echo "warn: git fetch origin failed (network/auth?); skipping remote deletes" >&2

  trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
  }

  while IFS= read -r branch; do
    branch="$(trim "$branch")"
    [ -n "$branch" ] || continue
    name="${branch#origin/}"
    case "$name" in main|HEAD) continue ;; esac
    echo "  delete remote $name"
    git push origin --delete "$name" || echo "    warn: could not delete origin/$name" >&2
  done < <(git for-each-ref --format='%(refname:short)' refs/remotes/origin 2>/dev/null || true)

  if git show-ref --verify --quiet refs/heads/main; then
    git checkout main 2>/dev/null || git checkout -f main
  fi

  while IFS= read -r branch; do
    branch="$(trim "$branch")"
    [ -n "$branch" ] || continue
    [ "$branch" = main ] && continue
    echo "  delete local $branch"
    git branch -D "$branch" 2>/dev/null || echo "    warn: could not delete local $branch" >&2
  done < <(git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null || true)

  git pull --ff-only origin main 2>/dev/null || echo "warn: git pull main failed" >&2
else
  echo "== git: skipped (--no-git) =="
fi

if $replay_baseline; then
  echo "== replay baseline on main (strip conversion, push origin) =="
  bash deploy/strip_replay_baseline.sh
fi

echo "== programme working files =="
rm -f rules/*.rules.json rules/*.rules.json.backup 2>/dev/null || true
rm -rf evidence/* 2>/dev/null || true
mkdir -p evidence rules
rm -f legacy/incoming/*.sql 2>/dev/null || true
mkdir -p legacy/incoming
touch legacy/incoming/.gitkeep
rm -f state/*.json 2>/dev/null || true
mkdir -p state

if $reset_api; then
  echo "== api ENGINE=legacy =="
  ENGINE=legacy docker compose up -d --build api
  curl -s localhost:8000/health || true
  echo
fi

cat <<'EOF'

Programme demo reset done.

Next: fresh thread in #loan-modernisation, attach <module>_bundle.sql, @ Programme Lead (SINGLE_MODULE).
Replay order: MODULE2_DEMO_STEPS.md, B5_BOT_SETUP.md.
EOF
