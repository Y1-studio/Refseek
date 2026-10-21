"""Portable desktop entry point. No Python or separate server required in the EXE."""
from __future__ import annotations
import argparse, atexit, json, os, queue, sys, threading, time, traceback, webbrowser
from pathlib import Path
from runtime import Runtime, ROOT, free_port

def main():
    parser = argparse.ArgumentParser(description='Semantic Search v2')
    parser.add_argument('--backend', choices=['auto','cuda','vulkan','cpu'], default='auto')
    parser.add_argument('--device', help='Optional llama.cpp device, e.g. CUDA1 or Vulkan0')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--diagnose', action='store_true', help='Verify real text/image embeddings, write diagnostics, then exit')
    parser.add_argument('--diagnostic-output', type=Path)
    args = parser.parse_args()
    home = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else ROOT
    data = (args.data_dir or home/'SemanticSearch-v2-data').resolve()
    data.mkdir(parents=True, exist_ok=True)
    # Windowed builds have no console; index progress still needs a writable stream.
    stream = (data/'application.log').open('a',encoding='utf-8',buffering=1)
    if sys.stdout is None: sys.stdout = stream
    if sys.stderr is None: sys.stderr = stream
    events = queue.Queue()
    def progress(message):
        print(message, flush=True); events.put(('status',message))
    runtime = Runtime(data,progress)
    atexit.register(runtime.close)
    def diagnostic():
        try:
            runtime.start(args.backend,args.device)
            result = dict(ok=True, **runtime.info)
        except Exception as exc:
            result = dict(ok=False,error=str(exc),attempts=runtime.attempts)
        finally: runtime.close()
        target = args.diagnostic_output or data/'diagnostics.json'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2),flush=True)
        return 0 if result['ok'] else 1
    if args.diagnose: return diagnostic()
    import msvcrt
    instance=(data/'desktop.lock').open('a+b')
    instance.seek(0);instance.write(b'0');instance.flush();instance.seek(0)
    try:msvcrt.locking(instance.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        try:
            session=json.loads((data/'session.json').read_text())
            if not args.no_browser:webbrowser.open(session['url'])
        except (OSError,KeyError,ValueError):pass
        return 0
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk(); root.title('Semantic Search v2'); root.geometry('660x310'); root.minsize(600,300)
    root.configure(bg='#10151c')
    style=ttk.Style(); style.theme_use('clam')
    style.configure('TFrame',background='#10151c');style.configure('TLabel',background='#10151c',foreground='#dce6f4')
    frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Semantic Search v2',font=('Segoe UI',20,'bold')).pack(anchor='w')
    ttk.Label(frame,text='EmbeddingGemma 2 Q8_0 · local search',font=('Segoe UI',11)).pack(anchor='w',pady=(4,16))
    status=tk.StringVar(value='Detecting graphics devices…')
    ttk.Label(frame,textvariable=status,wraplength=600).pack(anchor='w')
    bar=ttk.Progressbar(frame,mode='indeterminate');bar.pack(fill='x',pady=15);bar.start()
    ttk.Label(frame,text='Indexes and logs: '+str(data),wraplength=600).pack(anchor='w',pady=(0,12))
    row=ttk.Frame(frame);row.pack(fill='x')
    state={'server':None,'url':None,'closing':False,'started':False}
    def open_gui():
        if state['url']:webbrowser.open(state['url'])
    open_button=ttk.Button(row,text='Open search',command=open_gui,state='disabled');open_button.pack(side='left')
    def shutdown():
        from egl import webui
        runtime.cancel.set()
        # Cancel and flush complete indexing work before stopping the model.
        webui._JOB['cancel']=True
        # Wait for startup worker to release its server process before extraction cleanup.
        worker.join()
        if state['server']:state['server'].shutdown();state['server'].server_close()
        while webui._JOB.get('running'):time.sleep(.25)
        runtime.close();events.put(('closed',None))
    def close():
        if state['closing']:return
        state['closing']=True;status.set('Stopping; saving any completed indexing work…')
        open_button.configure(state='disabled');threading.Thread(target=shutdown,daemon=True).start()
    ttk.Button(row,text='Stop and exit',command=close).pack(side='right')
    root.protocol('WM_DELETE_WINDOW',close)
    def start():
        try:
            settings,client=runtime.start(args.backend,args.device)
            settings.runtime_info=runtime.info
            from egl.webui import build_server
            try:server,_=build_server(settings,client)
            except OSError:server,_=build_server(settings,client,port=free_port())
            state['server']=server;state['url']='http://127.0.0.1:'+str(server.server_address[1])+'/'
            threading.Thread(target=server.serve_forever,daemon=True).start()
            (data/'session.json').write_text(json.dumps(dict(url=state['url'],**runtime.info),indent=2),encoding='utf-8')
            events.put(('ready',state['url']))
        except Exception as exc:
            traceback.print_exc();runtime.close();events.put(('error',str(exc)))
    worker=threading.Thread(target=start,daemon=True);worker.start()
    def poll():
        try:
            while True:
                kind,message=events.get_nowait()
                if kind=='closed':root.destroy();return
                if state['closing']:continue
                if kind=='status':status.set(message)
                elif kind=='ready':
                    bar.stop();bar.pack_forget();open_button.configure(state='normal')
                    status.set(runtime.info['backend'].upper()+' · '+runtime.info['device_name']+'\n'+message)
                    if not args.no_browser:open_gui()
                    state['started']=True
                elif kind=='error':bar.stop();status.set('Startup failed: '+message)
        except queue.Empty:pass
        if state['started'] and not state['closing'] and runtime.process and runtime.process.poll() is not None:
            status.set('Model server stopped unexpectedly. Exit and restart; see the model log.');state['started']=False
        root.after(200,poll)
    root.after(200,poll);root.mainloop();return 0

if __name__=='__main__':raise SystemExit(main())
