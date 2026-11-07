#!/usr/bin/env bash
# Creates the tag that the laptop watcher deploys. The release bot runs this (or does the same
# through the GitHub connector) after the release manager approves in Slack.
#   bash deploy/release.sh release  "approved by <name> in Slack, evidence evidence/release_pack.md"
#   bash deploy/release.sh rollback "reason"
set -euo pipefail
kind="${1:?usage: release.sh release|rollback \"message\"}"
msg="${2:?give a message: who approved and why}"
case "$kind" in release|rollback) ;; *) echo "kind must be release or rollback" >&2; exit 2 ;; esac
cd "$(dirname "$0")/.."
git fetch --quiet origin main
tag="$kind-$(date -u +%Y%m%d-%H%M%S)"
git tag -a "$tag" origin/main -m "$msg"
git push --quiet origin "$tag"
echo "$tag"
