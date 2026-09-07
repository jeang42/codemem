"""codemem command line: serve, backfill, gitea, docs, seed, scan, embed, reindex, backup, sync."""
import argparse, gzip, shutil, sys, time
from pathlib import Path
from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="codemem", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the MCP server + web UI")
    b = sub.add_parser("backfill", help="ingest all commits from every bare repo on the git server")
    b.add_argument("repos", nargs="*")
    sub.add_parser("gitea", help="pull descriptions/topics/urls from Gitea")
    sub.add_parser("docs", help="(re)ingest markdown knowledge sources")
    sub.add_parser("seed", help="seed howto notes and known assets")
    s = sub.add_parser("scan", help="scan local directories for projects")
    s.add_argument("roots", nargs="*", help="directories (default: registered scan roots, else config defaults)")
    e = sub.add_parser("embed", help="embed index rows that lack vectors")
    e.add_argument("--all", action="store_true", help="keep going until nothing is pending")
    sub.add_parser("reindex", help="rebuild the FTS index from source tables (drops embeddings)")
    bk = sub.add_parser("backup", help="consistent gzip copy of the database")
    bk.add_argument("--dest", default=str(Path.home() / ".codemem" / "backups"))
    bk.add_argument("--keep", type=int, default=30)
    sub.add_parser("sync", help="docs + gitea + scan + embed: the timer job")
    d = sub.add_parser("describe", help="draft descriptions for own projects that lack one (Ollama, tagged auto-described)")
    d.add_argument("--dry-run", action="store_true")
    dc = sub.add_parser("discover", help="mine own repos for reusable assets (tagged auto-discovered) and shared code links")
    dc.add_argument("repos", nargs="*"); dc.add_argument("--no-describe", action="store_true", help="skip model drafts for files without a docstring")
    rv = sub.add_parser("review", help="targeted model review: near-duplicate confirmation, thin descriptions on trusted assets")
    rv.add_argument("--stage", type=int, choices=[1, 2], default=0, help="run one stage only (default both)")
    rv.add_argument("--limit", type=int, default=40); rv.add_argument("--dry-run", action="store_true")
    sub.add_parser("trust", help="recompute trust scores for all projects and assets")
    sub.add_parser("stats")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import run; run()
    elif a.cmd == "backfill":
        from .gitsync import backfill
        t = time.time(); n = backfill(a.repos or None); print(f"{n} new commits in {time.time()-t:.1f}s")
    elif a.cmd == "gitea":
        from .gitsync import gitea_sync; gitea_sync()
    elif a.cmd == "docs":
        from .knowledge import ingest_docs; ingest_docs()
    elif a.cmd == "seed":
        from .knowledge import seed; seed()
    elif a.cmd == "scan":
        from .scan import scan_local; print(scan_local(a.roots or None))
    elif a.cmd == "embed":
        from .search import embed_pending
        total = 0
        while True:
            n = embed_pending(); total += n; print(f"  embedded {n}")
            if not n or not a.all:
                break
        print(f"{total} embedded")
    elif a.cmd == "reindex":
        from .store import reindex_all; reindex_all(); print("reindexed")
    elif a.cmd == "backup":
        backup(Path(a.dest), a.keep)
    elif a.cmd == "sync":
        from .knowledge import ingest_docs, seed
        from .gitsync import gitea_sync, backfill
        from .scan import scan_local
        from .search import embed_pending
        print("docs"); ingest_docs()
        print("seed"); seed()
        print("gitea"); gitea_sync()
        print("backfill (catches anything the hook missed)"); backfill(log=lambda *_: None)
        print("scan"); print(" ", scan_local())
        from .discover import discover
        print("discover"); print(" ", discover(describe=True, log=lambda *_: None))
        from .trust import compute_all
        print("trust"); compute_all()
        print("embed"); 
        while embed_pending(): pass
        print("done")
    elif a.cmd == "discover":
        from .discover import discover; print(discover(a.repos or None, describe=not a.no_describe))
    elif a.cmd == "review":
        from .review import stage1, stage2
        if a.stage in (0, 1): stage1(a.limit, a.dry_run)
        if a.stage in (0, 2): stage2(a.limit, a.dry_run)
        from .trust import compute_all; compute_all()
    elif a.cmd == "trust":
        from .trust import compute_all; compute_all()
    elif a.cmd == "describe":
        from .describe import describe_all; print(f"{describe_all(dry=a.dry_run)} described")
    elif a.cmd == "stats":
        import json
        from .server import stats; print(json.dumps(stats(), indent=1))


def backup(dest: Path, keep: int):
    import sqlite3
    from .db import connect
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    tmp = dest / f"codemem-{stamp}.db"
    dst = sqlite3.connect(str(tmp))
    connect().backup(dst)
    dst.close()
    with open(tmp, "rb") as f, gzip.open(f"{tmp}.gz", "wb") as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    old = sorted(dest.glob("codemem-*.db.gz"))[:-keep]
    for o in old:
        o.unlink()
    print(f"backup -> {tmp}.gz ({len(old)} pruned)")


if __name__ == "__main__":
    main()
