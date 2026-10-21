"""Restore hash-locked Python application resources, never user library data."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'models', 'runtimes', 'style-models', 'research', 'licenses'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_target(name):
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name or parts.parts[0] not in ALLOWED:
        raise ValueError('Unexpected resource name: ' + name)
    target = (ROOT / Path(*parts.parts)).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError('Resource leaves this project: ' + name)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--from-exe', type=Path)
    source.add_argument('--from-directory', type=Path)
    source.add_argument('--verify-local', action='store_true')
    parser.add_argument('--verify-only', action='store_true', help='Check input resources without restoring them')
    args = parser.parse_args()
    records = json.loads((ROOT / 'resource-lock.json').read_text(encoding='utf-8'))['files']
    archive = None
    if args.from_exe:
        try:
            from PyInstaller.archive.readers import CArchiveReader
        except ImportError:
            raise SystemExit('Install requirements-build.txt in the project virtual environment first.')
        archive = CArchiveReader(str(args.from_exe.resolve()))
    archive_names = {name.replace('\\', '/'): name for name in archive.toc} if archive else {}
    restored = reused = 0
    for name, expected in records.items():
        target = checked_target(name)
        source_path = None
        raw = None
        if args.verify_local:
            source_path = target
        elif args.from_directory:
            source_root = args.from_directory.resolve()
            source_path = (source_root / Path(*PurePosixPath(name).parts)).resolve()
            if not source_path.is_relative_to(source_root):
                raise ValueError('Input leaves the source directory: ' + name)
        else:
            member = archive_names.get(name)
            if member is None or archive.toc[member][2] != expected['size']:
                raise ValueError('Resource missing or wrong size in the release: ' + name)
            raw = archive.extract(member)
            if len(raw) != expected['size'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
                raise ValueError('Release resource hash mismatch: ' + name)
        if source_path and (source_path.stat().st_size != expected['size'] or digest(source_path) != expected['sha256']):
            raise ValueError('Local resource hash/size mismatch: ' + name)
        if args.verify_only or args.verify_local:
            continue
        if target.exists():
            if target.stat().st_size != expected['size'] or digest(target) != expected['sha256']:
                raise ValueError('Existing resource differs; preserve it elsewhere before retrying: ' + name)
            reused += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.partial')
        try:
            if raw is None:
                shutil.copyfile(source_path, temporary)
            else:
                temporary.write_bytes(raw)
            if temporary.stat().st_size != expected['size'] or digest(temporary) != expected['sha256']:
                raise ValueError('Copied resource differs: ' + name)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        restored += 1
    print(json.dumps({'verified_resources': len(records), 'restored': restored, 'reused': reused,
                      'verify_only': args.verify_only or args.verify_local}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f'Resource preparation failed: {error}')
