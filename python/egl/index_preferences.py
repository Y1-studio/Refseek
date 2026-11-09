"""Remember indexing controls without changing an existing library's dimension."""
import json
import os
from pathlib import Path


def cpu_limit():
    return max(1, os.cpu_count() or 1)


def load(data_dir, fallback_kinds=None, fallback_workers=2):
    from .scan import INDEX_KINDS
    try:
        raw = json.loads((Path(data_dir) / 'index-preferences.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    kinds = raw.get('kinds', fallback_kinds if fallback_kinds is not None else ['image'])
    if not isinstance(kinds, list) or any(not isinstance(k, str) or k not in INDEX_KINDS for k in kinds):
        kinds = ['image']
    workers = raw.get('prepare_workers', fallback_workers)
    if type(workers) is not int or workers < 1:
        workers = 2
    return dict(kinds=list(dict.fromkeys(kinds)), prepare_workers=min(workers, cpu_limit()))


def save(data_dir, **changes):
    from .scan import INDEX_KINDS
    from .store import _atomic_write
    path = Path(data_dir) / 'index-preferences.json'
    try:
        original = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        original = {}
    if not isinstance(original, dict):
        raise ValueError('Invalid indexing preferences file')
    value = load(data_dir)
    if 'kinds' in changes:
        kinds = changes['kinds']
        if not isinstance(kinds, list) or any(not isinstance(k, str) or k not in INDEX_KINDS for k in kinds):
            raise ValueError('Choose known file types.')
        value['kinds'] = list(dict.fromkeys(kinds))
    if 'prepare_workers' in changes:
        workers = changes['prepare_workers']
        if type(workers) is not int or not 1 <= workers <= cpu_limit():
            raise ValueError(f'Choose 1 through {cpu_limit()} CPU preparation workers.')
        value['prepare_workers'] = workers
    merged = dict(original, **value)
    if not path.exists() or merged != original:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(merged).encode('utf-8'))
    return value
