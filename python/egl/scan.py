from __future__ import annotations

import os
from pathlib import Path

from .config import Settings
from .textio import kind_of

INDEX_KINDS = ("image", "pdf", "docx", "pptx", "xlsx", "epub", "odt",
               "text", "code", "audio", "video")

# extensions the indexer recognises but neither Pillow/stb nor the embedding
# APIs can decode — skipping at scan time saves a read + a doomed embed try.
NODECODE_EXTS = frozenset({".avif", ".heic", ".heif"})


def iter_files(root: Path, settings: Settings):
    root = Path(root).expanduser().resolve()
    data_dir = Path(settings.data_dir).expanduser().resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=settings.follow_symlinks):
        pruned = []
        for d in dirnames:
            child = (Path(dirpath) / d).resolve()
            if child == data_dir or data_dir in child.parents:
                continue
            if not settings.hidden and d.startswith("."):
                continue
            pruned.append(d)
        dirnames[:] = sorted(pruned)
        for name in sorted(filenames):
            if not settings.hidden and name.startswith("."):
                continue
            yield Path(dirpath) / name


def scan_folder(folder, settings: Settings) -> tuple[dict, list[tuple[Path, str]]]:
    root = Path(folder).expanduser().resolve()
    states: dict[str, dict] = {}
    skipped: list[tuple[Path, str]] = []
    if not root.exists():
        raise FileNotFoundError(f"folder not found: {root}")
    cap = settings.max_file_mb * 1024 * 1024
    for path in iter_files(root, settings):
        kind = kind_of(path)
        if kind == "skip":
            continue
        if path.suffix.lower() in NODECODE_EXTS:
            skipped.append((path, "image format has no decoder here (skipped)"))
            continue
        try:
            st = path.stat()
        except OSError:
            continue
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
