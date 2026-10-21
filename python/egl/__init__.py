from __future__ import annotations

from .config import Settings
from .indexer import Indexer
from .search import Searcher
from .store import Index
from .webui import serve

__version__ = "2.0"
__all__ = ["Settings", "Indexer", "Searcher", "Index", "serve"]
