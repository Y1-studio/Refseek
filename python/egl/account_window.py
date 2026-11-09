"""Account and role management for the trusted local Browser launcher operator."""
from collections import defaultdict
from queue import Empty, Queue
import sqlite3
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .accounts import Accounts, validate_guest_folders
from .desktop_ui import BACKGROUND, BORDER, FIELD, FONT, SELECTION, TEXT
from .folder_store import read_folder_tree
from .store import Index


ACCOUNT_ERRORS = (OSError, RuntimeError, ValueError, sqlite3.Error)


def open_account_window(parent, data_dir):
    try:
        accounts = Accounts(data_dir, manage=True)
    except ACCOUNT_ERRORS as error:
        messagebox.showerror('Manage users', str(error), parent=parent)
        return None
    # This entry point is used only by the local launcher, never by HTTP routes.
    return AccountWindow(parent, data_dir, accounts).window


class AccountWindow:
    def __init__(self, parent, data_dir, accounts):
        self.accounts = accounts
        self.data_dir = data_dir
        self.window = tk.Toplevel(parent)
        self.window.title('Refseek Browser · Manage users')
        self.window.configure(background=BACKGROUND)
        self.window.geometry('970x750')
        self.window.minsize(760, 700)
        self.window.transient(parent)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.folder_poll = None
        self.folder_stamp = None
        self.indexed_folders = set()
        self.folder_children = defaultdict(list)
        self.folder_paths = {}
        self.users = []
        self.folders_ready = False
        self.current = None
        self.grants = set()
        self.visible_grants = []

        layout = ttk.Frame(self.window, padding=18)
        layout.pack(fill='both', expand=True)
        layout.columnconfigure(1, weight=1)
        layout.rowconfigure(1, weight=1)
        header = ttk.Frame(layout)
        header.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 15))
        ttk.Label(header, text='Manage users', style='Title.TLabel').pack(anchor='w')
        ttk.Label(header, text='Administrators have full access. Guests use the folders you select.', style='Muted.TLabel').pack(anchor='w')
        users = ttk.LabelFrame(layout, text='Accounts', padding=10)
        users.grid(row=1, column=0, sticky='ns', padx=(0, 14))
        self.user_list = self.listbox(users, width=20)
        self.user_list.bind('<<ListboxSelect>>', self.select_user)
        self.user_list.bind('<Double-Button-1>', self.select_user)
        self.user_list.bind('<Return>', self.select_user)
        self.edit_button = ttk.Button(users, text='Edit selected user', command=self.select_user, state='disabled')
        self.edit_button.pack(fill='x', pady=(8, 4))
        ttk.Button(users, text='New user', command=self.new_user).pack(fill='x', pady=(8, 4))
        self.delete_button = ttk.Button(users, text='Delete user…', command=self.delete_user, state='disabled')
        self.delete_button.pack(fill='x', pady=(0, 4))
        ttk.Button(users, text='Refresh users', command=lambda: self.load_users(self.current)).pack(fill='x')

        editor = ttk.Frame(layout)
        editor.grid(row=1, column=1, sticky='nsew')
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(2, weight=2)
        editor.rowconfigure(3, weight=1)
        form = ttk.Frame(editor)
        form.grid(row=0, column=0, sticky='ew')
        form.columnconfigure(1, weight=1)
        self.username = tk.StringVar(master=self.window)
        self.password = tk.StringVar(master=self.window)
        self.administrator = tk.BooleanVar(master=self.window, value=False)
        self.editing = tk.StringVar(master=self.window, value='New account')
        ttk.Label(form, textvariable=self.editing, style='Muted.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))
        ttk.Label(form, text='Username').grid(row=1, column=0, sticky='w', padx=(0, 10), pady=(0, 6))
        ttk.Label(form, text='Password').grid(row=2, column=0, sticky='w', padx=(0, 10))
        self.username_input = ttk.Entry(form, textvariable=self.username)
        self.username_input.grid(row=1, column=1, sticky='ew', pady=(0, 6))
        ttk.Entry(form, textvariable=self.password, show='*').grid(row=2, column=1, sticky='ew')
        ttk.Checkbutton(form, text='Administrator', variable=self.administrator, command=self.update_role).grid(
            row=3, column=1, sticky='w', pady=(8, 0))
        ttk.Label(form, text='Full access to all folders, indexing, settings, and browser user management.',
                  style='Muted.TLabel', wraplength=440).grid(row=4, column=1, sticky='w', pady=(3, 0))
        ttk.Label(editor, text='Use at least 15 characters. Leave blank to keep the current password.', style='Muted.TLabel', wraplength=440).grid(
            row=1, column=0, sticky='w', pady=(5, 9))

        choices = ttk.LabelFrame(editor, text='Indexed folders', padding=8)
        choices.grid(row=2, column=0, sticky='nsew')
        tree_area = ttk.Frame(choices, style='Panel.TFrame')
        tree_area.pack(fill='both', expand=True)
        self.folders = ttk.Treeview(tree_area, columns=('files',), show='tree headings', selectmode='extended', height=4)
        self.folders.heading('#0', text='Folder', anchor='w')
        self.folders.heading('files', text='Files', anchor='e')
        self.folders.column('#0', width=400)
        self.folders.column('files', width=65, stretch=False, anchor='e')
        scroll = ttk.Scrollbar(tree_area, orient='vertical', command=self.folders.yview)
        self.folders.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.folders.pack(side='left', fill='both', expand=True)
        self.folders.bind('<<TreeviewOpen>>', self.expand_folder)
        tools = ttk.Frame(choices, style='Panel.TFrame')
        tools.pack(side='bottom', fill='x', before=tree_area, pady=(6, 0))
        self.allow_button = ttk.Button(tools, text='Allow selected folders', command=self.allow_folders)
        self.allow_button.pack(side='left')
        self.refresh_button = ttk.Button(tools, text='Refresh folders', command=self.load_folders)
        self.refresh_button.pack(side='right')

        allowed = ttk.LabelFrame(editor, text='Allowed folders and their subfolders', padding=8)
        allowed.grid(row=3, column=0, sticky='nsew', pady=(8, 0))
        self.allowed = self.listbox(allowed, height=3, selectmode=tk.EXTENDED)
        grant_tools = ttk.Frame(allowed, style='Panel.TFrame')
        grant_tools.pack(side='bottom', fill='x', before=self.allowed.master, pady=(6, 0))
        self.remove_button = ttk.Button(grant_tools, text='Remove selected access', command=self.remove_folders)
        self.remove_button.pack(side='left')
        self.clear_button = ttk.Button(grant_tools, text='Clear folder access', command=self.clear_folders)
        self.clear_button.pack(side='right')
        self.access_summary = tk.StringVar(master=self.window)
        ttk.Label(allowed, textvariable=self.access_summary, style='Muted.TLabel', wraplength=440).pack(
            side='bottom', anchor='w', before=self.allowed.master, pady=(0, 4))

        footer = ttk.Frame(layout)
        footer.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(13, 0))
        self.status = tk.StringVar(master=self.window)
        ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', wraplength=410).pack(side='left', fill='x', expand=True)
        ttk.Button(footer, text='Close', command=self.close).pack(side='right')
        self.save_button = ttk.Button(footer, text='Create user', command=self.save_user, state='disabled', style='Primary.TButton')
        self.save_button.pack(side='right', padx=(0, 8))
        self.load_users()
        self.load_folders()

    @staticmethod
    def listbox(parent, **options):
        frame = ttk.Frame(parent, style='Panel.TFrame')
        frame.pack(fill='both', expand=True)
        items = tk.Listbox(frame, exportselection=False, background=FIELD, foreground=TEXT,
                           selectbackground=SELECTION, selectforeground=TEXT, font=FONT,
                           highlightbackground=BORDER, highlightcolor=SELECTION, highlightthickness=1,
                           borderwidth=0, relief='flat', **options)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=items.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=items.xview)
        items.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        items.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        return items

    def close(self):
        if self.folder_poll is not None:
            self.window.after_cancel(self.folder_poll)
        self.window.destroy()

    def load_users(self, selected=None):
        try:
            self.users = self.accounts.users()
        except ACCOUNT_ERRORS as error:
            self.status.set(str(error))
            return
        self.user_list.delete(0, tk.END)
        for i, user in enumerate(self.users):
            self.user_list.insert(tk.END, user['username'] + (' · Admin' if user['admin'] else ' · Guest'))
            if user['username'] == selected:
                self.user_list.selection_set(i)
                self.user_list.see(i)
        if self.user_list.curselection():
            self.select_user()
        else:
            self.new_user()

    def select_user(self, _event=None):
        if self.user_list.curselection():
            user = self.users[self.user_list.curselection()[0]]
            self.edit_user(user['username'], user['folders'], user['admin'])

    def new_user(self):
        self.user_list.selection_clear(0, tk.END)
        self.edit_user(None, [])
        self.username_input.focus_set()

    def edit_user(self, name, folders, admin=False):
        self.current = name
        self.editing.set(f'Editing: {name}' if name else 'New account')
        self.username.set(name or '')
        self.password.set('')
        self.administrator.set(admin)
        self.grants = set(folders)
        self.username_input.configure(state='readonly' if name else 'normal')
        self.delete_button.configure(state='normal' if name else 'disabled')
        self.edit_button.configure(state='normal' if name else 'disabled')
        self.save_button.configure(text='Save changes' if name else 'Create user')
        self.render_grants()
        self.update_role()

    def update_role(self):
        admin = self.administrator.get()
        state = 'disabled' if admin else 'normal'
        self.allow_button.configure(state=state)
        self.remove_button.configure(state=state)
        self.clear_button.configure(state=state)
        self.folders.state(['disabled'] if admin else ['!disabled'])
        self.allowed.configure(state=state)
        self.save_button.configure(state='normal' if admin or self.folders_ready else 'disabled')
        self.update_access_summary()

    def update_access_summary(self):
        if self.administrator.get():
            text = 'Administrator: full access to all indexed folders.'
        elif self.grants:
            text = f'{len(self.grants)} allowed folder(s), including their subfolders. Click Save changes to apply edits.'
        else:
            text = 'No folders: this guest cannot search or open library files.'
        self.access_summary.set(text)

    def load_folders(self):
        self.refresh_button.configure(state='disabled')
        self.folders_ready = False
        self.update_role()
        self.folder_stamp = None
        self.status.set('Reading indexed folders…')
        result = Queue()

        def read():
            try:
                stamp = Index.stamp(self.data_dir)
                rows = (read_folder_tree(self.data_dir) if stamp and stamp[0] == 'catalog.json'
                        else Index.load(self.data_dir).folder_tree)
                result.put((stamp, rows, None))
            except ACCOUNT_ERRORS as error:
                result.put((None, [], error))

        def poll():
            try:
                self.folder_stamp, rows, error = result.get_nowait()
            except Empty:
                self.folder_poll = self.window.after(80, poll)
                return
            self.folder_poll = None
            self.refresh_button.configure(state='normal')
            self.folders.delete(*self.folders.get_children())
            self.folder_paths.clear()
            self.folder_children.clear()
            self.indexed_folders = {row['path'] for row in rows}
            for row in rows:
                self.folder_children[row['parent']].append(row)
            self.insert_folders('', None)
            self.render_grants()
            self.folders_ready = error is None
            self.update_role()
            self.status.set(str(error) if error else ('Add or remove folders, then save the user.' if rows else 'No indexed folders. You can save this account with no folder access.'))

        threading.Thread(target=read, daemon=True).start()
        self.folder_poll = self.window.after(80, poll)

    def insert_folders(self, item, path):
        for row in self.folder_children.get(path, []):
            folder = row['path']
            child = self.folders.insert(item, tk.END, text=row['label'] if item else folder, values=(row['files'],))
            self.folder_paths[child] = folder
            if self.folder_children.get(folder):
                self.folders.insert(child, tk.END)

    def expand_folder(self, _event=None):
        item = self.folders.focus()
        children = self.folders.get_children(item)
        if len(children) == 1 and children[0] not in self.folder_paths:
            self.folders.delete(children[0])
            self.insert_folders(item, self.folder_paths[item])

    def render_grants(self):
        self.visible_grants = sorted(self.grants, key=str.casefold)
        self.allowed.configure(state='normal')
        self.allowed.delete(0, tk.END)
        for path in self.visible_grants:
            suffix = '' if path in self.indexed_folders else ' (unavailable in current index)'
            self.allowed.insert(tk.END, path + suffix)
        self.allowed.configure(state='disabled' if self.administrator.get() else 'normal')
        self.update_access_summary()

    def allow_folders(self):
        self.grants.update(self.folder_paths[item] for item in self.folders.selection() if item in self.folder_paths)
        self.render_grants()

    def remove_folders(self):
        self.grants.difference_update(self.visible_grants[i] for i in self.allowed.curselection())
        self.render_grants()

    def clear_folders(self):
        self.grants.clear()
        self.render_grants()

    def save_user(self):
        try:
            admin = self.administrator.get()
            if not admin and (not self.folders_ready or self.folder_stamp != Index.stamp(self.data_dir)):
                raise ValueError('The index has changed. Click Refresh folders before saving.')
            folders = sorted(self.grants) if admin else validate_guest_folders(list(self.grants), self.indexed_folders)
            username = self.username.get().strip()
            self.accounts.save_user(username, self.password.get(), folders, admin=admin, create=self.current is None)
        except ACCOUNT_ERRORS as error:
            self.status.set(str(error))
            return
        self.load_users(username.lower())
        self.status.set('User saved. Changes to role, access, or password sign the user out.')

    def delete_user(self):
        username = self.current
        if not username or not messagebox.askyesno('Delete user', f'Delete {username} and revoke their access?', parent=self.window):
            return
        try:
            self.accounts.delete_user(username)
        except ACCOUNT_ERRORS as error:
            self.status.set(str(error))
            return
        self.load_users()
        self.status.set('User deleted and access revoked.')
