from __future__ import annotations

import hashlib
import math
import threading
import time
from array import array
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import nullcontext
from pathlib import Path

from .api import ApiError, EmbedClient, IndexCancelled, l2norm, media_item
from .config import Settings
from .scan import INDEX_KINDS, scan_folder
from .store import Index
from .textio import chunk_text, is_media, mime_of, prepare_image, read_file


class Indexer:
    def __init__(self, settings: Settings, client: EmbedClient, log=print):
        self.s, self.client, self.log = settings, client, log

    def index_roots(self):
        return Index.load(self.s.data_dir).roots or []

    def _scan_roots(self, roots, required=None, cancel=None):
        states, skipped = {}, []
        required = {str(r) for r in (required or [])}
        for root in roots:
            if cancel and cancel():
                raise IndexCancelled('Indexing cancelled during scanning')
            if not root.exists():
                if str(root) in required:
                    raise FileNotFoundError(f'folder not found: {root}')
                continue
            found, omitted = scan_folder(root, self.s, cancel)
            states.update(found)
            skipped.extend(omitted)
        return states, skipped

    def capabilities(self):
        try:
            result = self.client.get('/capabilities')
            if isinstance(result, dict):
                return result
        except IndexCancelled:
            raise
        except Exception:
            pass
        return {}

    def run(self, folders, media='auto', kinds=None, rescan=False, cancel=None, verify_contents=False):
        started = time.monotonic()
        explicit_kinds = kinds is not None
        stop = threading.Event()

        def stopped():
            return stop.is_set() or bool(cancel() if callable(cancel) else cancel)

        def check():
            if stopped():
                raise IndexCancelled('Indexing cancelled; previous file vectors retained')

        def scope():
            return self.client.cancel_scope(stopped) if hasattr(self.client, 'cancel_scope') else nullcontext()

        index = Index.load(self.s.data_dir)
        if not index.records:
            from .dimensions import preferred_dimension, DEFAULT_DIMENSION
            requested = preferred_dimension(self.s.data_dir)
            index.search_dim = requested if requested != DEFAULT_DIMENSION else None
        original_records, original_vectors = index.records, index.vectors
        by_path = index.records_by_path()
        initial_total = len(original_records)
        folders = [folders] if isinstance(folders, (str, Path)) else list(folders)
        required = [Path(f).expanduser().resolve() for f in folders]
        roots = list(dict.fromkeys(required + ([Path(r) for r in index.roots] if rescan else [])))
        target_roots = set(map(str, roots)) | set(index.roots or [])
        available = [root for root in roots if root.exists()]
        from .index_preferences import load, cpu_limit
        saved = load(self.s.data_dir, fallback_kinds=index.kind_filter or ['image'], fallback_workers=self.s.threads)
        if kinds is None:
            kinds = saved['kinds'] if (Path(self.s.data_dir) / 'index-preferences.json').exists() else (index.kind_filter or self.s.index_kinds or 'image')
        if isinstance(kinds, str):
            kinds = [k.strip().lower() for k in kinds.replace(';', ',').split(',') if k.strip()]
        allowed = set(kinds)
        if 'all' in allowed:
            allowed = set(INDEX_KINDS)
        if not allowed or allowed - set(INDEX_KINDS):
            raise ApiError('Select at least one known file type to index')
        desired_filter = None if allowed == set(INDEX_KINDS) else sorted(allowed)
        changed_metadata = (set(index.roots) != target_roots or index.kind_filter != desired_filter
                            or index.model != self.s.model or index.api_type != self.client.api_type)
        skipped, errors, warnings = [], [], []
        replacements = {}
        unchanged = {}
        states = {}
        obsolete = set()
        checkpoints = 0
        last_saved = 0
        last_checkpoint = time.monotonic()
        processed = 0
        cancelled = False
        interrupted = False
        workers = max(1, min(self.s.threads, cpu_limit()))
        window = workers + 1
        if explicit_kinds:
            from .index_preferences import save
            save(self.s.data_dir, kinds=sorted(allowed), prepare_workers=workers)
        max_prepared = 0
        max_inflight = 0

        def result():
            added = sum(len(rows) for rows, _ in replacements.values())
            return dict(files_scanned=len(states), unchanged=len(unchanged), planned=len(states)-len(unchanged),
                        indexed_files=len(replacements), vectors_added=added, vectors_total=len(index.records),
                        dropped=sum(len(by_path[p]) for p in obsolete) if not cancelled else 0,
                        kinds_excluded=kinds_excluded, kinds_filter=desired_filter, rescan=bool(rescan),
                        cancelled=cancelled, skipped=skipped, errors=errors[:5], warnings=warnings,
                        seconds=round(time.monotonic()-started, 3), checkpoints=checkpoints,
                        preparation_workers=workers, embedding_requests=2, preparation_window=window,
                        max_preparation_pending=max_prepared, max_embedding_pending=max_inflight,
                        processed_files=len(unchanged)+processed, verify_contents=bool(verify_contents))

        def publish(complete=False):
            nonlocal checkpoints, last_saved, last_checkpoint
            # Deletions/filter changes are applied only after a completed scan/job.
            # Partial checkpoints retain every previous file not fully replaced.
            records, vectors = [], []
            for path, ids in by_path.items():
                if path in replacements or (complete and path in obsolete):
                    continue
                records.extend(original_records[i] for i in ids)
                vectors.extend(original_vectors[i] for i in ids)
            for rows, values in replacements.values():
                records.extend(rows)
                vectors.extend(values)
            if not records and initial_total:
                raise ApiError('Nothing embeddable in this run; refusing to wipe the existing index. Use Forget to remove folders.')
            if len(replacements) == last_saved and not (complete and (changed_metadata or obsolete)):
                return
            index.records, index.vectors = records, vectors
            index.set_state(self.s.model, self.client.api_type, target_roots,
                            kind_filter=desired_filter if complete else index.kind_filter)
            index.save()
            for warning in index.last_save.get('warnings', []):
                if warning not in warnings:
                    warnings.append(warning)
            checkpoints += 1
            last_saved = len(replacements)
            last_checkpoint = time.monotonic()
            if self.log:
                self.log(f'  checkpoint: {sum(len(x[0]) for x in replacements.values())} vectors on disk')

        def await_result(future):
            while True:
                check()
                try:
                    return future.result(timeout=.05)
                except FutureTimeout:
                    continue

        kinds_excluded = 0
        try:
            check()
            if self.log:
                self.log(f'scanning {len(roots)} folder(s)…')
            all_states, skipped = self._scan_roots(roots, required, stopped)
            states = {p: st for p, st in all_states.items() if st['kind'] in allowed}
            kinds_excluded = len(all_states)-len(states)
            skipped_paths = {str(p) for p, _ in skipped}
            old = index.file_states()
            for path, st in states.items():
                check()
                prior = old.get(path)
                if (prior and all(prior.get(k) == st[k] for k in ('mtime_ns', 'size', 'kind'))
                    and not (not is_media(st['kind']) and prior['n'] >= self.s.max_chunks)):
                    if not verify_contents or (prior.get('sha256') and _file_hash(Path(path), check) == prior['sha256']):
                        unchanged[path] = st
            obsolete = {p for p in by_path if p not in states and p not in skipped_paths and _under_any(p, available)}
            planned = [p for p in states if p not in unchanged]
            if not planned:
                check()
                publish(complete=True)
                return result()
            with scope():
                caps = self.capabilities()
        except IndexCancelled:
            cancelled = True
            return result()

        def build(i):
            check()
            path = Path(planned[i]);st = states[str(path)];kind = st['kind']
            if kind in ('audio', 'video') and (media == 'off' or (media == 'auto' and not caps.get(kind, False))):
                raise ValueError(f'{kind} not supported by this API')
            if kind == 'image' and not caps.get('image', True):
                raise ValueError('image not supported by this API')
            _same_file(path, st)
            root = _root_of(path, roots)
            job = self._media_job(path, kind, st, root) if is_media(kind) else self._text_job(path, kind, st, root)
            _same_file(path, st)
            check()
            return job

        def embed(job):
            with scope():
                values = []
                # Large image arrays did not improve throughput. Keep each image
                # independent; text still uses the configured chunk batch size.
                size = 1 if is_media(job['kind']) else max(1, self.s.batch)
                for offset in range(0, len(job['items']), size):
                    check()
                    part = job['items'][offset:offset+size]
                    try:
                        chunk = self.client.embed(part, len(part))
                        if len(chunk) != len(part):
                            raise ApiError('Incomplete embedding response')
                    except IndexCancelled:
                        raise
                    except Exception:
                        chunk = []
                        for item in part:
                            check()
                            chunk.append(self.client.embed_one(item))
                    values.extend(chunk)
                check()
                return values

        prep = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='image-preparation')
        net = ThreadPoolExecutor(max_workers=2, thread_name_prefix='embedding-request')
        prepared, pending = deque(), deque()
        next_build = 0
        progress = _Progress(self.log, len(planned))

        def fill():
            nonlocal next_build, max_prepared, max_inflight, processed
            while len(pending) < 2:
                while next_build < len(planned) and len(prepared) < window:
                    prepared.append((next_build, prep.submit(build, next_build)))
                    next_build += 1
                    max_prepared = max(max_prepared, len(prepared))
                if not prepared:
                    break
                i, future = prepared.popleft()
                try:
                    job = await_result(future)
                except IndexCancelled:
                    raise
                except Exception as exc:
                    skipped.append((Path(planned[i]), f'{type(exc).__name__}: {exc}'))
                    processed += 1
                    progress.advance()
                    continue
                pending.append((i, job, net.submit(embed, job)))
                max_inflight = max(max_inflight, len(pending))

        try:
            if self.log:
                self.log(f'preparing up to {window} files ahead of 2 embedding requests…')
            fill()
            while pending:
                i, job, future = pending.popleft()
                try:
                    values = await_result(future)
                    check()
                    _same_file(job['path'], states[str(job['path'])])
                    if _file_hash(job['path'], check) != job['sha256']:
                        raise ValueError('File changed during indexing; previous vectors retained')
                    if len(values) != len(job['items']):
                        raise ApiError('Incomplete embedding response; previous vectors retained')
                    dimension = index.dim or self.s.dim or len(values[0])
                    if any(not _valid_vector(v, dimension) for v in values):
                        raise ApiError('Invalid/mismatched embedding dimensions; previous vectors retained')
                    rows = [job['record']()] if is_media(job['kind']) else job['records']
                    if len(rows) != len(values):
                        raise ApiError('Incomplete file embedding; previous vectors retained')
                    # Compact float64 buffers preserve the exact normalization
                    # math while avoiding a Python float object for every value.
                    replacements[str(job['path'])] = rows, [array('d', l2norm(v)) for v in values]
                    index.dim = dimension
                except IndexCancelled:
                    raise
                except Exception as exc:
                    skipped.append((Path(planned[i]), f'{type(exc).__name__}: {exc}'))
                    if len(errors) < 5:
                        errors.append(str(exc))
                processed += 1
                progress.advance()
                if ((self.s.checkpoint_files and len(replacements)-last_saved >= self.s.checkpoint_files)
                    or (self.s.checkpoint_secs and time.monotonic()-last_checkpoint >= self.s.checkpoint_secs)):
                    publish()
                fill()
            check()
        except IndexCancelled:
            cancelled = True
        except KeyboardInterrupt:
            cancelled = interrupted = True
        finally:
            stop.set()
            prep.shutdown(wait=True, cancel_futures=True)
            net.shutdown(wait=True, cancel_futures=True)
        # Check the external cancellation again after the final request, before
        # permitting deletions. stop is now only the internal pool shutdown flag.
        if bool(cancel() if callable(cancel) else cancel):
            cancelled = True
        publish(complete=not cancelled)
        progress.finish()
        if interrupted:
            raise KeyboardInterrupt
        return result()

    def _media_job(self, path: Path, kind: str, st: dict, root: Path):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != st['size']:
            raise ValueError('File changed during preparation')
        mime = mime_of(path, kind)
        if kind == 'image':
            raw, mime = prepare_image(raw, mime, self.s.image_max)
        item = media_item(raw, mime, kind)

        def record():
            return dict(path=str(path), rel=_rel(path, root), kind=kind, title=path.stem, snippet='',
                        chunk=0, chunks=1, size=st['size'], mtime_ns=st['mtime_ns'], ext=path.suffix.lower(), sha256=digest)
        return dict(path=path, kind=kind, items=[item], record=record, sha256=digest)

    def _text_job(self, path: Path, kind: str, st: dict, root: Path):
        digest = _file_hash(path)
        title, text = read_file(path, kind)
        chunks = chunk_text(text, self.s.chunk_chars, self.s.chunk_overlap)
        if not chunks:
            raise ValueError('no extractable text')
        if len(chunks) > self.s.max_chunks:
            raise ValueError(f'Document exceeds {self.s.max_chunks} text chunks; split it into smaller files. Previous vectors retained.')
        doc_title = (title or '').replace('\n', ' ').strip()[:200] or (path.name if kind == 'code' else path.stem)
        records = [dict(path=str(path), rel=_rel(path, root), kind=kind, title=title, snippet=_clean(c)[:400],
                        chunk=i, chunks=len(chunks), size=st['size'], mtime_ns=st['mtime_ns'],
                        ext=path.suffix.lower(), sha256=digest) for i, c in enumerate(chunks)]
        return dict(path=path, kind=kind, items=[self.s.with_prefix_doc(doc_title, c) for c in chunks], records=records, sha256=digest)


