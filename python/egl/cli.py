from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .api import EmbedClient, content_item
from .config import Settings
from .indexer import Indexer
from .search import Searcher
from .store import CorruptIndexError, Index
from .webui import serve

EPILOG = """examples:
  python run.py index ~/Pictures
  python run.py index ~/Documents --media auto
  python run.py search "sunset over the ocean" -k 10
  python run.py search --image ./query.jpg
  python run.py web
  python run.py status
  python run.py doctor
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="Local semantic search over a folder (images, PDFs, docs, code) using EmbeddingGemma 2.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--api-url", help="embedding API base (default: auto-discover on localhost)")
    p.add_argument("--api-type", choices=["auto", "openai", "ollama"], default=None)
    p.add_argument("--model", help="model id/name the API expects")
    p.add_argument("--dim", type=int, help="vector dimension to request (768/512/256/128)")
    p.add_argument("--data-dir", help="where to store the index (default: ./data)")
    p.add_argument("--threads", type=int)
    p.add_argument("--batch", type=int)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="index a folder (incremental)")
    pi.add_argument("folders", nargs="+", help="folders to index")
    pi.add_argument("--media", choices=["auto", "on", "off"], default="auto",
                    help="embed audio/video: auto (only if API supports), on, off")
    pi.add_argument("--kinds", help="only index these file types (comma list, e.g. image,pdf,code; 'all' clears a saved filter)")
    pi.add_argument("--rescan", action="store_true",
                    help="also rescan all existing index folders (default: only the given folders)")
    pi.add_argument("--watch", type=int, nargs="?", const=30, default=0,
                    metavar="SECONDS", help="keep watching for changes (default 30s)")
    pi.add_argument("--hidden", action="store_true", help="include dotfiles and dotfolders")
    pi.add_argument("--quiet", action="store_true", help="no progress output")

    ps = sub.add_parser("search", help="search the index")
    ps.add_argument("query", nargs="*", help="text query")
    ps.add_argument("--image", help="search using an image file")
    ps.add_argument("-k", type=int, default=10)
    ps.add_argument("--kind", help="comma separated kinds, e.g. image,pdf,code")
    ps.add_argument("--not", dest="exclude", action="append", default=[],
                    metavar="TEXT", help="exclude things like this (repeatable)")
    ps.add_argument("--not-weight", type=float, default=1.0,
                    help="how strongly --not pushes results down (default 1.0)")
    ps.add_argument("--want", dest="wants", action="append", default=[],
                    metavar="TEXT", help="another thing to match (repeatable)")
    ps.add_argument("--combine", choices=["avg", "max"], default="avg",
                    help="avg: match all wants at once, max: match any of them")
    ps.add_argument("--json", action="store_true")
    ps.add_argument("--open", action="store_true", help="open the top result with the system handler")

    pw = sub.add_parser("web", help="start the local web UI")
    pw.add_argument("--port", type=int)
    pw.add_argument("--host")

    sub.add_parser("status", help="show index + API info")
    sub.add_parser("doctor", help="check configuration and API")
    sub.add_parser("migrate", help="split a legacy combined index into independent folder indexes without re-embedding")
    sub.add_parser("reset", help="delete the index")
    pf = sub.add_parser("forget", help="remove folders from the index")
    pf.add_argument("folders", nargs="+", help="folders to forget")
    return p


def settings_from_args(args, parser) -> Settings:
    s = Settings.from_env()
    s.override(
        api_url=args.api_url,
        api_type=args.api_type,
        model=args.model,
        dim=args.dim,
        data_dir=args.data_dir,
        threads=args.threads,
        batch=args.batch,
    )
    if args.cmd == "index" and args.hidden:
        s.hidden = True
    if args.cmd == "web":
        s.override(web_host=args.host, web_port=args.port)
    return s


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def cmd_index(args, settings):
    client = EmbedClient(settings)
    print(f"API: {client.api_type} @ {client.base} (model: {settings.model})")
    index = Index.load(settings.data_dir)
    if index.records:
        print(f"existing index: {len(index.records)} vectors ({index.dim or '?'}d)")
    folders = list(args.folders)
    kinds = args.kinds or getattr(settings, "index_kinds", "") or None
    rescan = args.rescan or settings.rescan
    log = None if args.quiet else print
    while True:
        res = Indexer(settings, client, log=log).run(folders, media=args.media, kinds=kinds,
                                                     rescan=rescan)
        skipped = list(res.get("skipped") or [])
        errors = list(res.get("errors") or [])
        total_vectors = len(Index.load(settings.data_dir).records)
        print(
            f"scanned {res.get('files_scanned', 0)} files · "
            f"indexed {res.get('indexed_files', 0)} new · "
            f"{res.get('vectors_added', 0)} vectors added · "
            f"{res.get('unchanged', 0)} unchanged · "
            f"{res.get('dropped', 0)} removed · "
            f"{total_vectors} total in {res.get('seconds', 0)}s"
        )
        if skipped:
            reasons = {}
            for _, why in skipped:
                key = str(why)[:80]
                reasons[key] = reasons.get(key, 0) + 1
            print("skipped: " + ", ".join(f"{n} x {why}" for why, n in sorted(reasons.items(), key=lambda x: -x[1])[:6]))
        for err in errors[:3]:
            print("api error: " + str(err)[:300])
        if not args.watch:
            break
        time.sleep(args.watch)
    return 0


def cmd_search(args, settings):
    client = EmbedClient(settings)
    query = " ".join(args.query).strip()
    if not query and not args.image and not args.wants:
        print("error: provide a text query, --want, or --image", file=sys.stderr)
        return 2
    searcher = Searcher(settings, client)
    results = searcher.search(text=query or None, image_path=args.image, k=args.k,
                              kind=args.kind, exclude=args.exclude, neg_weight=args.not_weight,
                              wants=args.wants, combine=args.combine)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if not results:
        print("no results (is the index empty?)")
        return 1
    if args.open:
        import webbrowser

        webbrowser.open("file://" + results[0]["path"])
    width = max((len(r.get("rel") or r.get("path", "")) for r in results), default=10)
    header = f"{'score':>7}  {'kind':<6}  {'file':<{width}}"
    if args.exclude:
        header += "   (score = match - penalty)"
    print(header)
    for r in results:
        line = f"{r['score'] * 100:6.1f}%  {r.get('kind', ''):<6}  {r.get('rel') or r.get('path', '')}"
        if args.exclude and "penalty" in r:
            line += f"   -{r['penalty'] * 100:.1f}"
        print(line)
    if not args.json:
        print()
        top = results[0]
        snippet = (top.get("snippet") or "").strip()
        if snippet:
            print(f"# {top.get('rel') or top.get('path')}\n{snippet[:600]}")
    return 0


def cmd_web(args, settings):
    client = EmbedClient(settings)
    serve(settings, client)
    return 0


def cmd_status(args, settings):
    from . import __version__

    print(f"eg       : v{__version__}")
    try:
        index = Index.load(settings.data_dir)
    except CorruptIndexError as e:
        print(f"data dir : {Path(settings.data_dir).resolve()}")
        print(f"index    : UNREADABLE -> {e}")
        print("           back up data/, then run: python run.py reset  (or restore index.json.bak)")
        return 1
    st = index.stats()
    print(f"data dir : {Path(settings.data_dir).resolve()}")
    print(f"vectors  : {st['vectors']} across {st['files']} files ({_fmt_size(st['size_bytes'])})")
    print(f"dimension: {st['dim']}   model: {st['model']}   api: {st['api_type']}")
    print(f"roots    : {', '.join(st['roots']) or '(none)'}")
    if st.get("kind_filter"):
        print("types    : only " + ", ".join(st["kind_filter"]) + " (other types removed from index on next run)")
    if st["updated"]:
        print(f"updated  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st['updated']))}")
    if st["kinds"]:
        print("kinds    : " + ", ".join(f"{k}={v}" for k, v in sorted(st["kinds"].items())))
    if getattr(index, "_problem", None):
        print(f"warning  : {index._problem}")
    check = index.verify()
    if not check["ok"]:
        print(f"warning  : on-disk check failed: {check['error']}")
    try:
        info = EmbedClient(settings).describe()
        print(f"api url  : {info.get('api_url')} ({info.get('api_type')})")
        if info.get("models"):
            print(f"models   : {', '.join(str(m) for m in info['models'][:10])}")
        if info.get("error"):
            print(f"api error: {info['error'][:160]}")
    except Exception as e:
        print(f"api error: {e}")
    return 0


def cmd_doctor(args, settings):
    ok = True
    from . import __version__

    print(f"eg       : v{__version__}")
    print(f"python   : {sys.version.split()[0]}")
    try:
        import numpy  # noqa: F401

        print("numpy    : available (fast search)")
    except Exception:
        print("numpy    : not installed (pure-python search, still fast for small indexes)")
    print(f"data dir : {Path(settings.data_dir).resolve()}")
    try:
        index = Index.load(settings.data_dir)
    except CorruptIndexError as e:
        print(f"index    : UNREADABLE -> {e}")
        ok = False
        index = None
    if index is not None:
        print(f"index    : {len(index.records)} vectors, dim {index.dim}")
        if Path(settings.data_dir, "index.json").exists() or Path(settings.data_dir,"catalog.json").exists():
            check = index.verify()
            if check["ok"]:
                print(f"verify   : on-disk files match ({check['records']} records, {check['vectors']} vectors)")
            else:
                print(f"verify   : FAILED -> {check['error']}")
                ok = False
        if getattr(index, "_problem", None):
            print(f"warning  : {index._problem}")
            ok = False
    try:
        client = EmbedClient(settings)
        print(f"api      : {client.api_type} @ {client.base}")
        caps = Indexer(settings, client).capabilities()
        print(f"caps     : {caps if caps else 'unknown (assumed text + image)'}")
        vec = client.embed_query("connection test")
        print(f"embed    : ok, {len(vec)} dims")
        if index is not None and index.dim and len(vec) != index.dim:
            print(f"warning  : query dim {len(vec)} != index dim {index.dim} (re-index needed)")
            ok = False
        probe = {"model": settings.model, "input": [content_item(PNG_1PX, "image")]}
        try:
            data = client._request("POST", client.base + "/embeddings", probe)
            dim = len(data["data"][0]["embedding"])
            print(f"images   : supported by API ({dim} dims)")
        except Exception as e:
            print(f"images   : not supported by this API ({str(e)[:80]}) — text files still work")
    except Exception as e:
        print(f"api      : FAILED -> {e}")
        ok = False
    print("result   : " + ("ok" if ok else "check the warnings above"))
    return 0 if ok else 1


def cmd_reset(args, settings):
    try:
        detail = f"{len(Index.load(settings.data_dir).records)} vectors"
    except CorruptIndexError:
        detail = "unreadable index"
    Index(settings.data_dir).reset()
    print(f"reset active index ({detail}) in {Path(settings.data_dir).resolve()}; recovery files preserved")
    return 0


def cmd_migrate(args,settings):
    from .folder_store import migrate
    index=migrate(settings.data_dir)
    storage=index.storage_info()
    print(f'{len(index.records)} vectors in {storage["folders"]} folder indexes ({storage["format"]})')
    print(f'Catalog: {storage["catalog"]}')
    print('Legacy combined files are preserved as a migration backup. No embedding API was used.')
    return 0


def cmd_forget(args, settings):
    index = Index.load(settings.data_dir)
    targets = [str(Path(f).expanduser().resolve()) for f in args.folders]

    def under(path: str) -> bool:
        try:
            return any(Path(path).is_relative_to(t) for t in targets)
        except Exception:
            return False

    keep_r, keep_v = [], []
    for rec, vec in zip(index.records, index.vectors):
        if under(rec.get("path") or ""):
            continue
        keep_r.append(rec)
        keep_v.append(vec)
    removed = len(index.records) - len(keep_r)
    index.records, index.vectors = keep_r, keep_v
    index._matrix = None
    index.set_state(index.model, index.api_type, [r for r in (index.roots or []) if r not in targets])
    index.save()
    print(
        f"forgot {', '.join(targets)}: removed {removed} "
        f"vector{'s' if removed != 1 else ''}, {len(keep_r)} remain"
    )
    return 0


PNG_1PX = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAABzenr0AAAADUlEQVR4nGNg"
    "YAAAAAMAAWU3BpwAAAAASUVORK5CYII="
)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = settings_from_args(args, parser)
    handlers = {
        "index": cmd_index,
        "search": cmd_search,
        "web": cmd_web,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "migrate": cmd_migrate,
        "reset": cmd_reset,
        "forget": cmd_forget,
    }
    try:
        return handlers[args.cmd](args, settings)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        if os.environ.get("EG_DEBUG"):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
