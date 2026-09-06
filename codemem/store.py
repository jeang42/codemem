"""Write-side helpers. Every mutation goes through here so the search index stays in step."""
import os
from . import db
from .db import tx, q, one, now, index_item, tags_norm


def project_name_from_remote(url):
    if not url:
        return None
    name = url.rstrip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def get_project(name_or_id):
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
        return one("SELECT * FROM project WHERE id=?", (int(name_or_id),))
    return one("SELECT * FROM project WHERE name=? COLLATE NOCASE", (name_or_id,))


def upsert_project(name, **fields):
    """Create or update a project. Only non-empty fields overwrite existing values."""
    with tx() as c:
        row = one("SELECT * FROM project WHERE name=? COLLATE NOCASE", (name,))
        if "tags" in fields:
            fields["tags"] = tags_norm(fields["tags"])
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
        if row:
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE project SET {sets}, updated_at=? WHERE id=?",
                          (*fields.values(), now(), row["id"]))
            pid = row["id"]
        else:
            cols = ["name", *fields.keys(), "created_at", "updated_at"]
            c.execute(f"INSERT INTO project({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      (name, *fields.values(), now(), now()))
            pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        p = one("SELECT * FROM project WHERE id=?", (pid,))
        _index_project(c, p)
        if "audience" in fields:
            c.execute("UPDATE search_index SET audience=? WHERE project=? COLLATE NOCASE", (fields["audience"], p["name"]))
        return p


def _index_project(c, p):
    body = "\n".join(x for x in [p["description"], p["purpose"], "languages: " + (p["languages"] or ""),
                                  p["remote_url"], p["gitea_url"], p["github_url"]] if x)
    index_item(c, "project", p["id"], p["name"], body, p["tags"], p["name"])


def upsert_location(project_id, machine, path, **fields):
    with tx() as c:
        c.execute("""INSERT INTO location(project_id, machine, path, last_scanned) VALUES (?,?,?,?)
                     ON CONFLICT(machine, path) DO UPDATE SET project_id=excluded.project_id,
                     last_scanned=excluded.last_scanned""", (project_id, machine, path, now()))
        fields = {k: v for k, v in fields.items() if v is not None}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE location SET {sets} WHERE machine=? AND path=?",
                      (*fields.values(), machine, path))
        loc = one("SELECT * FROM location WHERE machine=? AND path=?", (machine, path))
        p = one("SELECT name FROM project WHERE id=?", (project_id,))
        body = f"{machine}:{path}\n{loc['readme_head'] or ''}\nkey files: {loc['key_files'] or ''}\nlanguages: {loc['languages'] or ''}"
        index_item(c, "location", loc["id"], f"{p['name']} on {machine}", body, "", p["name"])
        return loc


