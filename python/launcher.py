"""Portable desktop entry point. No Python or separate server required in the EXE."""
from __future__ import annotations
import argparse, atexit, json, os, queue, sys, threading, time, traceback, webbrowser
from pathlib import Path
from runtime import Runtime, ROOT, free_port


def close_opening_screen():
    try:
        import pyi_splash
        pyi_splash.close()
    except (ImportError, OSError):
        pass


def main():
    from egl import __version__
    from egl.portable import prepare_browser_data
    parser = argparse.ArgumentParser(description=f'Refseek Browser {__version__}')
    parser.add_argument('--backend', choices=['auto','cuda','vulkan','cpu'], default='auto')
    parser.add_argument('--device', help='Optional llama.cpp device, e.g. CUDA1 or Vulkan0')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--start', action='store_true', help='Start immediately without waiting for the Start button')
    parser.add_argument('--diagnose', action='store_true', help='Verify real text/image embeddings, write diagnostics, then exit')
    parser.add_argument('--diagnostic-output', type=Path)
    args = parser.parse_args()
    home = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else ROOT
    try:
        data = args.data_dir.expanduser().absolute() if args.data_dir else prepare_browser_data(home)
    except (OSError, ValueError, RuntimeError) as error:
        close_opening_screen()
        if args.diagnose:
            output = args.diagnostic_output or home/'data'/'diagnostics.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({'ok': False, 'error': str(error)}, indent=2), encoding='utf-8')
        else:
            from tkinter import messagebox
            messagebox.showerror('Refseek Browser', str(error))
        return 1
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
    if args.diagnose:
        close_opening_screen()
        return diagnostic()
    import msvcrt
    instance=(data/'desktop.lock').open('a+b')
    instance.seek(0);instance.write(b'0');instance.flush();instance.seek(0)
    try:msvcrt.locking(instance.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        close_opening_screen()
        try:
            session=json.loads((data/'session.json').read_text())
            if not args.no_browser:webbrowser.open(session['url'])
        except (OSError,KeyError,ValueError):pass
        return 0
    (data/'session.json').unlink(missing_ok=True)
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    import sqlite3
    from egl.accounts import Accounts, TUNNEL_HOST, account_mode, read_env, set_account_mode, set_tunnel_host
    from egl.portable import env_path
    from egl.desktop_ui import apply_theme
    if os.name == 'nt':
        import ctypes
        app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        app_id.argtypes = [ctypes.c_wchar_p]
        app_id.restype = ctypes.c_long
        app_id('Y1-studio.Refseek.Browser')
    root = tk.Tk(); root.title('Refseek Browser'); root.geometry('680x365'); root.minsize(620,355)
    apply_theme(root)
    frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Refseek Browser',style='Title.TLabel').pack(anchor='w')
    ttk.Label(frame,text=f'Version {__version__} · Local search',style='Muted.TLabel').pack(anchor='w',pady=(4,16))
    status=tk.StringVar(value='Choose your settings, then click Start.')
    ttk.Label(frame,textvariable=status,wraplength=600).pack(anchor='w')
    bar=ttk.Progressbar(frame,mode='indeterminate')
    data_label=ttk.Label(frame,text='Indexes and logs: '+str(data),wraplength=565,style='Muted.TLabel')
    data_label.pack(anchor='w',pady=(12,12))
    row=ttk.Frame(frame);row.pack(fill='x')
    state={'server':None,'url':None,'closing':False,'started':False,'starting':False,
           'active_mode':None,'active_tunnel':''}
    worker = None
    def open_gui():
        if state['url']:webbrowser.open(state['url'])
    start_button=ttk.Button(row,text='Start',style='Primary.TButton')
    start_button.pack(side='left')
    open_button=ttk.Button(row,text='Open search',command=open_gui,state='disabled')
    open_button.pack(side='left',padx=(8,0))
    accounts_window = None
    def manage_accounts():
        from egl.account_window import open_account_window

        nonlocal accounts_window
        if accounts_window is not None and accounts_window.winfo_exists():
            accounts_window.lift()
            accounts_window.focus_set()
        else:
            accounts_window = open_account_window(root, data)

    def setup_accounts():
        username = simpledialog.askstring('Set up accounts', 'Owner username', initialvalue='owner', parent=root)
        if username is None:
            return False
        password = simpledialog.askstring('Set up accounts', 'Owner password (at least 15 characters)', show='*', parent=root)
        if password is None:
            return False
        if password != simpledialog.askstring('Set up accounts', 'Confirm owner password', show='*', parent=root):
            messagebox.showerror('Set up accounts', 'Passwords do not match.', parent=root)
            return False
        try:
            Accounts.setup(data, username, password)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            messagebox.showerror('Set up accounts', str(error), parent=root)
            return False
        return True

    def refresh_access():
        protected = account_mode(data)
        tunnel = read_env(env_path(data)).get(TUNNEL_HOST, '')
        pending = state['active_mode'] is not None and (
            protected != state['active_mode'] or tunnel != state['active_tunnel'])
        label = 'Account mode · Sign-in required' if protected else 'Normal mode · Full access on this computer'
        access_status.set(label + (' (restart to apply)' if pending and state['started'] else ''))
        mode_button.configure(text='Use Normal mode' if protected else 'Use Account mode…')
        mode_button.configure(state='disabled' if state['starting'] else 'normal')
        tunnel_button.configure(state='normal' if protected and not state['starting'] else 'disabled')
        account_button.configure(state='disabled' if state['starting'] else 'normal')
        if (data/'accounts.sqlite3').is_file() and not account_button.winfo_manager():
            account_button.pack(side='left', padx=(8, 0))

    def change_access():
        try:
            enable = not account_mode(data)
            if enable and not (data/'accounts.sqlite3').is_file():
                if not setup_accounts():
                    return
            else:
                set_account_mode(data, enable)
            refresh_access()
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            messagebox.showerror('Access mode', str(error), parent=root)

    def configure_tunnel():
        try:
            current = read_env(env_path(data)).get(TUNNEL_HOST, '')
            address = simpledialog.askstring('Cloudflare tunnel',
                'Paste your public HTTPS tunnel address. Leave blank to turn off tunnel access.',
                initialvalue=f'https://{current}' if current else '', parent=root)
            if address is None:
                return
            set_tunnel_host(data, address)
            refresh_access()
            instruction = 'Restart Refseek to apply the tunnel address.' if state['started'] else 'Saved. Click Start to use this tunnel address.'
            messagebox.showinfo('Cloudflare tunnel', instruction, parent=root)
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror('Cloudflare tunnel', str(error), parent=root)

    access_row = ttk.Frame(frame)
    access_row.pack(fill='x', pady=(12, 0))
    access_status = tk.StringVar()
    ttk.Label(access_row, textvariable=access_status, style='Muted.TLabel').pack(anchor='w', pady=(0, 5))
    mode_button = ttk.Button(access_row, command=change_access)
    mode_button.pack(side='left')
    tunnel_button = ttk.Button(access_row, text='Cloudflare tunnel…', command=configure_tunnel)
    tunnel_button.pack(side='right')
    account_button = ttk.Button(access_row, text='Manage users…', command=manage_accounts)
    refresh_access()
    def shutdown():
        from egl import webui
        runtime.cancel.set()
        # Cancel and flush complete indexing work before stopping the model.
        webui._JOB['cancel']=True
        # Wait for startup worker to release its server process before extraction cleanup.
        if worker is not None:
            worker.join()
        if state['server']:state['server'].shutdown();state['server'].server_close()
        while webui._JOB.get('running'):time.sleep(.25)
        runtime.close();events.put(('closed',None))
    def close():
        if state['closing']:return
        if worker is None:
            root.destroy()
            return
        state['closing']=True;status.set('Stopping; saving any completed indexing work…')
        for button in (start_button, open_button, mode_button, account_button, tunnel_button):
            button.configure(state='disabled')
        threading.Thread(target=shutdown,daemon=True).start()
    close_button=ttk.Button(row,text='Close',command=close)
    close_button.pack(side='right')
    root.protocol('WM_DELETE_WINDOW',close)
    def start():
        server = None
        server_thread = None
        try:
            from egl.store import _atomic_write

            settings,client=runtime.start(args.backend,args.device)
            settings.runtime_info=runtime.info
            from egl.webui import build_server
            try:server,_=build_server(settings,client,accounts_mode=state['active_mode'])
            except OSError:server,_=build_server(settings,client,port=free_port(),accounts_mode=state['active_mode'])
            state['server']=server;state['url']=server.access_url
            server_thread=threading.Thread(target=server.serve_forever,daemon=True)
            server_thread.start()
            session = json.dumps(dict(url=state['url'],**runtime.info),indent=2).encode('utf-8')
            _atomic_write(data/'session.json',session)
            events.put(('ready',state['url']))
        except Exception as exc:
            traceback.print_exc()
            if server is not None:
                if server_thread is not None and server_thread.is_alive():
                    server.shutdown()
                server.server_close()
            state['server'] = None
            state['url'] = None
            try:
                (data/'session.json').unlink(missing_ok=True)
            except OSError:
                traceback.print_exc()
            runtime.close()
            events.put(('error',str(exc)))

    def begin_start():
        nonlocal worker
        if state['starting'] or state['started'] or state['closing']:
            return
        try:
            state['active_mode'] = account_mode(data)
            state['active_tunnel'] = read_env(env_path(data)).get(TUNNEL_HOST, '')
        except (OSError, ValueError) as error:
            status.set(str(error))
            return
        if worker is not None:
            worker.join()
        runtime.cancel.clear()
        runtime.attempts.clear()
        runtime.info.clear()
        state['starting'] = True
        start_button.configure(text='Starting…',state='disabled')
        close_button.configure(text='Stop and exit')
        refresh_access()
        status.set('Starting the search engine…')
        bar.pack(fill='x',pady=(12,0),before=data_label)
        bar.start()
        worker=threading.Thread(target=start,daemon=True)
        worker.start()

    start_button.configure(command=begin_start)
    root.update_idletasks()
    close_opening_screen()
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
                    state['starting']=False
                    state['started']=True
                    start_button.configure(text='Running')
                    refresh_access()
                elif kind=='error':
                    bar.stop()
                    bar.pack_forget()
                    state['starting']=False
                    start_button.configure(text='Start',state='normal')
                    refresh_access()
                    status.set('Startup failed: '+message)
        except queue.Empty:pass
        if state['started'] and not state['closing'] and runtime.process and runtime.process.poll() is not None:
            status.set('Model server stopped unexpectedly. Exit and restart; see the model log.');state['started']=False
        root.after(200,poll)
    root.after(200,poll)
    if args.start:
        root.after_idle(begin_start)
    root.mainloop()
    return 0

if __name__=='__main__':raise SystemExit(main())
