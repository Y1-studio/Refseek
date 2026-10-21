"""Build-time only: append a verified resource ZIP to the native Rust PE.

The shipped application never invokes Python. Do not package library data,
developer caches, model logs or any files outside this edition.
"""
from pathlib import Path
import hashlib, json, shutil, zipfile, tomllib

ROOT = Path(__file__).resolve().parents[1]
def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while b := f.read(1024 * 1024): h.update(b)
    return h.hexdigest()

def main():
    licenses = ROOT / 'assets/licenses/rust'
    licenses.mkdir(parents=True, exist_ok=True)
    registry = ROOT / '.cargo/registry/src'
    lock = tomllib.loads((ROOT / 'Cargo.lock').read_text())
    notices = []
    for package in lock['package']:
        if 'source' not in package: continue
        name = f"{package['name']}-{package['version']}"
        candidates = list(registry.glob(f'*/{name}'))
        # Cargo locks other operating systems too; those crates are not linked
        # into this Windows binary and need not have been downloaded.
        if not candidates: continue
        src = candidates[0]
        meta = tomllib.loads((src / 'Cargo.toml').read_text())['package']
        dest = licenses / name
        dest.mkdir(exist_ok=True)
        for file in src.iterdir():
            if file.is_file() and file.name.upper().startswith(('LICENSE','LICENCE','COPYING','NOTICE','UNLICENSE')):
                shutil.copy2(file, dest / file.name)
        notices.append({'crate': name, 'license': meta.get('license'), 'repository': meta.get('repository')})
    (licenses / 'packages.json').write_text(json.dumps(notices, indent=2), encoding='utf-8')
    files = sorted(p for p in (ROOT / 'assets').rglob('*') if p.is_file())
    manifest = {p.relative_to(ROOT).as_posix(): digest(p) for p in files}
    expected = json.loads((ROOT / 'assets/assets-manifest.json').read_text())['files']
    for name, record in expected.items():
        if manifest.get('assets/' + name) != record['sha256']:
            raise RuntimeError(f'Pinned asset hash mismatch: {name}')
    binary = ROOT / 'target/x86_64-pc-windows-gnu/release/semantic-search-native.exe'
    output = ROOT / 'Refseek-Desktop.exe'
    temp = ROOT / 'Refseek-Desktop.building.exe'
    shutil.copyfile(binary, temp)
    with zipfile.ZipFile(temp, 'a', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True, strict_timestamps=False) as z:
        z.writestr('bundle-manifest.json', json.dumps(manifest, sort_keys=True))
        for p in files: z.write(p, p.relative_to(ROOT).as_posix())
    temp.replace(output)
    result = {'executable': output.name, 'size': output.stat().st_size, 'sha256': digest(output), 'rust_binary_sha256': digest(binary), 'resource_files': len(files), 'runtime': 'native Rust GUI plus privately owned llama.cpp; no Python/browser runtime'}
    (ROOT / 'build-manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))

if __name__ == '__main__': main()