def upsert_asset(name, kind, project=None, **fields):
    pid = None
    if project:
        p = get_project(project) or upsert_project(project)
        pid = p["id"]
    with tx() as c:
        if "tags" in fields:
            fields["tags"] = tags_norm(fields["tags"])
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
        row = one("SELECT * FROM asset WHERE name=? AND kind=?", (name, kind))
        if row:
            fields["project_id"] = pid or row["project_id"]
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE asset SET {sets}, updated_at=? WHERE id=?", (*fields.values(), now(), row["id"]))
            aid = row["id"]
        else:
            cols = ["name", "kind", "project_id", *fields.keys(), "created_at", "updated_at"]
            c.execute(f"INSERT INTO asset({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      (name, kind, pid, *fields.values(), now(), now()))
            aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        a = one("SELECT a.*, p.name AS project FROM asset a LEFT JOIN project p ON p.id=a.project_id WHERE a.id=?", (aid,))
        body = "\n".join(x for x in [f"kind: {kind}", a["description"], "usage: " + (a["usage"] or ""),
                                      f"{a['machine']}:{a['path']}" if a["path"] else ""] if x)
        index_item(c, "asset", aid, name, body, a["tags"], a["project"] or "")
        return a


def add_note(kind, title, body, project=None, tags="", machine="", session_id="", path=""):
    pid = None
    pname = ""
    if project:
        p = get_project(project) or upsert_project(project)
        pid, pname = p["id"], p["name"]
    with tx() as c:
        c.execute("""INSERT INTO note(project_id, kind, title, body, tags, machine, session_id, path, created_at)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (pid, kind, title, body, tags_norm(tags), machine, session_id, path, now()))
        nid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        index_item(c, "note", nid, f"[{kind}] {title}", body, tags_norm(tags), pname)
        return one("SELECT * FROM note WHERE id=?", (nid,))


def add_link(from_kind, from_id, to_kind, to_id, relation, note=""):
    with tx() as c:
        c.execute("""INSERT OR REPLACE INTO link(from_kind, from_id, to_kind, to_id, relation, note, created_at)
                     VALUES (?,?,?,?,?,?,?)""", (from_kind, from_id, to_kind, to_id, relation, note, now()))


def upsert_doc(source, title, body, digest):
    with tx() as c:
        row = one("SELECT * FROM doc WHERE source=?", (source,))
        if row and row["hash"] == digest:
            return row, False
        if row:
            c.execute("UPDATE doc SET title=?, body=?, hash=?, updated_at=? WHERE id=?",
                      (title, body, digest, now(), row["id"]))
            did = row["id"]
        else:
            c.execute("INSERT INTO doc(source, title, body, hash, updated_at) VALUES (?,?,?,?,?)",
                      (source, title, body, digest, now()))
            did = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        index_item(c, "doc", did, title, body, "", "")
        return one("SELECT * FROM doc WHERE id=?", (did,)), True


def upsert_scan_root(machine, path, note="", enabled=1):
    with tx() as c:
        c.execute("""INSERT INTO scan_root(machine, path, note, enabled) VALUES (?,?,?,?)
                     ON CONFLICT(machine, path) DO UPDATE SET note=excluded.note, enabled=excluded.enabled""",
                  (machine, path, note, enabled))
    return one("SELECT * FROM scan_root WHERE machine=? AND path=?", (machine, path))


def reindex_all():
    with tx() as c:
        c.execute("DELETE FROM search_index")
        c.execute("DELETE FROM embedding")
        for p in q("SELECT * FROM project"):
            _index_project(c, p)
        for loc in q("SELECT l.*, p.name AS pname FROM location l JOIN project p ON p.id=l.project_id"):
            body = f"{loc['machine']}:{loc['path']}\n{loc['readme_head'] or ''}\nkey files: {loc['key_files'] or ''}\nlanguages: {loc['languages'] or ''}"
            index_item(c, "location", loc["id"], f"{loc['pname']} on {loc['machine']}", body, "", loc["pname"])
        for a in q("SELECT a.*, p.name AS project FROM asset a LEFT JOIN project p ON p.id=a.project_id"):
            body = "\n".join(x for x in [f"kind: {a['kind']}", a["description"], "usage: " + (a["usage"] or ""),
                                          f"{a['machine']}:{a['path']}" if a["path"] else ""] if x)
            index_item(c, "asset", a["id"], a["name"], body, a["tags"], a["project"] or "")
        for n in q("SELECT n.*, p.name AS project FROM note n LEFT JOIN project p ON p.id=n.project_id"):
            index_item(c, "note", n["id"], f"[{n['kind']}] {n['title']}", n["body"], n["tags"], n["project"] or "")
        for d in q("SELECT * FROM doc"):
            index_item(c, "doc", d["id"], d["title"], d["body"], "", "")
        for cm in q('SELECT c.*, p.name AS project FROM "commit" c JOIN project p ON p.id=c.project_id'):
            index_item(c, "commit", cm["id"], f"{cm['project']} {cm['hash'][:8]}: {cm['message'].splitlines()[0] if cm['message'] else ''}",
                       (cm["message"] or "") + "\nfiles: " + (cm["files"] or ""), "", cm["project"])
