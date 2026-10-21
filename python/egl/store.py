from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from array import array
from pathlib import Path

try:
    import numpy as _np
except Exception:
    _np = None

VERSION = 1
_KEEP = object()


class CorruptIndexError(RuntimeError):
    pass


def _read_retry(path: Path, tries: int = 2) -> bytes:
    last = None
    for _ in range(tries):
        try:
            return path.read_bytes()
        except PermissionError as e:
            last = e
            time.sleep(1.0)
        except OSError as e:
            raise CorruptIndexError(f"{path} unreadable: {e}")
    raise CorruptIndexError(f"{path} unreadable (locked by antivirus/sync?): {last}")


class Index:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.meta_path = self.data_dir / "index.json"
        self.vec_path = self.data_dir / "vectors.f32"
        self.dim = None
        self.model = None
        self.api_type = None
        self.roots = []
        self.kind_filter = None
        self.created = None
        self.updated = None
        self.records: list[dict] = []
        self.vectors: list[array] = []
        self._matrix = None
        self._problem = None
        self._catalog_digest = None
        self._catalog = None
        self._folder_entries = []
        self._partial = False

    @classmethod
    def load(cls, data_dir, roots=None):
        if Path(data_dir,'catalog.json').exists():
            from .folder_store import load
            return load(cls(data_dir),roots=roots)
        return cls._load_flat(data_dir)

    @staticmethod
    def stamp(data_dir):
        root=Path(data_dir)
        path=root/('catalog.json' if (root/'catalog.json').exists() else 'index.json')
        try:
            stat=path.stat()
            return path.name,stat.st_mtime_ns,stat.st_size
        except OSError:return None

    @classmethod
    def _load_flat(cls, data_dir):
        idx = cls(data_dir)
        idx._problem = None
        if not idx.meta_path.exists():
            return idx
        try:
            raw_text = _read_retry(idx.meta_path).decode("utf-8")
        except CorruptIndexError:
            raise
        except Exception as e:
            raise CorruptIndexError(f"{idx.meta_path} unreadable: {e}")
        try:
            meta = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise CorruptIndexError(
                f"{idx.meta_path} is not valid JSON "
                f"(line {e.lineno}, column {e.colno}: {e.msg}); "
                f"a previous copy may exist at {idx.meta_path.with_name('index.json.bak')}"
            )
        if not isinstance(meta, dict) or not isinstance(meta.get("records"), list):
            raise CorruptIndexError(f"{idx.meta_path} is not an index file")
        idx.dim = meta.get("dim")
        idx.model = meta.get("model")
        idx.api_type = meta.get("api_type")
        idx.roots = meta.get("roots") or []
        kf = meta.get("kinds_filter")
        idx.kind_filter = [str(x).strip() for x in kf if str(x).strip()] if isinstance(kf, list) and kf else None
        idx.created = meta.get("created")
        idx.updated = meta.get("updated")
        idx.records = meta.get("records") or []
        n = len(idx.records)
        d = idx.dim or 0
        try:
            raw = _read_retry(idx.vec_path) if idx.vec_path.exists() else b""
        except CorruptIndexError as e:
            raise CorruptIndexError(f"vectors file {e}")
        arr = array("f")
        if raw:
            arr.frombytes(raw[: len(raw) - (len(raw) % 4)] if d == 0 else raw)
        if sys.byteorder == "big":
            arr.byteswap()
        have = len(arr) // d if d else 0
        idx.vectors = [arr[i * d : (i + 1) * d] for i in range(min(n, have))] if d else []
        if len(idx.vectors) < n:
            idx.records = idx.records[: len(idx.vectors)]
            idx._problem = (
                f"vectors file holds {have} of {n} records "
                f"({len(raw)} bytes, expected {n * d * 4}); index truncated"
            )
        return idx

    def verify(self) -> dict:
        report = {"ok": True, "records": 0, "vectors": 0, "dim": None, "error": None}
        try:
            if (self.data_dir/'catalog.json').exists():
                checked=type(self).load(self.data_dir)
                return dict(report,records=len(checked.records),vectors=len(checked.vectors),dim=checked.dim,
                            folder_indexes=len(checked._folder_entries))
            if not self.meta_path.exists():
                return report
            meta = json.loads(_read_retry(self.meta_path).decode("utf-8"))
            records = meta.get("records") or []
            dim = meta.get("dim") or 0
            report["records"] = len(records)
            report["dim"] = dim
            actual = self.vec_path.stat().st_size if self.vec_path.exists() else -1
            report["vectors"] = actual // (dim * 4) if dim and actual >= 0 else 0
            if actual != len(records) * dim * 4:
                report.update(
                    ok=False,
                    error=f"vectors file is {actual} bytes but "
                    f"{len(records)}x{dim} records need {len(records) * dim * 4}",
                )
        except CorruptIndexError as e:
            report.update(ok=False, error=str(e))
        except Exception as e:
            report.update(ok=False, error=f"{type(e).__name__}: {e}")
        return report

    def add(self, record: dict, vector):
        self.records.append(record)
        self.vectors.append(array("f", vector))
        self._matrix = None

    def ensure_dim(self, dim):
        if not self.dim:
            self.dim = dim
            return True
        return self.dim == dim

    def drop_paths(self, paths: set) -> int:
        if not self.records:
            return 0
        keep_r, keep_v = [], []
        dropped = 0
        for rec, vec in zip(self.records, self.vectors):
            if rec.get("path") in paths:
                dropped += 1
                continue
            keep_r.append(rec)
            keep_v.append(vec)
        self.records, self.vectors = keep_r, keep_v
        self._matrix = None
        return dropped

    def set_state(self, model: str, api_type: str, roots, kind_filter=_KEEP):
        self.model = model
        self.api_type = api_type
        self.roots = list(roots)
        if kind_filter is not _KEEP:
            self.kind_filter = list(kind_filter) if kind_filter else None
        if not self.created:
            self.created = time.time()
        self.updated = time.time()

    def save(self):
        from .folder_store import save
        return save(self)

    def _save_flat(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        d = self.dim or 0
        flat = array("f", bytes(4 * d * len(self.vectors)))
        for i, vec in enumerate(self.vectors):
            flat[i * d : (i + 1) * d] = array("f", vec)
        if sys.byteorder == "big":
            flat.byteswap()
        _atomic_write(self.vec_path, flat.tobytes())
        meta = {
            "version": VERSION,
            "model": self.model,
            "api_type": self.api_type,
            "dim": self.dim,
            "roots": self.roots,
            "kinds_filter": self.kind_filter,
            "created": self.created,
            "updated": self.updated,
            "count": len(self.records),
            "records": self.records,
        }
        meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
        bak = self.meta_path.with_name("index.json.bak")
        if self.meta_path.exists():
            try:
                os.replace(self.meta_path, bak)
            except OSError:
                pass
        try:
            _atomic_write(self.meta_path, meta_bytes)
            self._verify_saved(len(self.records), d)
        except Exception:
            try:
                if bak.exists():
                    os.replace(bak, self.meta_path)
            except OSError:
                pass
            raise

    def _verify_saved(self, n: int, d: int):
        try:
            saved = json.loads(self.meta_path.read_bytes().decode("utf-8"))
        except Exception as e:
            raise CorruptIndexError(f"index.json failed verification after save: {e}")
        if len(saved.get("records") or []) != n:
            raise CorruptIndexError(
                f"index.json failed verification after save "
                f"({len(saved.get('records') or [])} records on disk, {n} expected)"
            )
        actual = self.vec_path.stat().st_size if self.vec_path.exists() else -1
        if actual != n * d * 4:
            raise CorruptIndexError(
                f"vectors file failed verification after save ({actual} bytes, {n * d * 4} expected)"
            )

    def file_states(self) -> dict:
        out = {}
        for rec in self.records:
            path = rec.get("path")
            if not path:
                continue
            st = out.setdefault(path, {"n": 0})
            st["n"] += 1
            st["kind"] = rec.get("kind")
            st["mtime_ns"] = rec.get("mtime_ns")
            st["size"] = rec.get("size")
        return out

    def records_by_path(self) -> dict:
        out: dict[str, list[int]] = {}
        for i, rec in enumerate(self.records):
            out.setdefault(rec.get("path"), []).append(i)
        return out

    def matrix(self):
        if _np is None:
            return None
        if self._matrix is None and self.vectors and self.dim:
            rows = [array("f", v).tobytes() for v in self.vectors]
            self._matrix = _np.frombuffer(b"".join(rows), dtype="<f4").reshape(len(self.vectors), self.dim)
        return self._matrix

    def search(self, query, k=20, kinds=None, negatives=None, neg_weight=1.0, roots=None,
               offset=0, neg_images=None, neg_image_weight=1.0) -> list[dict]:
        if not self.records or not self.dim:
            return []
        norm = sum(x * x for x in query) ** 0.5 or 1.0
        q = array("f", [x / norm for x in query])
        n = len(self.vectors)
        sep = os.sep
        rootpfx = None
        if roots:
            rootpfx = [str(r).rstrip("/\\") + sep for r in roots if str(r).strip()]
            if not rootpfx:
                rootpfx = None
        mat = self.matrix()
        if mat is not None:
            scores = mat.dot(_np.asarray(q, dtype=_np.float32)).tolist()
            pos = list(scores)
            if negatives:
                normed = []
                for nv in negatives:
                    nnorm = sum(x * x for x in nv) ** 0.5 or 1.0
                    normed.append(_np.asarray([x / nnorm for x in nv], dtype=_np.float32))
                nq = sum(normed) / len(normed)
                neg = mat.dot(nq).tolist()
                scores = [s - neg_weight * t for s, t in zip(scores, neg)]
            if neg_images:
                normed = []
                for nv in neg_images:
                    nnorm = sum(x * x for x in nv) ** 0.5 or 1.0
                    normed.append(_np.asarray([x / nnorm for x in nv], dtype=_np.float32))
                nq = sum(normed) / len(normed)
                neg = mat.dot(nq).tolist()
                scores = [s - neg_image_weight * t for s, t in zip(scores, neg)]
        else:
            scores = [math.sumprod(q, v) for v in self.vectors]
            pos = list(scores)
            if negatives:
                normed = []
                for nv in negatives:
                    nnorm = sum(x * x for x in nv) ** 0.5 or 1.0
                    normed.append([x / nnorm for x in nv])
                mean = [sum(col) / len(normed) for col in zip(*normed)]
                nq = array("f", mean)
                neg = [math.sumprod(nq, v) for v in self.vectors]
                scores = [s - neg_weight * t for s, t in zip(scores, neg)]
            if neg_images:
                normed = []
                for nv in neg_images:
                    nnorm = sum(x * x for x in nv) ** 0.5 or 1.0
                    normed.append([x / nnorm for x in nv])
                mean = [sum(col) / len(normed) for col in zip(*normed)]
                nq = array("f", mean)
                neg = [math.sumprod(nq, v) for v in self.vectors]
                scores = [s - neg_image_weight * t for s, t in zip(scores, neg)]
        hits = []
        kindset = set(kinds) if kinds else None
        for i, (score, rec) in enumerate(zip(scores, self.records)):
            if kindset and rec.get("kind") not in kindset:
                continue
            if rootpfx is not None:
                p = rec.get("path") or ""
                if not any(p.startswith(pfx) for pfx in rootpfx):
                    continue
            hits.append((float(score), i, pos[i] if pos is not None else float(score), rec))
        hits.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, _, base, rec in hits[max(0, offset):max(0, offset) + k]:
            item = dict(rec)
            item["score"] = round(score, 4)
            if negatives or neg_images:
                item["pos"] = round(float(base), 4)
                item["penalty"] = round(float(base - score), 4)
            out.append(item)
        return out

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for rec in self.records:
            kinds[rec.get("kind", "?")] = kinds.get(rec.get("kind", "?"), 0) + 1
        return {
            "vectors": len(self.records),
            "dim": self.dim,
            "model": self.model,
            "api_type": self.api_type,
            "files": len({r.get("path") for r in self.records}),
            "kinds": kinds,
            "kind_filter": self.kind_filter,
            "roots": self.roots,
            "updated": self.updated,
            "size_bytes": (sum(e['size_bytes'] for e in self._folder_entries) if self._catalog
                           else self.vec_path.stat().st_size if self.vec_path.exists() else 0),
            "storage": self.storage_info(),
        }

    def storage_info(self):
        return dict(format='per-folder' if self._catalog else 'legacy',
                    folders=len(self._folder_entries),catalog=str(self.data_dir/'catalog.json'),
                    indexes=[dict(root=e['root'],id=e['id'],files=e['files'],vectors=e['count'],size_bytes=e['size_bytes'],
                                  index_dir=str(self.data_dir/e['snapshot'])) for e in self._folder_entries])

    def reset(self):
        from .folder_store import reset
        reset(self)


def _atomic_write(path: Path, data: bytes):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
