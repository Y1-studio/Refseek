"""Recover the exact application resources from an existing native release.

Python 3.11+ standard library only. No downloads, model changes or library data.
"""
import argparse
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def safe_target(name):
    part = PurePosixPath(name)
    if part.is_absolute() or '..' in part.parts or not name.startswith('assets/'):
        raise ValueError(f'Unexpected resource name: {name}')
    target = (ROOT / Path(*part.parts)).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError(f'Resource leaves this project: {name}')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--from-exe', type=Path, help='A matching SemanticSearch-Native.exe release')
    group.add_argument('--from-directory', type=Path, help='An existing assets folder')
    parser.add_argument('--verify-only', action='store_true', help='Verify source resources without copying them')
    args = parser.parse_args()
    expected = json.loads((ROOT / 'resource-lock.json').read_text(encoding='utf-8'))['files']
    archive = zipfile.ZipFile(args.from_exe) if args.from_exe else None
    try:
        if archive:
            manifest = json.loads(archive.read('bundle-manifest.json'))
            if any(manifest.get(name) != row['sha256'] for name, row in expected.items()):
                raise ValueError('This EXE contains a different resource bundle. Use the matching release.')
        restored = reused = 0
        for name, row in sorted(expected.items()):
            destination = safe_target(name)
            if args.from_directory:
                source = args.from_directory.resolve() / Path(*PurePosixPath(name).parts[1:])
                if source.stat().st_size != row['size'] or digest(source) != row['sha256']:
                    raise ValueError(f'Source resource hash/size mismatch: {name}')
                if args.verify_only:
                    continue
                reader = lambda: source.open('rb')
            else:
                if archive.getinfo(name).file_size != row['size']:
                    raise ValueError(f'Archive resource size mismatch: {name}')
                reader = lambda: archive.open(name)
            if destination.exists() and not args.verify_only:
                if destination.stat().st_size != row['size'] or digest(destination) != row['sha256']:
                    raise ValueError(f'Existing resource differs: {name}. Preserve it elsewhere before retrying.')
                reused += 1
                continue
            if args.verify_only:
                h = hashlib.sha256()
                size = 0
                with reader() as stream:
                    while block := stream.read(1024 * 1024):
                        h.update(block)
                        size += len(block)
                if size != row['size'] or h.hexdigest() != row['sha256']:
                    raise ValueError(f'Archive resource hash/size mismatch: {name}')
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.partial')
            try:
                with reader() as stream, temporary.open('wb') as output:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                if temporary.stat().st_size != row['size'] or digest(temporary) != row['sha256']:
                    raise ValueError(f'Copied resource hash/size mismatch: {name}')
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            restored += 1
        print(json.dumps({'verified_resources': len(expected), 'restored': restored, 'reused': reused,
                          'verify_only': args.verify_only}, indent=2))
    finally:
        if archive:
            archive.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise SystemExit(f'Resource preparation failed: {error}')
