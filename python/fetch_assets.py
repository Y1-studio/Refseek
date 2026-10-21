"""Download exactly pinned official llama.cpp assets and the requested GGUFs."""
from pathlib import Path
import concurrent.futures, hashlib, json, os, shutil, time, urllib.request, zipfile

ROOT=Path(__file__).resolve().parent
TAG='b11741'
REVISION='031f0d4b35536f69ab3509d4893c923264fcf253'
ASSETS={
 'llama-b11741-bin-win-cuda-12.4-x64.zip':('c2c67fe06d7f6aec243e47d42e312a85d2374e602f83ce503bfc1e58ae6eeb3a',267553037,'cuda'),
 'cudart-llama-bin-win-cuda-12.4-x64.zip':('8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6',391443627,'cuda'),
 'llama-b11741-bin-win-vulkan-x64.zip':('1981b9898b68dc6637077d6c618effaa350f5939ee75e9a4d803508708413ab5',32359614,'vulkan'),
 'llama-b11741-bin-win-cpu-x64.zip':('2f86ad2fb6e118aedf3b319c554a6ba14bc6888b2d0ff9135e81fb0201da807e',19708452,'cpu'),
 'embeddinggemma-2-Q8_0.gguf':('6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc',309855520,'model'),
 'mmproj-Q8_0.gguf':('90e7b0238009e2954f856f2081dcf7f35af026b64c765e98f4777053e1754460',554821120,'model')}

def file_sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(8*1024*1024),b''):h.update(chunk)
 return h.hexdigest()

def fetch(name):
 expected,size,backend=ASSETS[name]
 target=ROOT/('models' if backend=='model' else 'downloads')/name
 url=(f'https://huggingface.co/unsloth/embeddinggemma-2-GGUF/resolve/{REVISION}/{name}' if backend=='model'
      else f'https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{name}')
 if not (target.is_file() and target.stat().st_size==size and file_sha(target)==expected):
  temporary=target.with_suffix(target.suffix+'.part')
  for attempt in range(3):
   offset=temporary.stat().st_size if temporary.exists() else 0
   headers={'User-Agent':'SemanticSearch-v2-builder'}
   if offset:headers['Range']=f'bytes={offset}-'
   try:
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=60) as response:
     append=offset and response.status==206
     received=offset if append else 0;last=time.monotonic()
     with temporary.open('ab' if append else 'wb') as stream:
      for chunk in iter(lambda:response.read(4*1024*1024),b''):
       stream.write(chunk);received+=len(chunk)
       if time.monotonic()-last>5:
        print(f'{name}: {received/size:.0%} ({received//1048576} MiB)',flush=True);last=time.monotonic()
    if temporary.stat().st_size!=size or file_sha(temporary)!=expected:
     temporary.unlink(missing_ok=True);raise ValueError('Downloaded file size/hash differs from pinned release metadata: '+name)
    temporary.replace(target);break
   except Exception:
    if attempt==2:raise
    time.sleep(2)
  print(f'Verified {name} ({size//1048576} MiB)',flush=True)
 if backend!='model':
  destination=ROOT/'runtimes'/backend;destination.mkdir(parents=True,exist_ok=True)
  with zipfile.ZipFile(target) as archive:
   for item in archive.infolist():
    if item.is_dir():continue
    # Windows release payloads are self-contained directories; flatten only
    # executable/library/license files needed by llama-server.
    basename=Path(item.filename).name
    if Path(basename).suffix.lower() not in ('.dll','.exe') and 'LICENSE' not in basename.upper():continue
    if Path(basename).suffix.lower()=='.exe' and basename!='llama-server.exe':continue
    output=destination/basename
    raw=archive.read(item)
    if output.exists() and output.read_bytes()!=raw:raise ValueError('Conflicting runtime member: '+basename)
    output.write_bytes(raw)
 return dict(name=name,url=url,sha256=expected,size=size,backend=backend)

def main():
 for directory in ('downloads','models','runtimes','licenses'):(ROOT/directory).mkdir(exist_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:assets=list(pool.map(fetch,ASSETS))
 # App-local MSVC support: no redistributable installer or administrator rights.
 for backend in ('cuda','vulkan','cpu'):
  for name in ('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll'):
   source=Path(os.environ['SystemRoot'])/'System32'/name
   shutil.copy2(source,ROOT/'runtimes'/backend/name)
 manifest=dict(llama_release=TAG,model_revision=REVISION,assets=assets,
               files={str(p.relative_to(ROOT)).replace('\\','/'):dict(sha256=file_sha(p),size=p.stat().st_size)
                      for directory in ['models','runtimes'] for p in (ROOT/directory).rglob('*') if p.is_file()})
 (ROOT/'assets-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 print('All pinned payloads downloaded, extracted and verified.',flush=True)

if __name__=='__main__':main()