def _same_file(path, state):
    stat = path.stat()
    if stat.st_size != state['size'] or stat.st_mtime_ns != state['mtime_ns']:
        raise ValueError('File changed during indexing; previous vectors retained')


def _file_hash(path, check=lambda: None):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while True:
            check()
            block = source.read(131072)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _valid_vector(vector, dimension):
    return (len(vector) == dimension and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector)
            and math.isfinite(math.hypot(*vector)) and math.hypot(*vector) > 1e-12)


class _Progress:
    def __init__(self, log, total_files):
        self.log, self.total = log, total_files
        self.completed = 0
        self.started = time.monotonic()
        self.last_shown = 0.

    def advance(self):
        self.completed += 1
        self.show()

    def show(self, force=False):
        now = time.monotonic()
        if not self.log or not self.total or (not force and now-self.last_shown < 2):
            return
        self.last_shown = now
        elapsed = max(now-self.started, 1e-6)
        rate = self.completed/elapsed
        eta = _fmt_eta((self.total-self.completed)/rate if rate else None)
        self.log(f'indexing {self.completed}/{self.total} files ({100*self.completed/self.total:.0f}%) · {rate:.1f} files/s · ETA {eta}')

    def finish(self):
        self.show(True)


def _fmt_eta(seconds):
    if seconds is None:
        return '--:--'
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f'{minutes}:{seconds:02}'


def _root_of(path, roots):
    return max((r for r in roots if path.is_relative_to(r)), key=lambda r: len(str(r)), default=path.parent)


def _clean(text):
    return ' '.join(text.split())


def _rel(path, root):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def _under_any(path, roots):
    return any(Path(path).is_relative_to(root) for root in roots)
