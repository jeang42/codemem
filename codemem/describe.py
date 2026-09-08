"""Draft a description and purpose for projects that lack one, using a local Ollama model.

Only touches own projects whose description is empty, tiny, or just the name. Everything it writes
is tagged `auto-described` so a human-written replacement is easy to spot and the draft is never
mistaken for a considered statement. Re-running skips projects already tagged.
"""
import json, urllib.request
from pathlib import Path
from . import config
from .db import q, one
from .store import upsert_project

MODEL = config.DESCRIBE_MODEL
PROMPT = """You are cataloguing a developer's personal projects. From the evidence below, write:
1. "description": one sentence, max 25 words, what the project IS (tool/app/library/experiment) and what it does. No marketing words.
2. "purpose": one sentence, max 25 words, why someone would reach for it, or what problem it solved.
If the evidence is too thin, say so in the description ("Unclear: ...") rather than guessing.
Answer with JSON only: {{"description": "...", "purpose": "..."}}

Project name: {name}
Languages: {languages}
Key files: {key_files}
Path: {path}
README / CLAUDE.md head:
{readme}
Recent commit subjects:
{commits}
"""


def candidates():
    return q("""SELECT p.id, p.name, p.description, p.tags, l.path, l.languages, l.key_files, l.readme_head
                FROM project p LEFT JOIN location l ON l.project_id=p.id AND l.machine=?
                WHERE p.origin='own' AND (p.description='' OR length(p.description)<25 OR p.description=p.name COLLATE NOCASE)
                AND (',' || p.tags || ',') NOT LIKE '%,auto-described,%' GROUP BY p.id ORDER BY p.name""", (config.MACHINE,))


def ask(prompt):
    req = urllib.request.Request(f"{config.OLLAMA_URL}/api/chat", data=json.dumps({
        "model": MODEL, "stream": False, "format": "json", "options": {"temperature": 0.2, "num_ctx": 8192},
        "messages": [{"role": "user", "content": prompt}]}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(json.load(r)["message"]["content"])


def describe_all(log=print, dry=False, limit=100):
    done = 0
    for c in candidates()[:limit]:
        commits = q('SELECT substr(message,1,90) AS m FROM "commit" WHERE project_id=? ORDER BY date DESC LIMIT 8', (c["id"],))
        readme = c["readme_head"] or ""
        if c["path"] and not readme:
            for f in ("README.md", "CLAUDE.md"):
                fp = Path(c["path"]) / f
                if fp.exists():
                    readme = "\n".join(fp.read_text(errors="replace").splitlines()[:25])[:2000]; break
        if not readme and not commits and not c["path"]:
            log(f"  {c['name']}: no evidence, skipped"); continue
        prompt = PROMPT.format(name=c["name"], languages=c["languages"] or "?", key_files=c["key_files"] or "?",
                               path=c["path"] or "(bare repo only)", readme=readme or "(none)",
                               commits="\n".join(x["m"].splitlines()[0] for x in commits) or "(none)")
        try:
            out = ask(prompt)
        except Exception as e:
            log(f"  {c['name']}: model error {e}"); continue
        desc, purpose = (out.get("description") or "").strip()[:300], (out.get("purpose") or "").strip()[:300]
        log(f"  {c['name']}: {desc}")
        if not dry and desc:
            tags = ",".join(t for t in [c["tags"], "auto-described"] if t)
            upsert_project(c["name"], description=desc, purpose=purpose, tags=tags)
            done += 1
    return done
