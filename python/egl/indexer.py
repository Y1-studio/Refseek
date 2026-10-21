from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .api import ApiError, EmbedClient, l2norm, media_item
from .config import Settings
from .scan import INDEX_KINDS, scan_folder
from .store import Index
from .textio import chunk_text, is_media, mime_of, read_file, shrink_image


class Indexer:
    def __init__(self, settings: Settings, client: EmbedClient, log=print):
        self.s = settings
        self.client = client
        self.log = log

    def index_roots(self) -> list[str]:
        return Index.load(self.s.data_dir).roots or []

    def _scan_roots(self, roots, required=None) -> tuple[dict, list]:
        states: dict[str, dict] = {}
        skipped: list[tuple[Path, str]] = []
        required_set = {str(r) for r in (required or [])}
        for root in roots:
            if not root.exists():
                if str(root) in required_set:
                    raise FileNotFoundError(f"folder not found: {root}")
                continue
            root_states, root_skipped = scan_folder(root, self.s)
            states.update(root_states)
            skipped.extend(root_skipped)
        return states, skipped

    def capabilities(self) -> dict:
        try:
            data = self.client.get("/capabilities")
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def run(self, folders, media="auto", kinds=None, rescan=False, cancel=None) -> dict:
        started = time.time()
        if isinstance(folders, (str, Path)):
            folders = [folders]
        if isinstance(kinds, str):
            kinds = [k for k in kinds.replace(";", ",").split(",") if k.strip()]
        scan_roots: list[Path] = []
        seen_roots = set()
        extra = list(self.index_roots()) if rescan else []
        for folder in list(folders) + extra:
            root = Path(folder).expanduser().resolve()
            if str(root) in seen_roots:
                continue
            seen_roots.add(str(root))
            scan_roots.append(root)
        index = Index.load(self.s.data_dir)
        original_records,original_vectors=index.records,index.vectors
        initial_total = len(index.records)
        if self.log:
            self.log(f"scanning {len(scan_roots)} folder(s)…")
        states, skipped = self._scan_roots(scan_roots, required=[Path(f).expanduser().resolve() for f in folders])
        available_roots=[root for root in scan_roots if root.exists()]
        allowed = None
        if kinds is not None:
            allowed = {str(k).strip().lower() for k in kinds if str(k).strip()}
            if "all" in allowed:
                allowed = set(INDEX_KINDS)
            unknown = sorted(allowed - set(INDEX_KINDS))
            if unknown:
                raise ApiError(f"unknown file type(s): {', '.join(unknown)} (known: {', '.join(INDEX_KINDS)})")
            if allowed == set(INDEX_KINDS):
                allowed = None
        elif index.kind_filter:
            allowed = {str(k).lower() for k in index.kind_filter}
        kinds_excluded = 0
        if allowed is not None:
            kept = {p: st for p, st in states.items() if st["kind"] in allowed}
            kinds_excluded = len(states) - len(kept)
            states = kept
        caps = self.capabilities()

        def media_allowed(kind: str) -> bool:
            if kind == "image":
                return bool(caps.get("image", True))
            if media == "off":
                return False
            if media == "on":
                return True
            return bool(caps.get(kind, False))

        old_states = index.file_states()
        unchanged = {
            p: st
            for p, st in states.items()
            if p in old_states
            and old_states[p].get("mtime_ns") == st["mtime_ns"]
            and old_states[p].get("size") == st["size"]
            and old_states[p].get("kind") == st["kind"]
        }

        by_path = index.records_by_path()
        keep_records, keep_vectors = [], []
        obsolete = 0
        for path, idxs in by_path.items():
            if path in unchanged:
                for i in idxs:
                    keep_records.append(index.records[i])
                    keep_vectors.append(index.vectors[i])
            elif path in states:
                pass  # changed file -> re-embedded below via planned
            elif _under_any(path, available_roots):
                obsolete += len(idxs)
            else:
                for i in idxs:  # outside the scanned scope (rescan=False) -> keep
                    keep_records.append(index.records[i])
                    keep_vectors.append(index.vectors[i])

        planned = [p for p in states if p not in unchanged]

        def _build(idx):
            path = planned[idx]
            p = Path(path)
            kind = states[path]["kind"]
            if is_media(kind) and not media_allowed(kind):
                return (idx, None, (p, f"{kind} not supported by this API"))
            try:
                file_root = _root_of(p, scan_roots)
                if is_media(kind):
                    job = self._media_job(p, kind, states[path], file_root)
                else:
                    job = self._text_job(p, kind, states[path], file_root)
                return (idx, job, None)
            except Exception as e:
                return (idx, None, (p, f"{type(e).__name__}: {e}"))

        built = [None] * len(planned)
        if planned:
            workers = max(1, self.s.threads)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                n_done = 0
                for idx, jb, sk in ex.map(_build, range(len(planned))):
                    built[idx] = jb
                    if sk is not None:
                        skipped.append(sk)
                    n_done += 1
                    if self.log is not None and len(planned) > 500 and n_done % 500 == 0:
                        self.log(f"  prepared {n_done}/{len(planned)}")
        jobs = [b for b in built if b]

        flat: list = []
        item_job: list[int] = []
        for ji, job in enumerate(jobs):
            job["offset"] = len(flat)
            flat.extend(job["items"])
            item_job.extend([ji] * len(job["items"]))
        total_items = len(flat)
        results: list = [None] * total_items
        bs = max(1, self.s.batch)
        batches = [flat[i : i + bs] for i in range(0, total_items, bs)]
        errors: list[str] = []
        if self.log and (total_items or jobs):
            self.log(f"preparing {total_items} items from {len(planned)} pending files…")

        def run_batch(batch):
            return self.client.embed(batch, len(batch))

        done = [False] * total_items
        remaining = [len(j["items"]) for j in jobs]
        progress = _Progress(self.log, total_items, len(jobs))
        new_records, new_vectors = [], []
        drained = [False] * len(jobs)
        dim = None
        unsupported: list[Path] = []

        def assemble(ji):
            """(records, vectors) for a fully-embedded job, else None. Raises on dim change."""
            nonlocal dim
            job = jobs[ji]
            if not job["items"]:
                return None
            vecs = results[job["offset"] : job["offset"] + len(job["items"])]
            if any(v is None for v in vecs):
                return None
            if dim is None:
                dim = len(vecs[0])
                if index.dim and index.dim != dim:
                    raise ApiError(
                        f"API returned {dim}-dim vectors but the index has {index.dim}-dim. "
                        "Run 'python run.py reset' and re-index with matching dimensions."
                    )
            if is_media(job["kind"]):
                records, vectors = [job["record"]()], [l2norm(vecs[0])]
            else:
                records, vectors = job["records"], [l2norm(v) for v in vecs]
                if len(records) > len(vectors):
                    records = records[: len(vectors)]
            return records, vectors

        def drain():
            """Move fully-embedded jobs into new_records. Returns completed file count."""
            n = 0
            for ji, job in enumerate(jobs):
                if drained[ji]:
                    continue
                if job["items"] and remaining[ji] > 0:
                    continue
                drained[ji] = True
                out = assemble(ji)
                if out is None:
                    unsupported.append(job["path"])
                    continue
                records, vectors = out
                for rec, vec in zip(records, vectors):
                    new_records.append(rec)
                    new_vectors.append(vec)
                n += 1
            return n

        ck_files = max(0, self.s.checkpoint_files)
        ck_secs = max(0, self.s.checkpoint_secs)
        last_ck = time.time()
        since_ck = 0
        last_drained_files = -1

        def store():
            replaced={r['path'] for r in new_records}
            # Keep the previous versions of changed files until their entire
            # replacements are embedded. Failed/cancelled files remain searchable.
            pending=[i for path,ids in by_path.items() if path in states and path not in unchanged and path not in replaced for i in ids]
            fallback_records=[original_records[i] for i in pending]
            fallback_vectors=[original_vectors[i] for i in pending]
            index.records = keep_records + fallback_records + new_records
            index.vectors = keep_vectors + fallback_vectors + new_vectors
            if dim:
                index.dim = dim
            index.set_state(self.s.model, self.client.api_type,
                            {str(r) for r in scan_roots} | set(index.roots or []),
                            kind_filter=sorted(allowed) if allowed is not None else None)
            index.save()

        def maybe_checkpoint(force=False):
            nonlocal last_ck, since_ck, last_drained_files
            if force or progress.files_done != last_drained_files:
                last_drained_files = progress.files_done
                since_ck += drain()
            due = force or (ck_files and since_ck >= ck_files) \
                or (ck_secs and time.time() - last_ck >= ck_secs)
            if force or (due and since_ck):
                has_fallback=any(path in states and path not in unchanged for path in by_path)
                if not keep_records and not new_records and not has_fallback and initial_total:
                    raise ApiError(
                        "nothing embeddable in this run — refusing to wipe the existing "
                        f"index ({initial_total} vectors). Use forget to delete folders, "
                        "or check the folder paths / file-type filter.")
                store()
                if self.log and since_ck:
                    self.log(f"  checkpoint: {len(new_records)} vectors on disk")
                since_ck = 0
                last_ck = time.time()

        def is_cancelled():
            try:
                return bool(cancel() if callable(cancel) else cancel)
            except Exception:
                return False

        cancelled = False
        ex = None
        try:
            if batches:
                ex = ThreadPoolExecutor(max_workers=max(1, self.s.threads))
                future_to_batch = {ex.submit(run_batch, b): (fi, b) for fi, b in enumerate(batches)}
                for fut in as_completed(future_to_batch):
                    fi, batch = future_to_batch[fut]
                    base = fi * bs
                    try:
                        vecs = fut.result()
                        for j, v in enumerate(vecs):
                            if base + j < total_items:
                                results[base + j] = v
                                progress.mark_done(base + j, done, item_job, remaining)
                    except Exception as e:
                        errors.append(str(e))
                        for j, item in enumerate(batch):
                            if base + j >= total_items:
                                break
                            try:
                                results[base + j] = self.client.embed_one(item)
                                progress.mark_done(base + j, done, item_job, remaining)
                            except Exception:
                                pass
                    progress.show()
                    maybe_checkpoint()
                    if is_cancelled():
                        cancelled = True
                        break
            progress.finish()
        finally:
            if ex is not None:
                ex.shutdown(cancel_futures=True)
            maybe_checkpoint(force=True)

        for p in unsupported:
            skipped.append((p, "API could not embed this input"))
        return {
            "files_scanned": len(states),
            "unchanged": len(unchanged),
            "planned": len(planned),
            "indexed_files": len({record['path'] for record in new_records}),
            "vectors_added": len(new_records),
            "vectors_total": len(index.records),
            "dropped": obsolete,
            "kinds_excluded": kinds_excluded,            "kinds_filter": sorted(allowed) if allowed is not None else None,
            "rescan": bool(rescan),
            "cancelled": cancelled,
            "skipped": skipped,
            "errors": errors[:5],
            "seconds": round(time.time() - started, 1),
        }

    def _media_job(self, path: Path, kind: str, st: dict, root: Path) -> dict:
        raw = path.read_bytes()
        if kind == "image":
            raw = shrink_image(raw, self.s.image_max)
        item = media_item(raw, mime_of(path, kind), kind)

        def record(path=path, st=st, root=root, kind=kind):
            return {
                "path": str(path),
                "rel": _rel(path, root),
                "kind": kind,
                "title": path.stem,
                "snippet": "",
                "chunk": 0,
                "chunks": 1,
                "size": st["size"],
                "mtime_ns": st["mtime_ns"],
                "ext": path.suffix.lower(),
            }

        return {"path": path, "kind": kind, "items": [item], "record": record}

    def _text_job(self, path: Path, kind: str, st: dict, root: Path) -> dict:
        title, text = read_file(path, kind)
        chunks = chunk_text(text, self.s.chunk_chars, self.s.chunk_overlap)
        if not chunks:
            raise ValueError("no extractable text")
        chunks = chunks[: self.s.max_chunks]
        doc_title = (title or "").replace("\n", " ").strip()[:200] or (
            path.name if kind == "code" else path.stem
        )
        items = [self.s.with_prefix_doc(doc_title, c) for c in chunks]
        records = [
            {
                "path": str(path),
                "rel": _rel(path, root),
                "kind": kind,
                "title": title,
                "snippet": _clean(c)[:400],
                "chunk": i,
                "chunks": len(chunks),
                "size": st["size"],
                "mtime_ns": st["mtime_ns"],
                "ext": path.suffix.lower(),
            }
            for i, c in enumerate(chunks)
        ]
        return {"path": path, "kind": kind, "items": items, "records": records}


