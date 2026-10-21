from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

PROBE_ORDER = [
    ("http://127.0.0.1:8765/v1", "openai"),
    ("http://127.0.0.1:1234/v1", "openai"),
    ("http://127.0.0.1:11434", "ollama"),
    ("http://127.0.0.1:8000/v1", "openai"),
    ("http://127.0.0.1:8080/v1", "openai"),
]


def _default_data_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "data")


@dataclass
class Settings:
    api_url: str = "auto"
    api_type: str = "auto"
    model: str = "embeddinggemma-2"
    api_key: str = "lm-studio"
    dim: int | None = 768
    threads: int = 4
    timeout: int = 900
    batch: int = 8
    index_kinds: str = ""
    image_max: int = 1024
    rescan: bool = False
    checkpoint_files: int = 200
    checkpoint_secs: int = 300
    prefixes: bool = True
    query_prompt: str = "task: search result | query: {text}"
    doc_prompt: str = "title: {title} | text: {text}"
    data_dir: str = ""
    max_file_mb: int = 64
    chunk_chars: int = 1600
    chunk_overlap: int = 200
    max_chunks: int = 400
    web_host: str = "127.0.0.1"
    web_port: int = 8088
    hidden: bool = False
    follow_symlinks: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        env = os.environ
        s.api_url = env.get("EG_API_URL", s.api_url)
        s.api_type = env.get("EG_API_TYPE", s.api_type).lower()
        s.model = env.get("EG_MODEL", s.model)
        s.api_key = env.get("EG_API_KEY", s.api_key)
        s.dim = _int_or_none(env.get("EG_DIM"), s.dim)
        s.threads = _int_or(env.get("EG_THREADS"), s.threads)
        s.timeout = _int_or(env.get("EG_TIMEOUT"), s.timeout)
        s.batch = _int_or(env.get("EG_BATCH"), s.batch)
        s.index_kinds = env.get("EG_INDEX_KINDS", s.index_kinds)
        s.image_max = _int_or(env.get("EG_IMAGE_MAX"), s.image_max)
        s.rescan = _bool(env.get("EG_RESCAN"), s.rescan)
        s.checkpoint_files = _int_or(env.get("EG_CHECKPOINT_FILES"), s.checkpoint_files)
        s.checkpoint_secs = _int_or(env.get("EG_CHECKPOINT_SECS"), s.checkpoint_secs)
        s.prefixes = _bool(env.get("EG_PROMPT_PREFIXES"), s.prefixes)
        if env.get("EG_QUERY_PROMPT"):
            s.query_prompt = env["EG_QUERY_PROMPT"]
        if env.get("EG_DOC_PROMPT"):
            s.doc_prompt = env["EG_DOC_PROMPT"]
        s.data_dir = env.get("EG_DATA_DIR") or _default_data_dir()
        s.max_file_mb = _int_or(env.get("EG_MAX_FILE_MB"), s.max_file_mb)
        s.chunk_chars = _int_or(env.get("EG_CHUNK_CHARS"), s.chunk_chars)
        s.chunk_overlap = _int_or(env.get("EG_CHUNK_OVERLAP"), s.chunk_overlap)
        s.max_chunks = _int_or(env.get("EG_MAX_CHUNKS"), s.max_chunks)
        s.web_host = env.get("EG_WEB_HOST", s.web_host)
        s.web_port = _int_or(env.get("EG_WEB_PORT"), s.web_port)
        s.hidden = _bool(env.get("EG_HIDDEN"), s.hidden)
        s.follow_symlinks = _bool(env.get("EG_FOLLOW_SYMLINKS"), s.follow_symlinks)
        if not _valid_dim(s.dim):
            s.dim = None
        return s

    def override(self, **kw) -> "Settings":
        for f in fields(self):
            if f.name in kw and kw[f.name] is not None:
                setattr(self, f.name, kw[f.name])
        return self

    def with_prefix_query(self, text: str) -> str:
        if not self.prefixes:
            return text
        return self.query_prompt.format(text=text)

    def with_prefix_doc(self, title: str, text: str) -> str:
        if not self.prefixes:
            return text
        return self.doc_prompt.format(title=title, text=text)


def _valid_dim(dim) -> bool:
    if dim is None:
        return True
    if not isinstance(dim, int) or isinstance(dim, bool):
        return False
    return dim in (768, 512, 256, 128) or dim <= 768


def _int_or(value, default):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _int_or_none(value, default):
    if value is None or value == "" or str(value).lower() == "auto":
        return default
    return _int_or(value, default)


def _bool(value, default):
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")
