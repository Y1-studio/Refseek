from __future__ import annotations

import os
from pathlib import Path

from .config import Settings
from .portable import shared_data_dir
from .textio import kind_of

INDEX_KINDS = ("image", "pdf", "docx", "pptx", "xlsx", "epub", "odt",
               "text", "code", "audio", "video")

# extensions the indexer recognises but neither Pillow/stb nor the embedding
# APIs can decode — skipping at scan time saves a read + a doomed embed try.
NODECODE_EXTS = frozenset({".avif", ".heic", ".heif"})


def _cancelled(cancel):
    from .api import IndexCancelled
    if cancel and cancel():
        raise IndexCancelled("Indexing cancelled; existing vectors retained")


def iter_files(root: Path, settings: Settings, cancel=None):
    root = Path(root).expanduser().resolve()
    data_dir = shared_data_dir(settings.data_dir).resolve()
    def failed(error):
        raise error  # An incomplete scan cannot safely identify deleted files.
    for dirpath, dirnames, filenames in os.walk(root, followlinks=settings.follow_symlinks, onerror=failed):
        _cancelled(cancel)
        pruned = []
        for d in dirnames:
            entry = Path(dirpath) / d
            if not settings.follow_symlinks and (entry.is_symlink() or entry.is_junction()):
                continue
            child = entry.resolve()
            if child == data_dir or data_dir in child.parents:
                continue
            if not settings.hidden and d.startswith("."):
                continue
            pruned.append(d)
        dirnames[:] = sorted(pruned)
        for name in sorted(filenames):
            _cancelled(cancel)
            if not settings.hidden and name.startswith("."):
                continue
            entry = Path(dirpath) / name
            if entry.is_symlink() and (not settings.follow_symlinks or entry.resolve().is_relative_to(data_dir)):
                continue
            yield entry


def scan_folder(folder, settings: Settings, cancel=None) -> tuple[dict, list[tuple[Path, str]]]:
    root = Path(folder).expanduser().resolve()
    states: dict[str, dict] = {}
    skipped: list[tuple[Path, str]] = []
    if not root.exists():
        raise FileNotFoundError(f"folder not found: {root}")
    if root.is_relative_to(shared_data_dir(settings.data_dir).resolve()):
        raise ValueError('Choose a picture folder outside Refseek’s data folder.')
    cap = settings.max_file_mb * 1024 * 1024
    for path in iter_files(root, settings, cancel):
        kind = kind_of(path)
        if kind == "skip":
            continue
        if path.suffix.lower() in NODECODE_EXTS:
            skipped.append((path, "image format has no decoder here (skipped)"))
            continue
        st = path.stat()  # Fail without publishing removals if a file is unreadable.
        if st.st_size > cap:
            skipped.append((path, f"larger than {settings.max_file_mb}MB"))
            continue
        states[str(path)] = {
            "mtime_ns": st.st_mtime_ns,
            "size": st.st_size,
            "kind": kind,
            "n": 0,
        }
    return states, skipped
