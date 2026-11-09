"""Local account storage and revocable browser sessions."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from .portable import env_path


COOKIE = 'refseek_session'
SESSION_SECONDS = 12 * 3600
KEY_NAME = 'REFSEEK_SESSION_KEY'
ACCESS_MODE = 'REFSEEK_ACCESS_MODE'
TUNNEL_HOST = 'REFSEEK_TUNNEL_HOST'
USER_NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}')


def read_env(path):
    try:
        return dict(line.strip().split('=', 1) for line in path.read_text(encoding='utf-8-sig').splitlines()
                    if '=' in line and not line.lstrip().startswith('#'))
    except FileNotFoundError:
        return {}


def account_mode(data_dir):
    return read_env(env_path(data_dir)).get(ACCESS_MODE, '').strip().lower() == 'accounts'


def _write_env_value(path, key, value):
    from .store import _atomic_write

    raw = path.read_bytes().decode('utf-8-sig') if path.exists() else ''
    lines = [line for line in raw.splitlines() if line.strip().partition('=')[0] != key]
    lines.append(f'{key}={value}')
    _atomic_write(path, ('\n'.join(lines) + '\n').encode('utf-8'))


def set_account_mode(data_dir, enabled):
    from .folder_store import library_lock

    path = env_path(data_dir)
    with library_lock(path.parent, name='.accounts.lock'):
        if enabled:
            accounts = Accounts(data_dir, manage=True)
            with accounts.connect() as con:
                if not con.execute('SELECT 1 FROM accounts WHERE owner = 1').fetchone():
                    raise ValueError('Set up an owner account first.')
        _write_env_value(path, ACCESS_MODE, 'accounts' if enabled else 'normal')


def parse_tunnel_host(value):
    value = value.strip()
    if not value:
        return ''
    url = urlsplit(value if '://' in value else 'https://' + value)
    host = url.hostname or ''
    if (url.scheme != 'https' or url.netloc.lower() not in (host, host + ':443')
            or url.path not in ('', '/') or url.query or url.fragment
            or len(host) > 253 or '.' not in host
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))
            or not host.rsplit('.', 1)[-1].isalpha()):
        raise ValueError('Enter the public HTTPS address Cloudflare gives you.')
    return host


def set_tunnel_host(data_dir, value):
    from .folder_store import library_lock

    host = parse_tunnel_host(value)
    path = env_path(data_dir)
    with library_lock(path.parent, name='.accounts.lock'):
        if host and not account_mode(data_dir):
            raise ValueError('Turn on Account mode before configuring a tunnel.')
        _write_env_value(path, TUNNEL_HOST, host)
    return host


def make_key(data_dir):
    """Create a private random key without replacing other .env entries."""
    from .store import _atomic_write
    from .folder_store import library_lock

    path = env_path(data_dir)
    with library_lock(path.parent, name='.accounts.lock'):
        values = read_env(path)
        if KEY_NAME not in values:
            raw = path.read_bytes() if path.exists() else b''
            if raw and not raw.endswith(b'\n'):
                raw += b'\n'
            _atomic_write(path, raw + f'{KEY_NAME}={secrets.token_hex(32)}\n'.encode('ascii'))
            if os.name != 'nt':
                path.chmod(0o600)
        return path


def _password(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**17, r=8, p=1,
                          maxmem=192 * 1024**2, dklen=32)


def _credentials(username, password):
    if not isinstance(username, str) or not USER_NAME.fullmatch(username):
        raise ValueError('Use 1 to 64 letters, numbers, dots, dashes or underscores for the username.')
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ValueError('Choose a password between 15 and 128 characters.')
    salt = secrets.token_bytes(16)
    return username.lower(), salt, _password(password, salt)


def validate_guest_folders(selected, indexed):
    if not isinstance(selected, list) or len(selected) > 64 or any(not isinstance(path, str) for path in selected):
        raise ValueError('Select up to 64 indexed folders. Leave empty to give no folder access.')
    if not set(selected) <= indexed:
        raise ValueError('Select folders from the current index.')
    return sorted(set(selected))


def _login_name(username, password):
    if isinstance(username, str) and USER_NAME.fullmatch(username) and isinstance(password, str) and len(password) <= 128:
        return username.lower()
    return None


@dataclass(frozen=True)
class Account:
    username: str
    owner: bool
    folders: tuple[str, ...]
    csrf: str


class Accounts:
    def __init__(self, data_dir, *, initialize=False, manage=False, enabled=None):
        root = Path(data_dir)
        self.path = root / 'accounts.sqlite3'
        self.initialize = initialize
        values = read_env(env_path(root))
        self.enabled = (values.get(ACCESS_MODE, '').strip().lower() == 'accounts') if enabled is None else enabled
        if not (initialize or manage or self.enabled):
            return
        if not initialize and not self.path.is_file():
            raise ValueError('Set up an owner account first.')
        key = os.environ.get(KEY_NAME) or values.get(KEY_NAME, '')
        try:
            self.key = bytes.fromhex(key.strip())
        except ValueError as exc:
            raise ValueError(f'Invalid {KEY_NAME} in the library .env file.') from exc
        if len(self.key) != 32:
            raise ValueError(f'The library .env needs a 32-byte hexadecimal {KEY_NAME}.')
        self.login_slots = threading.BoundedSemaphore(2)
        self.attempts = {}
        self.attempt_lock = threading.Lock()

    @contextmanager
    def connect(self):
        mode = 'rwc' if self.initialize else 'rw'
        con = sqlite3.connect(f'{self.path.resolve().as_uri()}?mode={mode}', uri=True)
        try:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys = ON')
            with con:
                yield con
        finally:
            con.close()

    @classmethod
    def setup(cls, root, username, password):
        from .folder_store import library_lock

        username, salt, verifier = _credentials(username, password)
        env = make_key(root)
        with library_lock(env.parent, name='.accounts.lock'):
            Path(root).mkdir(parents=True, exist_ok=True)
            auth = cls(root, initialize=True)
            with auth.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                con.execute('''CREATE TABLE IF NOT EXISTS accounts (username TEXT PRIMARY KEY,
                    salt BLOB NOT NULL, verifier BLOB NOT NULL, owner INTEGER NOT NULL, folders TEXT NOT NULL)''')
                con.execute('''CREATE TABLE IF NOT EXISTS sessions (digest BLOB PRIMARY KEY, username TEXT NOT NULL
                    REFERENCES accounts(username) ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL)''')
                if con.execute('SELECT 1 FROM accounts WHERE owner = 1').fetchone():
                    raise ValueError('An owner account already exists.')
                con.execute('INSERT INTO accounts VALUES (?,?,?,1,?)', (username, salt, verifier, '[]'))
            if os.name != 'nt':
                auth.path.chmod(0o600)
            _write_env_value(env, ACCESS_MODE, 'accounts')

    def _digest(self, token):
        return hmac.digest(self.key, token.encode('ascii'), 'sha256')

    def _matching_account(self, username, password):
        with self.connect() as con:
            row = con.execute('SELECT salt, verifier, owner FROM accounts WHERE username = ?', (username,)).fetchone()
        actual = _password(password, row['salt'] if row else b'\x00' * 16)
        return row if row is not None and hmac.compare_digest(actual, row['verifier']) else None

    def verify_owner(self, username, password):
        username = _login_name(username, password)
        row = self._matching_account(username, password) if username else None
        return row is not None and bool(row['owner'])

    def login(self, username, password, address):
        username = _login_name(username, password)
        if not username:
            return None
        now = time.monotonic()
        with self.attempt_lock:
            if len(self.attempts) >= 2048:
                self.attempts = {key: value for key, value in self.attempts.items() if now - value[0] < 60}
            if len(self.attempts) >= 2048 or not self.login_slots.acquire(blocking=False):
                raise ValueError('Too many sign-in attempts. Try again shortly.')
            start, count = self.attempts.get(address, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            if count >= 8:
                self.login_slots.release()
                raise ValueError('Too many sign-in attempts. Try again shortly.')
            self.attempts[address] = (start, count + 1)
        try:
            row = self._matching_account(username, password)
            if row is None:
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                current = con.execute('SELECT verifier FROM accounts WHERE username = ?', (username,)).fetchone()
                if current is None or current['verifier'] != row['verifier']:
                    return None
                con.execute('DELETE FROM sessions WHERE expires <= ?', (int(time.time()),))
                con.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                            (self._digest(token), username, csrf, int(time.time()) + SESSION_SECONDS))
            with self.attempt_lock:
                self.attempts.pop(address, None)
            return token
        finally:
            self.login_slots.release()

    def session(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        with self.connect() as con:
            row = con.execute('''SELECT a.username,a.owner,a.folders,s.csrf FROM sessions s
                JOIN accounts a ON s.username = a.username WHERE s.digest = ? AND s.expires > ?''',
                              (self._digest(token), int(time.time()))).fetchone()
        if row:
            return Account(row['username'], bool(row['owner']), tuple(json.loads(row['folders'])), row['csrf'])
        return None

    def logout(self, token):
        with self.connect() as con:
            con.execute('DELETE FROM sessions WHERE digest = ?', (self._digest(token),))

    def guests(self):
        with self.connect() as con:
            return [dict(username=row['username'], folders=json.loads(row['folders'])) for row in
                    con.execute('SELECT username,folders FROM accounts WHERE owner = 0 ORDER BY username')]

    def users(self):
        with self.connect() as con:
            return [dict(username=row['username'], admin=bool(row['owner']), folders=json.loads(row['folders']))
                    for row in con.execute('SELECT username,owner,folders FROM accounts ORDER BY username')]

    def save_guest(self, username, password, folders, *, create=None):
        self._save_account(username, password, folders, admin=False, create=create, allow_admin=False)

    def save_user(self, username, password, folders, *, admin=False, create=None):
        """Set roles from the trusted local account manager."""
        self._save_account(username, password, folders, admin=admin, create=create, allow_admin=True)

    def _save_account(self, username, password, folders, *, admin, create, allow_admin):
        if not isinstance(username, str) or not USER_NAME.fullmatch(username):
            raise ValueError('Invalid username.')
        if not isinstance(admin, bool):
            raise ValueError('Invalid administrator option.')
        username = username.lower()
        # A blank password preserves an existing account's credentials.
        credential = _credentials(username, password) if password != '' else None
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT owner FROM accounts WHERE username = ?', (username,)).fetchone()
            if row and row['owner'] and not allow_admin:
                raise ValueError('This username belongs to the owner.')
            if create is True and row is not None:
                raise ValueError('This username is already in use. Select it from the user list to edit it.')
            if create is False and row is None:
                raise ValueError('This user was removed. Refresh the user list.')
            if row and row['owner'] and not admin:
                self._keep_administrator(con)
            if credential:
                _, salt, verifier = credential
                con.execute('''INSERT INTO accounts VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET
                    salt=excluded.salt, verifier=excluded.verifier, owner=excluded.owner, folders=excluded.folders''',
                            (username, salt, verifier, int(admin), json.dumps(folders)))
            elif row:
                con.execute('UPDATE accounts SET owner=?,folders=? WHERE username=?',
                            (int(admin), json.dumps(folders), username))
            else:
                raise ValueError('Set a password for a new account.')
            con.execute('DELETE FROM sessions WHERE username=?', (username,))

    @staticmethod
    def _keep_administrator(con):
        if con.execute('SELECT COUNT(*) FROM accounts WHERE owner=1').fetchone()[0] <= 1:
            raise ValueError('Keep at least one administrator. Make another user an administrator first.')

    def delete_user(self, username):
        if not isinstance(username, str) or not USER_NAME.fullmatch(username):
            raise ValueError('Invalid username.')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT owner FROM accounts WHERE username=?', (username.lower(),)).fetchone()
            if row and row['owner']:
                self._keep_administrator(con)
            con.execute('DELETE FROM accounts WHERE username=?', (username.lower(),))

    def delete_guest(self, username):
        if not isinstance(username, str):
            raise ValueError('Invalid username.')
        with self.connect() as con:
            con.execute('DELETE FROM accounts WHERE username=? AND owner=0', (username.lower(),))
