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
def library_lock(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    # OS locks release on process death; a leftover lock file is harmless.
    with (root/'.library.lock').open('a+b') as stream:
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


def read_catalog(root):
    from .store import CorruptIndexError, _read_retry
    path=Path(root)/'catalog.json'
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


def load(index,roots=None):
    from .store import CorruptIndexError, _read_retry
    with library_lock(index.data_dir):
        catalog,raw=read_catalog(index.data_dir)
        for attr,key in [('dim','dim'),('model','model'),('api_type','api_type'),('roots','roots'),
                         ('kind_filter','kinds_filter'),('created','created'),('updated','updated')]:
            setattr(index,attr,catalog.get(key))
        index.roots=index.roots or []
        index._catalog_digest=digest(raw);index._catalog=catalog
        index._partial=bool(roots);index._folder_entries=catalog['folders']
        records=[];vectors=[];positions=[];all_positions=[];ids=set()
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
    if getattr(index,'_partial',False):raise ValueError('A folder-filtered search view cannot be saved')
    if index._problem:raise CorruptIndexError('Refusing to migrate/save an incomplete index: '+index._problem)
    if len(index.records)!=len(index.vectors):raise CorruptIndexError('Record/vector counts differ before saving')
    dim=index.dim or 0
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
            for i in positions:values.extend(index.vectors[i])
            if sys.byteorder=='big':values.byteswap()
            vec_raw=values.tobytes();vec_hash=digest(vec_raw)
            content=dict(version=1,root=folder,roots=[folder] if folder else [],model=index.model,api_type=index.api_type,dim=index.dim,records=rows,count=len(rows))
            fingerprint=digest(encoded(content)+vec_hash.encode())
            old=previous.get(folder)
            if old and old['fingerprint']==fingerprint:
                entry=dict(old,positions=positions)
            else:
                ident=folder_id(folder);snapshot=f'folders/{ident}/{fingerprint[:24]}'
                destination=snapshot_path(root,snapshot);destination.mkdir(parents=True,exist_ok=True)
                meta=dict(content,created=old.get('created') if old else index.created,updated=index.updated)
                meta_raw=encoded(meta)
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
                    _atomic_write(destination/'index.json',meta_raw)
                if digest((destination/'vectors.f32').read_bytes())!=vec_hash or digest((destination/'index.json').read_bytes())!=digest(meta_raw):
                    raise CorruptIndexError('Folder snapshot failed verification after save')
                entry=dict(id=ident,root=folder,snapshot=snapshot,count=len(rows),files=len({r['path'] for r in rows}),
                           positions=positions,index_sha256=digest(meta_raw),vectors_sha256=vec_hash,
                           fingerprint=fingerprint,created=meta.get('created'),updated=meta.get('updated'),size_bytes=len(vec_raw))
                written+=1
            entries.append(entry)
        catalog=dict(format='folder-index-v1',version=2,model=index.model,api_type=index.api_type,dim=index.dim,
                     roots=index.roots,kinds_filter=index.kind_filter,created=index.created,updated=index.updated,
                     count=len(index.records),folders=entries)
        raw=encoded(catalog)
        if old_raw is not None:_atomic_write(root/'catalog.json.bak',old_raw)
        _atomic_write(root/'catalog.json',raw)
        index._catalog_digest=digest(raw);index._catalog=catalog;index._folder_entries=entries
        index._matrix=None;index.last_save=dict(folders_written=written,folders_unchanged=len(entries)-written)
        # Readers hold the same lock while loading complete snapshots. Only
        # snapshots referenced by neither current nor previous catalog are pruned.
        retain={e['snapshot'] for e in entries}|{e['snapshot'] for e in current['folders']}
        prune(root,retain)


def prune(root,retain):
    base=(Path(root)/'folders').resolve()
    if not base.exists():return
    for directory in base.iterdir():
        if not directory.is_dir() or directory.is_symlink():continue
        for revision in directory.iterdir():
            if not revision.is_dir() or revision.is_symlink():continue
            resolved=revision.resolve()
            if not resolved.is_relative_to(base):continue
            if revision.relative_to(Path(root).resolve()).as_posix() in retain:continue
            try:
                for name in ['index.json','vectors.f32']:
                    path=resolved/name
                    if path.is_file():path.unlink()
                resolved.rmdir()
            except OSError:pass  # A held file/antivirus must not fail a good commit.
        try:directory.rmdir()
        except OSError:pass


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
        if path.exists():_atomic_write(root/'catalog.json.bak',path.read_bytes())
        catalog=dict(format='folder-index-v1',version=2,model=None,api_type=None,dim=None,roots=[],
                     kinds_filter=None,created=None,updated=time.time(),count=0,folders=[])
        raw=encoded(catalog);_atomic_write(path,raw)
        index.records=[];index.vectors=[];index.dim=None;index.model=None;index.api_type=None
        index.roots=[];index.kind_filter=None;index._matrix=None;index._problem=None
        index._catalog=catalog;index._catalog_digest=digest(raw);index._folder_entries=[];index._partial=False
        index.created=None;index.updated=catalog['updated']
