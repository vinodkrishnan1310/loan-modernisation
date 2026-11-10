#!/usr/bin/env bash
# Remove conversion outputs from main so Cursor always has work on mod/* branches.
# Idempotent: no commit when nothing to remove.
#
#   bash deploy/strip_replay_baseline.sh              # commit and push origin/main
#   bash deploy/strip_replay_baseline.sh --no-push    # commit locally only
#   bash deploy/strip_replay_baseline.sh --dry-run    # print paths that would go
#
set -euo pipefail
cd "$(dirname "$0")/.."

MANIFEST="$(dirname "$0")/replay_baseline_paths.txt"
COMMIT_MSG="demo: reset replay baseline (strip module conversion from main)"

push=true
dry_run=false
for arg in "$@"; do
  case "$arg" in
    --no-push) push=false ;;
    --dry-run) dry_run=true ;;
    *) echo "unknown option: $arg (try --no-push, --dry-run)" >&2; exit 2 ;;
  esac
done

if [ ! -f "$MANIFEST" ]; then
  echo "strip_replay_baseline: missing manifest: $MANIFEST" >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "strip_replay_baseline: not a git repository" >&2
  exit 1
fi

if ! git show-ref --verify --quiet refs/heads/main 2>/dev/null \
  && ! git show-ref --verify --quiet refs/remotes/origin/main 2>/dev/null; then
  echo "strip_replay_baseline: no main branch" >&2
  exit 1
fi

git checkout main 2>/dev/null || git checkout -f main
git pull --ff-only origin main 2>/dev/null \
  || echo "warn: git pull origin main failed (offline?); using local main" >&2

if [ -n "$(git status --porcelain)" ]; then
  echo "strip_replay_baseline: working tree is not clean; stash or commit first" >&2
  git status --short >&2
  exit 1
fi

removed=()
while IFS= read -r path || [ -n "${path:-}" ]; do
  path="${path%%#*}"
  path="${path#"${path%%[![:space:]]*}"}"
  path="${path%"${path##*[![:space:]]}"}"
  [ -n "$path" ] || continue

  if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
    if $dry_run; then
      echo "  would git rm: $path"
      removed+=("$path")
      continue
    fi
    git rm -f -- "$path"
    removed+=("$path")
  elif [ -e "$path" ]; then
    if $dry_run; then
      echo "  would delete untracked: $path"
      removed+=("$path")
      continue
    fi
    rm -f -- "$path"
    removed+=("$path")
  fi
done < "$MANIFEST"

if [ "${#removed[@]}" -eq 0 ]; then
  echo "replay baseline already clean on main"
  exit 0
fi

if $dry_run; then
  echo "dry-run: ${#removed[@]} path(s); no commit"
  exit 0
fi

if git diff --cached --quiet; then
  echo "replay baseline: no tracked changes to commit"
  exit 0
fi

git commit -m "$COMMIT_MSG"
echo "committed replay baseline strip (${#removed[@]} path(s))"

if $push; then
  git push origin main
  echo "pushed origin/main"
else
  echo "left local main unpushed (--no-push)"
fi
