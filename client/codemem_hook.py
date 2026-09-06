#!/usr/bin/env python3
"""Claude Code hook for codemem. Stdlib only, runs on Linux, macOS and Windows.

SessionStart : GET  /brief?path=<cwd>&machine=<host>  -> printed to stdout, which Claude Code adds to context
SessionEnd   : POST /session/end with a compact summary read from the transcript

Never fails the session: any error is swallowed and the hook exits 0.
Config: CODEMEM_URL (default http://localhost:8055), CODEMEM_MACHINE (default hostname).
"""
import json, os, socket, sys, urllib.parse, urllib.request

URL = os.environ.get("CODEMEM_URL", "http://localhost:8055").rstrip("/")
MACHINE = os.environ.get("CODEMEM_MACHINE", socket.gethostname().split(".")[0])
TIMEOUT = 4


def _get(path):
    with urllib.request.urlopen(f"{URL}{path}", timeout=TIMEOUT) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _post(path, data):
    req = urllib.request.Request(f"{URL}{path}", data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status


def session_start(ev):
    cwd = ev.get("cwd") or os.getcwd()
    status, text = _get(f"/brief?path={urllib.parse.quote(cwd)}&machine={urllib.parse.quote(MACHINE)}")
    if status == 200 and text.strip():
        print(text)
    else:
        print(f"codemem: no record of {cwd} on {MACHINE}. When you know what this project is, call update_project; "
              f"search codemem before building utilities; log_session when done.")


def _read_transcript(path):
    """Return (first user prompt, last assistant text, turns) from a Claude Code transcript .jsonl."""
    first, last, turns = "", "", 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                m = o.get("message") or {}
                role = m.get("role") or o.get("type")
                content = m.get("content")
                if isinstance(content, list):
                    text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                else:
                    text = content if isinstance(content, str) else ""
                if not text.strip():
                    continue
                if role == "user":
                    turns += 1
                    if not first and not text.startswith("<") and not o.get("isMeta"):
                        first = text
                elif role == "assistant":
                    last = text
    except OSError:
        pass
    return first, last, turns


def session_end(ev):
    first, last, turns = _read_transcript(ev.get("transcript_path", ""))
    if not (first or last):
        return
    _post("/session/end", {"machine": MACHINE, "path": ev.get("cwd", ""), "session_id": ev.get("session_id", ""),
                           "first_prompt": first[:2000], "last_reply": last[:4000], "turns": turns})


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        ev = {}
    name = ev.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "")
    try:
        if name == "SessionStart":
            session_start(ev)
        elif name == "SessionEnd":
            session_end(ev)
    except Exception as e:  # never break a session over memory
        print(f"codemem hook: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
