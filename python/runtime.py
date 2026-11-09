"""Own and verify a private, loopback-only llama.cpp embedding server."""
from __future__ import annotations
import hashlib, json, os, re, socket, subprocess, sys, threading, time, urllib.request
from pathlib import Path
import numpy as np

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

def child_job():
    """Windows closes this handle on a crash too, terminating our server children."""
    if os.name != 'nt':return None
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                  ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',ctypes.c_uint64*6),('process_memory',ctypes.c_size_t),
                  ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    handle=k.CreateJobObjectW(None,None)
    info=Extended();info.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(),'Cannot create model process lifetime guard')
    return k,handle

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

class Runtime:
    def __init__(self, data, progress=lambda text: None):
        self.data = Path(data); self.data.mkdir(parents=True, exist_ok=True)
        self.progress = progress; self.process = None; self.log = None
        self.cancel = threading.Event(); self.attempts = []; self.info = {}
        self.job = child_job()

    def devices(self, backend):
        exe = ROOT/'runtimes'/backend/'llama-server.exe'
        try:
            self.progress('Checking '+backend.upper()+' devices…')
            with subprocess.Popen([str(exe), '--list-devices'], cwd=exe.parent, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors='replace', creationflags=HIDDEN) as probe:
                deadline = time.monotonic() + 120
                while True:
                    try:
                        output, _ = probe.communicate(timeout=.25)
                        break
                    except subprocess.TimeoutExpired:
                        if self.cancel.is_set() or time.monotonic() >= deadline:
                            probe.kill()
                            probe.communicate()
                            if self.cancel.is_set():
                                raise RuntimeError('Startup cancelled')
                            raise OSError('Device enumeration timed out')
            prefix = 'CUDA' if backend == 'cuda' else 'Vulkan'
            devices=re.findall(r'^\s*('+prefix+r'\d+): (.+)$', output, re.M)
            usable = [(dev,name) for dev,name in devices if not any(
                software in name.lower() for software in ('llvmpipe','lavapipe','swiftshader','software renderer'))]
            if not usable:
                self.attempts.append(dict(backend=backend, stage='enumeration', error='No usable GPU reported'))
            return usable
        except OSError as exc:
            self.attempts.append(dict(backend=backend, stage='enumeration', error=str(exc)))
            return []

    def candidates(self, backend, device):
        for kind in ('cuda', 'vulkan'):
            if backend in ('auto', kind):
                for identifier, name in self.devices(kind):
                    if device is None or identifier == device:
                        yield kind, identifier, name
        if backend in ('auto', 'cpu'):
            yield 'cpu', 'none', 'CPU'

    def start(self, backend='auto', device=None):
        from egl.api import EmbedClient, image_item
        from egl.config import Settings
        from egl.store import _atomic_write

        def record_attempts():
            try:
                _atomic_write(self.data/'backend-attempts.json', json.dumps(self.attempts, indent=2).encode('utf-8'))
            except OSError:
                pass
        self.progress('Verifying bundled Q8 model and projector…')
        manifest=json.loads((ROOT/'assets-manifest.json').read_text(encoding='utf-8'))
        for asset in manifest['assets']:
            if asset['backend']!='model':continue
            path=ROOT/'models'/asset['name'];digest=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(8*1024*1024),b''):
                    if self.cancel.is_set():raise RuntimeError('Startup cancelled')
                    digest.update(block)
            if path.stat().st_size!=asset['size'] or digest.hexdigest()!=asset['sha256']:
                raise RuntimeError('Bundled model integrity check failed: '+asset['name'])
        for kind, dev, name in self.candidates(backend, device):
            if self.cancel.is_set(): raise RuntimeError('Startup cancelled')
            self.progress('Starting '+kind.upper()+': '+name)
            port = free_port(); exe = ROOT/'runtimes'/kind/'llama-server.exe'
            command = [str(exe), '-m', str(ROOT/'models'/'embeddinggemma-2-Q8_0.gguf'),
                '--mmproj', str(ROOT/'models'/'mmproj-Q8_0.gguf'), '--embeddings',
                '--pooling', 'mean', '--alias', 'embeddinggemma-2-q8', '--host', '127.0.0.1',
                '--port', str(port), '-np', '2', '-c', '8192', '-b', '8192', '-ub', '8192',
                '-fa', 'auto', '--device', dev, '-ngl', '0' if kind == 'cpu' else 'all']
            command += ['--no-mmproj-offload'] if kind == 'cpu' else ['--mmproj-device', dev]
            logpath = self.data/('llama-'+kind+'-'+dev+'.log')
            try:
                self.log = logpath.open('w', encoding='utf-8')
                self.process = subprocess.Popen(command, cwd=exe.parent, stdout=self.log,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=HIDDEN)
                if self.job and not self.job[0].AssignProcessToJobObject(self.job[1],int(self.process._handle)):
                    raise RuntimeError('Could not attach model server to its lifetime guard')
                url = 'http://127.0.0.1:'+str(port)
                deadline = time.monotonic() + (180 if kind == 'cpu' else 600)
                while True:
                    if self.cancel.is_set(): raise RuntimeError('Startup cancelled')
                    if self.process.poll() is not None: raise RuntimeError('Server exited: '+str(self.process.returncode))
                    try:
                        with urllib.request.urlopen(url+'/health', timeout=2) as r:
                            if r.status == 200: break
                    except Exception: pass
                    if time.monotonic() > deadline: raise RuntimeError('Server startup timed out')
                    self.cancel.wait(.3)
                settings = Settings(api_url=url+'/v1', api_type='openai', model='embeddinggemma-2-q8',
                                    data_dir=str(self.data), web_port=8088, timeout=90)
                client = EmbedClient(settings)
                probes = json.loads((ROOT/'style-models'/'model-probes.json').read_text())
                with client.cancel_scope(self.cancel.is_set):
                    vectors = [client.embed_one(probes['text']), client.embed_one(image_item(
                        (ROOT/'style-models'/'probe-image.bin').read_bytes(), probes['mime']))]
                scores = {}
                self.probe_vectors = dict(zip(('text', 'image'), vectors))
                for label, vec in zip(('text', 'image'), vectors):
                    vec = np.asarray(vec)
                    if vec.shape != (768,) or not np.isfinite(vec).all() or np.linalg.norm(vec) < .01:
                        raise RuntimeError('Invalid '+label+' embedding')
                    expected = np.load(ROOT/'style-models'/('probe-'+label+'.npy'), allow_pickle=False)
                    scores[label] = float(vec@expected/(np.linalg.norm(vec)*np.linalg.norm(expected)))
                    if not np.isfinite(scores[label]) or scores[label] < .9995:
                        raise RuntimeError(f'Bundled model compatibility check failed for {label}: '
                                           f'cosine {scores[label]:.6f}, requires 0.9995')
                self.info = dict(backend=kind, device=dev, device_name=name, llama_release='b11741',
                                 model='EmbeddingGemma 2 Q8_0', api_url=url+'/v1',
                                 bf16_probe_cosines=scores, attempts=self.attempts, log=str(logpath))
                self.info['fallback'] = backend == 'auto' and kind == 'cpu'
                record_attempts()
                settings.bundled_q8 = True
                settings.runtime_info = self.info
                # Readiness probes have a short deadline; real image batches
                # retain the original GUI's longer inference timeout.
                settings.timeout = 900
                self.progress('Ready: '+kind.upper()+' / '+name)
                return settings, client
            except Exception as exc:
                if self.cancel.is_set():
                    self.stop_process()
                    record_attempts()
                    raise RuntimeError('Startup cancelled') from exc
                self.attempts.append(dict(backend=kind, device=dev, error=str(exc), log=str(logpath)))
                record_attempts()
                self.stop_process()
                self.progress(kind.upper()+' failed; trying the next device/backend.')
        record_attempts()
        raise RuntimeError('All embedding backends failed. See logs in '+str(self.data)+'\n'+json.dumps(self.attempts))

    def stop_process(self):
        p = self.process
        if p is not None and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=10)
        self.process = None
        if self.log: self.log.close(); self.log = None

    def close(self):
        self.cancel.set(); self.stop_process()
