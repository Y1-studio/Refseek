"""Theme shared by the Browser launcher and its account window."""
from tkinter import ttk


BACKGROUND = '#111821'
SURFACE = '#1a2532'
FIELD = '#0e151f'
BORDER = '#36485e'
TEXT = '#edf4fb'
MUTED = '#adbed0'
ACCENT = '#95caff'
SELECTION = '#254f79'
FONT = ('Segoe UI', 10)


def apply_theme(root):
    root.configure(background=BACKGROUND)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=FONT, background=BACKGROUND, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    troughcolor=FIELD, arrowcolor=TEXT, focuscolor=ACCENT)
    style.configure('TLabel', background=BACKGROUND, foreground=TEXT)
    style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('TLabelframe', background=SURFACE, relief='solid', borderwidth=1)
    style.configure('TLabelframe.Label', background=BACKGROUND, foreground=TEXT, font=('Segoe UI', 10, 'bold'))
    style.configure('Panel.TFrame', background=SURFACE)
    style.configure('TButton', background='#26394d', padding=(11, 6), relief='flat')
    style.map('TButton', background=[('pressed', SELECTION), ('active', '#344c65')],
              foreground=[('disabled', '#8295a8')], bordercolor=[('focus', ACCENT)])
    style.configure('Primary.TButton', background=ACCENT, foreground='#0b2035', font=('Segoe UI', 10, 'bold'))
    style.map('Primary.TButton', background=[('disabled', SURFACE), ('pressed', '#75aee8'), ('active', '#b7ddff')],
              foreground=[('disabled', '#8295a8'), ('!disabled', '#0b2035')])
    style.configure('TEntry', fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, padding=6)
    style.map('TEntry', fieldbackground=[('readonly', SURFACE)], bordercolor=[('focus', ACCENT)])
    style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=TEXT, rowheight=28)
    style.map('Treeview', background=[('selected', SELECTION)], foreground=[('selected', TEXT)])
    style.configure('Treeview.Heading', background=SURFACE, padding=(7, 5), font=('Segoe UI', 10, 'bold'))
    style.map('Treeview.Heading', background=[('active', '#26394d')])
    for orientation in ('Horizontal', 'Vertical'):
        name = f'{orientation}.TScrollbar'
        style.configure(name, background=BORDER)
        style.map(name, background=[('active', '#526c86')])
    style.configure('Horizontal.TProgressbar', background=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