class _Progress:
    def __init__(self, log, total_items: int, total_files: int):
        import sys

        self.log = log
        self.total_items = total_items
        self.total_files = total_files
        self.completed = 0
        self.files_done = 0
        self.started = time.time()
        self.last_shown = 0.0
        self.tty = bool(log) and sys.stdout.isatty()

    def mark_done(self, idx: int, done: list, item_job: list, remaining: list):
        if done[idx]:
            return
        done[idx] = True
        self.completed += 1
        ji = item_job[idx]
        remaining[ji] -= 1
        if remaining[ji] == 0:
            self.files_done += 1

    def show(self, force=False):
        if not self.log or not self.total_items:
            return
        now = time.time()
        gap = 0.2 if self.tty else 2.0
        if not force and now - self.last_shown < gap:
            return
        self.last_shown = now
        msg = self.message(now)
        if self.tty:
            import sys

            sys.stdout.write("\r" + msg + " " * 6)
            sys.stdout.flush()
        else:
            self.log(msg)

    def message(self, now=None) -> str:
        now = now if now is not None else time.time()
        elapsed = max(now - self.started, 1e-6)
        rate = self.completed / elapsed
        pct = 100.0 * self.completed / self.total_items if self.total_items else 100.0
        eta = (self.total_items - self.completed) / rate if rate > 0 else None
        return (
            f"  embedding {self.completed}/{self.total_items} items ({pct:.0f}%), "
            f"{self.files_done}/{self.total_files} files "
            f"· {rate:.1f} items/s · ETA {_fmt_eta(eta)}"
        )

    def finish(self):
        if not self.log or not self.total_items:
            return
        self.show(force=True)
        if self.tty:
            import sys

            sys.stdout.write("\n")
            sys.stdout.flush()


def _fmt_eta(seconds) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    if seconds < 0:
        seconds = 0
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _root_of(path: Path, roots: list[Path]) -> Path:
    best = None
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if best is None or len(str(root)) > len(str(best)):
            best = root
    return best or path.parent


def _clean(text: str) -> str:
    return " ".join(text.split())


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _under_any(path: str, roots: list[Path]) -> bool:
    p = Path(path)
    for r in roots:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False
