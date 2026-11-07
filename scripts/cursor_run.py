#!/usr/bin/env python3
"""Launch a Cursor cloud agent, wait for the run to finish, print the pull request URL.

  python3 scripts/cursor_run.py --repo https://github.com/OWNER/loan-modernisation --ref main \
      --prompt-file prompts/cursor/run_b_conversion.md

Needs CURSOR_API_KEY in the environment. Uses only the standard library, so any machine can run it.
Cursor's Cloud Agents API is in public beta; field names may change. If a step fails, the script
prints Cursor's answer so you can see what changed.
Exit codes: 0 finished with a PR, 3 finished without a PR, 4 ended without finishing, 5 timed out.
"""
import argparse, base64, json, os, sys, time, urllib.error, urllib.request

BASE = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com/v1")
TERMINAL = {"FINISHED", "FAILED", "ERROR", "CANCELLED", "CANCELED", "EXPIRED"}


def call(method, path, key, body=None):
    req = urllib.request.Request(
        BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(f"{key}:".encode()).decode(),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from Cursor on {method} {path}: {e.read().decode()[:600]}")


def find_all(o, key):
    found = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key and isinstance(v, str) and v:
                found.append(v)
            found += find_all(v, key)
    elif isinstance(o, list):
        for x in o:
            found += find_all(x, key)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the run")
    ap.add_argument("--interval", type=int, default=10)
    a = ap.parse_args()

    key = os.environ.get("CURSOR_API_KEY")
    if not key:
        sys.exit("CURSOR_API_KEY is not set")
    prompt = open(a.prompt_file).read()

    created = call("POST", "/agents", key, {
        "prompt": {"text": prompt},
        "repos": [{"url": a.repo, "startingRef": a.ref}],
        "autoCreatePR": True})
    agent = created.get("agent", {})
    run = created.get("run", {})
    agent_id = agent.get("id")
    run_id = run.get("id") or agent.get("latestRunId")
    if not agent_id or not run_id:
        sys.exit("Unexpected create response: " + json.dumps(created)[:600])
    print(f"agent {agent_id} run {run_id} started; page: {agent.get('url', 'n/a')}", flush=True)

    start, last = time.time(), None
    while True:
        status_obj = call("GET", f"/agents/{agent_id}/runs/{run_id}", key)
        status = status_obj.get("status") or (status_obj.get("run") or {}).get("status")
        if status != last:
            print(f"[{int(time.time() - start):>4}s] status {status}", flush=True)
            last = status
        if status in TERMINAL:
            break
        if time.time() - start > a.timeout:
            print(f"timed out after {a.timeout}s; the agent may still be running: {agent.get('url', agent_id)}")
            sys.exit(5)
        time.sleep(a.interval)

    prs = find_all(status_obj, "prUrl")
    if status != "FINISHED":
        print("run ended with status", status, json.dumps(status_obj)[:600])
        sys.exit(4)
    if not prs:
        print("finished, but no prUrl in the response; check the agent page:", agent.get("url", agent_id))
        print(json.dumps(status_obj)[:800])
        sys.exit(3)
    print("PR:", prs[0])


if __name__ == "__main__":
    main()
