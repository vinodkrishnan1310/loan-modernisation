#!/usr/bin/env python3
"""Derive module_id, git branch, incoming path, and rules path from a slack-drop bundle name.

  python3 scripts/branch_from_bundle.py prepayment_quote_bundle.sql
  python3 scripts/branch_from_bundle.py slack-drop/rate_reset_notice_bundle.sql
"""
from __future__ import annotations

import argparse
from pathlib import Path


def derive(name: str) -> dict[str, str]:
    base = Path(name).name
    if not base.endswith(".sql"):
        raise SystemExit("expected a .sql filename")
    stem = base[: -len(".sql")]
    if stem.endswith("_bundle"):
        module_id = stem[: -len("_bundle")]
    else:
        module_id = stem
    branch = "mod/" + module_id.replace("_", "-")
    incoming = f"legacy/incoming/{module_id}_bundle.sql"
    rules = f"rules/{module_id}.rules.json"
    return {
        "module_id": module_id,
        "branch": branch,
        "incoming": incoming,
        "rules": rules,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle", help="e.g. prepayment_quote_bundle.sql")
    args = p.parse_args()
    d = derive(args.bundle)
    for k, v in d.items():
        print(f"{k}={v}")


if __name__ == "__main__":
    main()
