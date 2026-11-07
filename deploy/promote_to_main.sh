#!/usr/bin/env bash
# Merge an approved programme commit onto origin/main and push.
# Used by Release before tag_release when conversion merged on mod/* only.
#
#   bash deploy/promote_to_main.sh <commit> [branch]
#
# branch is optional (e.g. mod/prepayment-quote). If merge of <commit> fails, retries
# merging origin/<branch> into main.
#
# Prints one line: the new origin/main SHA (after fetch; after push if promoted).
# Exit 0 if commit is already on main or promote succeeded; non-zero on failure.
#
set -euo pipefail
cd "$(dirname "$0")/.."

commit="${1:?usage: promote_to_main.sh <commit> [branch]}"
branch="${2:-}"

git fetch --quiet origin main
if [ -n "$branch" ]; then
  git fetch --quiet origin "$branch" || true
fi

if git cat-file -e "${commit}^{commit}" 2>/dev/null; then
  :
elif [ -n "$branch" ] && git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
  commit="$(git rev-parse "origin/$branch")"
else
  echo "promote_to_main: unknown commit $commit" >&2
  exit 1
fi

main_sha="$(git rev-parse origin/main)"
if git merge-base --is-ancestor "$commit" origin/main 2>/dev/null; then
  echo "$main_sha"
  exit 0
fi

prev="$(git rev-parse HEAD 2>/dev/null || true)"
git checkout -B main origin/main

if ! git merge --no-ff "$commit" -m "release: promote programme commit to main" 2>/dev/null; then
  git merge --abort 2>/dev/null || true
  git reset --hard origin/main
  if [ -z "$branch" ]; then
    git checkout "${prev:--}" 2>/dev/null || true
    echo "promote_to_main: merge of $commit into main failed" >&2
    exit 2
  fi
  if ! git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
    git checkout "${prev:--}" 2>/dev/null || true
    echo "promote_to_main: branch origin/$branch not found" >&2
    exit 3
  fi
  if ! git merge --no-ff "origin/$branch" -m "release: promote $branch to main"; then
    git merge --abort 2>/dev/null || true
    git checkout "${prev:--}" 2>/dev/null || true
    echo "promote_to_main: merge of origin/$branch into main failed" >&2
    exit 4
  fi
fi

git push --quiet origin main
git fetch --quiet origin main
git rev-parse origin/main
