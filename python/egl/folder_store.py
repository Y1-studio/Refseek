"""Per-root immutable index snapshots behind an atomically committed catalog.

Native vectors are not re-embedded. Existing single-file indexes remain readable
and are kept as a migration backup. The library lock covers readers and commits,
not embedding API calls. Unchanged folders keep their existing files.
"""
from __future__ import annotations
from array import array
from contextlib import contextmanager
import hashlib, json, os, re, sys, time
from pathlib import Path


def encoded(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')


def digest(raw):return hashlib.sha256(raw).hexdigest()


@contextmanager
def library_lock(root, name='.library.lock'):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    # OS locks release on process death; a leftover lock file is harmless.
    with (root/name).open('a+b') as stream:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        deadline=time.monotonic()+30
        while True:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:raise RuntimeError('Library is busy committing an index; retry shortly')
                time.sleep(.05)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def read_catalog(root, name='catalog.json'):
    from .store import CorruptIndexError, _read_retry
    path=Path(root)/name
    try:
        raw=_read_retry(path);catalog=json.loads(raw.decode('utf-8'))
        if catalog.get('format')!='folder-index-v1' or not isinstance(catalog.get('folders'),list):
            raise ValueError('unsupported folder catalog')
        if (not isinstance(catalog.get('count'),int) or isinstance(catalog['count'],bool) or catalog['count']<0
                or not isinstance(catalog.get('roots'),list) or any(not isinstance(r,str) for r in catalog['roots'])):
            raise ValueError('invalid catalog counts or roots')
        for entry in catalog['folders']:
            required={'id','root','snapshot','count','files','positions','index_sha256','vectors_sha256','fingerprint','size_bytes'}
            if not isinstance(entry,dict) or not required.issubset(entry):raise ValueError('incomplete folder entry')
            if (not isinstance(entry['count'],int) or isinstance(entry['count'],bool) or entry['count']<0
                    or not isinstance(entry['positions'],list) or len(entry['positions'])!=entry['count']
                    or any(not isinstance(p,int) or isinstance(p,bool) or p<0 for p in entry['positions'])
                    or not isinstance(entry['id'],str) or not isinstance(entry['snapshot'],str)
                    or entry['root'] is not None and not isinstance(entry['root'],str)):
                raise ValueError('invalid folder entry')
        return catalog,raw
    except (ValueError,TypeError,AttributeError) as exc:
        raise CorruptIndexError(f'{path}: {exc}; a previous catalog may be in catalog.json.bak') from exc


def snapshot_path(root,relative):
    from .store import CorruptIndexError
    base=Path(root).resolve();path=(base/relative).resolve()
    if not path.is_relative_to(base/'folders') or len(path.relative_to(base).parts)!=3:
        raise CorruptIndexError('Folder catalog contains an invalid snapshot path')
    return path


def selected(entry,roots):
    if not roots:return True
    root=entry.get('root')
    if not root:return True
    return any(Path(root).is_relative_to(Path(r)) or Path(r).is_relative_to(Path(root)) for r in roots)


def read_folder_tree(root):
    from .folder_scope import folder_tree
    from .store import CorruptIndexError, _read_retry

    paths = set()
    with library_lock(root):
        catalog, _ = read_catalog(root)
        for entry in catalog['folders']:
            raw = _read_retry(snapshot_path(root, entry['snapshot']) / 'index.json')
            if digest(raw) != entry['index_sha256']:
                raise CorruptIndexError('Folder metadata checksum mismatch.')
            try:
                paths.update(row['path'] for row in json.loads(raw)['records'])
            except (ValueError, TypeError, KeyError) as error:
                raise CorruptIndexError('Invalid folder metadata.') from error
    return folder_tree(catalog['roots'], ({'path': path} for path in paths))


def load(index,roots=None):
    from .store import CorruptIndexError, _read_retry
    from .dimensions import DIMENSIONS
    with library_lock(index.data_dir):
        catalog,raw=read_catalog(index.data_dir)
        for attr,key in [('dim','dim'),('search_dim','search_dim'),('model','model'),('api_type','api_type'),('roots','roots'),
                         ('kind_filter','kinds_filter'),('created','created'),('updated','updated')]:
            setattr(index,attr,catalog.get(key))
        index.roots=index.roots or []
        if index.search_dim is not None and (index.search_dim not in DIMENSIONS or not index.dim or index.search_dim >= index.dim):
            raise CorruptIndexError('Invalid saved search dimensions')
        index._catalog_digest=digest(raw);index._catalog=catalog
        index._partial=bool(roots);index._folder_entries=catalog['folders']
        records=[];vectors=[];reduced=[];positions=[];all_positions=[];ids=set()
        for entry in catalog['folders']:
            order=entry['positions'];all_positions.extend(order)
            if entry['id'] in ids or len(order)!=entry['count']:raise CorruptIndexError('Invalid folder catalog counts or repeated IDs')
            ids.add(entry['id'])
            if not selected(entry,roots):continue
            folder=snapshot_path(index.data_dir,entry['snapshot'])
            try:
                meta_raw=_read_retry(folder/'index.json');vec_raw=_read_retry(folder/'vectors.f32')
                if digest(meta_raw)!=entry['index_sha256'] or digest(vec_raw)!=entry['vectors_sha256']:
                    raise CorruptIndexError('index/vector checksum mismatch')
                meta=json.loads(meta_raw.decode('utf-8'));rows=meta['records'];dim=index.dim or 0
                if (len(rows)!=entry['count'] or meta['dim']!=index.dim or meta['model']!=index.model
                        or len(vec_raw)!=len(rows)*dim*4 or (rows and not dim)):
                    raise CorruptIndexError('index/vector counts or model dimensions differ')
                values=array('f');values.frombytes(vec_raw)
                if sys.byteorder=='big':values.byteswap()
                if index.search_dim:
                    size = index.search_dim
                    raw_search = _read_retry(folder/'search-vectors.f32')
                    if len(raw_search) != len(rows)*size*4 or digest(raw_search) != entry.get('search_sha256'):
                        raise CorruptIndexError('reduced search vectors failed verification')
                    values_search = array('f'); values_search.frombytes(raw_search)
                    if sys.byteorder == 'big': values_search.byteswap()
                    reduced.extend(values_search[i*size:(i+1)*size] for i in range(len(rows)))
                if any(not isinstance(p,int) or isinstance(p,bool) for p in order):
                    raise CorruptIndexError('invalid record ordering')
                records.extend(rows);vectors.extend(values[i*dim:(i+1)*dim] for i in range(len(rows)));positions.extend(order)
            except (OSError,ValueError,TypeError,KeyError,CorruptIndexError) as exc:
                raise CorruptIndexError(f'Folder index {entry.get("root") or entry["id"]}: {exc}. '
                                        'Restore this folder snapshot or its previous catalog; other folder files are independent.') from exc
        if sorted(all_positions)!=list(range(catalog['count'])):
            raise CorruptIndexError('Folder catalog ordering is incomplete or repeated')
        order=sorted(range(len(records)),key=positions.__getitem__)
        index.records=[records[i] for i in order];index.vectors=[vectors[i] for i in order]
        if index.search_dim:
            index._reduced_vectors = [reduced[i] for i in order]
    return index


def owner(path,roots):
    candidates=[r for r in roots if Path(path).is_relative_to(Path(r))]
    return max(candidates,key=lambda r:len(Path(r).parts)) if candidates else None


def folder_id(root):
    name=Path(root).name if root else 'unassigned'
    slug=re.sub(r'[^a-zA-Z0-9_-]+','-',name).strip('-')[:40] or 'folder'
    identity=os.path.normcase(os.path.normpath(root)) if root else '_unassigned'
    return slug+'-'+digest(identity.encode('utf-8'))[:12]


def save(index):
    from .store import CorruptIndexError, _atomic_write
    from .dimensions import DIMENSIONS, prefix_vector
    if getattr(index,'_partial',False):raise ValueError('A folder-filtered search view cannot be saved')
    if index._problem:raise CorruptIndexError('Refusing to migrate/save an incomplete index: '+index._problem)
    if len(index.records)!=len(index.vectors):raise CorruptIndexError('Record/vector counts differ before saving')
    dim=index.dim or 0
    search_dim = index.search_dim if dim and index.search_dim != dim else None
    if search_dim is not None and (search_dim not in DIMENSIONS or search_dim >= dim):
        raise CorruptIndexError('Selected search dimensions exceed the model output')
    if any(len(v)!=dim for v in index.vectors) or (index.records and not dim):
        raise CorruptIndexError('Invalid vector dimensions before saving')
    root=index.data_dir;root.mkdir(parents=True,exist_ok=True)
    with library_lock(root):
        current,old_raw=read_catalog(root) if (root/'catalog.json').exists() else ({'folders':[]},None)
        expected=getattr(index,'_catalog_digest',None)
        if (digest(old_raw) if old_raw is not None else None)!=expected:
            raise RuntimeError('Library changed in another process; reload before saving to avoid losing its updates')
        previous={entry['root']:entry for entry in current['folders']}
        existing_names={os.path.normcase(os.path.normpath(r)):r for r in previous if r}
        canonical=[];seen=set()
        for folder in index.roots:
            identity=os.path.normcase(os.path.normpath(folder))
            if identity not in seen:
                seen.add(identity);canonical.append(existing_names.get(identity,folder))
        index.roots=canonical
        groups={r:[] for r in index.roots}
        prefixes=sorted(((os.path.normcase(r).rstrip('/\\')+os.sep,r) for r in index.roots),key=lambda p:len(p[0]),reverse=True)
        owners={}
        for i,record in enumerate(index.records):
            path=record['path']
            if path not in owners:
                normalized=os.path.normcase(path)
                owners[path]=next((r for prefix,r in prefixes if normalized.startswith(prefix)),None)
            groups.setdefault(owners[path],[]).append(i)
        entries=[];written=0
        for folder,positions in groups.items():
            rows=[index.records[i] for i in positions]
            values=array('f')
            for i in positions:values.extend(iter(index.vectors[i]))
            if sys.byteorder=='big':values.byteswap()
            vec_raw=values.tobytes();vec_hash=digest(vec_raw)
            content=dict(version=1,root=folder,roots=[folder] if folder else [],model=index.model,api_type=index.api_type,dim=index.dim,records=rows,count=len(rows))
            if search_dim:
                content['search_dim'] = search_dim
            fingerprint=digest(encoded(content)+vec_hash.encode())
            old=previous.get(folder)
            if old and old['fingerprint']==fingerprint:
                entry=dict(old,positions=positions)
            else:
                ident=folder_id(folder);snapshot=f'folders/{ident}/{fingerprint[:24]}'
                destination=snapshot_path(root,snapshot);destination.mkdir(parents=True,exist_ok=True)
                meta=dict(content,created=old.get('created') if old else index.created,updated=index.updated)
                meta_raw=encoded(meta)
                search_raw = None
                if search_dim:
                    search_values = array('f')
                    for i in positions: search_values.extend(prefix_vector(index.vectors[i], search_dim))
                    if sys.byteorder == 'big': search_values.byteswap()
                    search_raw = search_values.tobytes()
                # A previously committed snapshot is immutable, even when content
                # returns to an earlier state after a remove/re-add cycle.
                if (destination/'index.json').exists() and (destination/'vectors.f32').exists():
                    existing=(destination/'index.json').read_bytes()
                    restored=json.loads(existing.decode('utf-8'))
                    if any(restored.get(k)!=v for k,v in content.items()) or digest((destination/'vectors.f32').read_bytes())!=vec_hash:
                        raise CorruptIndexError('Existing snapshot content does not match its identity')
                    meta_raw=existing
                else:
                    _atomic_write(destination/'vectors.f32',vec_raw)
                    if search_raw is not None:
                        _atomic_write(destination/'search-vectors.f32',search_raw)
                    _atomic_write(destination/'index.json',meta_raw)
                if digest((destination/'vectors.f32').read_bytes())!=vec_hash or digest((destination/'index.json').read_bytes())!=digest(meta_raw):
                    raise CorruptIndexError('Folder snapshot failed verification after save')
                entry=dict(id=ident,root=folder,snapshot=snapshot,count=len(rows),files=len({r['path'] for r in rows}),
                           positions=positions,index_sha256=digest(meta_raw),vectors_sha256=vec_hash,
                           fingerprint=fingerprint,created=meta.get('created'),updated=meta.get('updated'),size_bytes=len(vec_raw))
                if search_raw is not None:
                    entry.update(search_sha256=digest(search_raw), search_size_bytes=len(search_raw))
                    if digest((destination/'search-vectors.f32').read_bytes()) != entry['search_sha256']:
                        raise CorruptIndexError('Reduced vectors failed verification after save')
                written+=1
            entries.append(entry)
        catalog=dict(format='folder-index-v1',version=2,model=index.model,api_type=index.api_type,dim=index.dim,
                     roots=index.roots,kinds_filter=index.kind_filter,created=index.created,updated=index.updated,
                     count=len(index.records),folders=entries)
        if search_dim:
            catalog['search_dim'] = search_dim
        index.search_dim = search_dim
        index._reduced_vectors = index._reduced_matrix = None
        index.__dict__.pop('_folder_tree', None)
        if old_raw is not None and all(current.get(k) == value for k, value in catalog.items() if k != 'updated'):
            index.updated = current.get('updated')
            index.last_save = dict(folders_written=0, folders_unchanged=len(entries))
            return
        raw=encoded(catalog)
        # Publish first: a failed active replace must never rotate recovery.
        _atomic_write(root/'catalog.json',raw)
        recovery_warning = None
        if old_raw is not None:
            try:
                _atomic_write(root/'catalog.json.bak',old_raw)
            except OSError as exc:
                recovery_warning = 'Index saved, but recovery could not be updated; older snapshots retained: ' + str(exc)
        index._catalog_digest=digest(raw);index._catalog=catalog;index._folder_entries=entries
        index._matrix=None;index.last_save=dict(folders_written=written,folders_unchanged=len(entries)-written, warnings=[recovery_warning] if recovery_warning else [])
        # Readers hold the same lock while loading complete snapshots. Only
        # snapshots referenced by neither current nor previous catalog are pruned.
        retain={e['snapshot'] for e in entries}|{e['snapshot'] for e in current['folders']}
        if not recovery_warning:
            try:
                prune(root,retain)
            except OSError:
                pass  # A verified, published index stays valid if cleanup must be retried.


def plain(path):
    return not path.is_symlink() and not path.is_junction()


def prune(root, retain):
    root = Path(root).resolve()
    base = root / 'folders'
    report = dict(removed=0, freed_bytes=0, failed=0)
    if not plain(base) or not base.is_dir():
        return report
    for directory in base.iterdir():
        if not re.fullmatch(r'[A-Za-z0-9_-]+-[0-9a-f]{12}', directory.name) or not plain(directory) or not directory.is_dir():
            continue
        for revision in directory.iterdir():
            if (revision.relative_to(root).as_posix() in retain or not re.fullmatch(r'[0-9a-f]{24}', revision.name)
                    or not plain(revision) or not revision.is_dir()):
                continue
            try:
                files = list(revision.iterdir())
                if any(p.name not in ('index.json', 'vectors.f32', 'search-vectors.f32') or not plain(p) or not p.is_file() for p in files):
                    continue
                for path in files:
                    size = path.stat().st_size
                    path.unlink()
                    report['freed_bytes'] += size
                revision.rmdir()
                report['removed'] += 1
            except OSError:
                report['failed'] += 1
    return report


def cleanup(root):
    """Validate both catalogs and their contents before removing anything."""
    from .store import CorruptIndexError

    root = Path(root).resolve()
    with library_lock(root):
        retain, verified = set(), set()
        for name in ('catalog.json', 'catalog.json.bak'):
            catalog_path = root / name
            if not plain(catalog_path):
                raise CorruptIndexError(f'Cleanup refused: linked catalog {name}')
            if not catalog_path.exists():
                if name == 'catalog.json':
                    return dict(removed=0, freed_bytes=0, failed=0)
                continue
            catalog, _ = read_catalog(root, name)
            for entry in catalog['folders']:
                rel = entry['snapshot']
                folder = snapshot_path(root, rel)
                if folder != root / rel or any(not plain(p) for p in (folder, folder.parent, folder.parent.parent)):
                    raise CorruptIndexError(f'Cleanup refused: linked snapshot {rel}')
                files = [('index.json', 'index_sha256'), ('vectors.f32', 'vectors_sha256')]
                if catalog.get('search_dim'):
                    files.append(('search-vectors.f32', 'search_sha256'))
                for filename, field in files:
                    path = folder / filename
                    expected = entry.get(field)
                    if (path, expected) in verified:
                        continue
                    if not plain(path) or not path.is_file():
                        raise CorruptIndexError(f'Cleanup refused: missing or linked {path}')
                    with path.open('rb') as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                            raise CorruptIndexError(f'Cleanup refused: hash mismatch for {path}')
                    verified.add((path, expected))
                retain.add(rel)
        return prune(root, retain)


def migrate(data_dir):
    from .store import Index
    root=Path(data_dir)
    if (root/'catalog.json').exists():return Index.load(root)
    index=Index.load(root)
    if (root/'index.json').exists():
        check=index.verify()
        if not check['ok']:
            from .store import CorruptIndexError
            raise CorruptIndexError('Legacy index must be repaired before migration: '+str(check['error']))
        index.save()
    return index


def reset(index):
    """Clear the active catalog even if it is damaged; preserve recovery files."""
    from .store import _atomic_write
    root=index.data_dir
    with library_lock(root):
        path=root/'catalog.json'
        old_raw = None
        if path.exists():
            try:
                _, old_raw = read_catalog(root)
            except Exception:
                pass  # Do not replace valid recovery with a damaged active catalog.
        catalog=dict(format='folder-index-v1',version=2,model=None,api_type=None,dim=None,roots=[],
                     kinds_filter=None,created=None,updated=time.time(),count=0,folders=[])
        raw=encoded(catalog);_atomic_write(path,raw)
        if old_raw is not None:
            try:_atomic_write(root/'catalog.json.bak',old_raw)
            except OSError:pass  # Keep existing recovery and all snapshot payloads.
        index.records=[];index.vectors=[];index.dim=None;index.model=None;index.api_type=None
        index.search_dim = index._reduced_vectors = index._reduced_matrix = None
        index.__dict__.pop('_folder_tree', None)
        index.roots=[];index.kind_filter=None;index._matrix=None;index._problem=None
        index._catalog=catalog;index._catalog_digest=digest(raw);index._folder_entries=[];index._partial=False
        index.created=None;index.updated=catalog['updated']
