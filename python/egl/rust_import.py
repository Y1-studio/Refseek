"""Import an EmbeddingGemma 2 Q8 Desktop catalog into an empty Browser library."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from .dimensions import DIMENSIONS
from .folder_store import digest, encoded, folder_id, library_lock, snapshot_path
from .store import _atomic_write
from .textio import kind_of


MODEL = 'embeddinggemma-2-q8'
NATIVE_DIM = 768
FINGERPRINT = f'native-v1:{MODEL}:{NATIVE_DIM}:mean:fullframe1024-lanczos3-jpeg85'


def windows_path(value):
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe source path: {value}')
    return path


def copy_vectors(source, target, count, dimension, checksum):
    if source.stat().st_size != count * dimension * 4:
        raise ValueError(f'Unexpected vector file size: {source}')
    shutil.copyfile(source, target)
    with target.open('r+b') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        os.fsync(stream.fileno())
    if actual != checksum:
        raise ValueError(f'Vector checksum mismatch: {source}')
    return target.stat().st_size


def import_library(source, target):
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('Use separate Desktop and Browser data directories.')
    native_catalog = source / 'catalog.json'
    original = native_catalog.read_bytes()
    native = json.loads(original)
    dimension = native.get('dimension', NATIVE_DIM)
    if dimension not in DIMENSIONS:
        raise ValueError('Unsupported Desktop index dimensions.')
    search_dim = dimension if dimension != NATIVE_DIM else None
    fingerprint = f'{FINGERPRINT}:mrl-{dimension}:native-cache-v1' if search_dim else FINGERPRINT
    if native.get('version') not in (1, 2) or native.get('fingerprint') != fingerprint or not native.get('roots'):
        raise ValueError('Expected an indexed EmbeddingGemma 2 Q8 Desktop library.')

    target.mkdir(parents=True, exist_ok=True)
    reserved = ('catalog.json', 'catalog.json.bak', 'index.json', 'vectors.f32', 'folders')
    with library_lock(target):
        if any((target / name).exists() for name in reserved):
            raise ValueError('Choose an empty Browser index directory. Existing data was preserved.')
        with TemporaryDirectory(prefix='.rust-import-', dir=target) as temp:
            stage = Path(temp)
            entries, roots, seen = [], [], set()
            count = files = 0
            for entry in native['roots']:
                root = windows_path(entry['path'])
                shard = snapshot_path(source, entry['snapshot'])
                raw = (shard / 'index.json').read_bytes()
                if digest(raw) != entry['index_sha']:
                    raise ValueError(f'Metadata checksum mismatch: {shard}')
                meta = json.loads(raw)
                records = meta['records']
                if (meta.get('version') not in (1, 2) or meta.get('fingerprint') != fingerprint
                        or meta.get('dimension', NATIVE_DIM) != dimension or len(records) != entry['vectors']):
                    raise ValueError(f'Unexpected snapshot format: {shard}')
                paths = [windows_path(row['path']) for row in records]
                totals = Counter(paths)
                if root in roots or len(totals) != entry['files'] or not seen.isdisjoint(totals):
                    raise ValueError(f'Unexpected file counts or duplicate files: {root}')
                roots.append(root)
                seen.update(totals)
                for row, path in zip(records, paths):
                    kind = kind_of(path)
                    if kind == 'skip':
                        kind = 'text' if row['kind'] == 'document' else row['kind']
                    row.update(path=str(path), rel=str(path.relative_to(root)), kind=kind, title=path.stem,
                               chunks=totals[path], mtime_ns=row.pop('modified'), ext=path.suffix.lower())
                    row.pop('hash')

                name = str(root)
                updated = entry['updated'] / 1_000_000_000
                content = dict(version=1, root=name, roots=[name], model=MODEL, api_type='openai',
                               dim=NATIVE_DIM, records=records, count=len(records))
                vector_checksum = entry['native_vectors_sha'] if search_dim else entry['vectors_sha']
                if search_dim:
                    content['search_dim'] = search_dim
                identity = folder_id(name)
                signature = digest(encoded(content) + vector_checksum.encode('ascii'))
                relative = f'folders/{identity}/{signature[:24]}'
                output = stage / relative
                output.mkdir(parents=True)
                full_source = 'native-vectors.f32' if search_dim else 'vectors.f32'
                size = copy_vectors(shard / full_source, output / 'vectors.f32', len(records), NATIVE_DIM, vector_checksum)
                index_raw = encoded(dict(content, created=updated, updated=updated))
                _atomic_write(output / 'index.json', index_raw)
                converted = dict(id=identity, root=name, snapshot=relative, count=len(records), files=len(totals),
                                 positions=list(range(count, count + len(records))), index_sha256=digest(index_raw),
                                 vectors_sha256=vector_checksum, fingerprint=signature, created=updated,
                                 updated=updated, size_bytes=size)
                if search_dim:
                    reduced = copy_vectors(shard / 'vectors.f32', output / 'search-vectors.f32',
                                           len(records), search_dim, entry['vectors_sha'])
                    converted.update(search_sha256=entry['vectors_sha'], search_size_bytes=reduced)
                entries.append(converted)
                count += len(records)
                files += len(totals)
                print(f'Verified {len(totals):,} files in {name}', flush=True)

            catalog = dict(format='folder-index-v1', version=2, model=MODEL, api_type='openai', dim=NATIVE_DIM,
                           roots=[str(root) for root in roots], kinds_filter=None, count=count, folders=entries,
                           created=min(e['created'] for e in entries), updated=max(e['updated'] for e in entries))
            if search_dim:
                catalog['search_dim'] = search_dim
            if native_catalog.read_bytes() != original:
                raise RuntimeError('Desktop changed its index during import. Try again when indexing has finished.')
            staged_folders, folders = stage / 'folders', target / 'folders'
            os.rename(staged_folders, folders)
            try:
                _atomic_write(target / 'catalog.json', encoded(catalog))
            except OSError:
                os.rename(folders, staged_folders)
                raise
    return dict(files=files, vectors=count, dimensions=dimension, roots=catalog['roots'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('desktop_library', type=Path)
    parser.add_argument('browser_library', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(import_library(args.desktop_library, args.browser_library), ensure_ascii=False))
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f'Import failed: {error}\n')
