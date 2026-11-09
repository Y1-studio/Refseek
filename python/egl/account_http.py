"""HTTP authorization for the optional account mode."""
import copy
import hmac
from ipaddress import ip_address
import json
from http.cookies import CookieError, SimpleCookie
import os
from pathlib import Path
import stat

from .account_pages import LOGIN, USERS
from .accounts import COOKIE, SESSION_SECONDS, account_mode, validate_guest_folders


GUEST_GET = {'/', '/index.html', '/api/status', '/api/search', '/api/roots', '/api/style_models', '/thumb', '/file'}
GUEST_POST = {'/api/search_image', '/api/logout'}
PROXY_HEADERS = ('CF-Ray', 'CF-Connecting-IP', 'Forwarded', 'X-Forwarded-For',
                 'X-Forwarded-Host', 'X-Forwarded-Proto')


def allowed_file(path, folders, private):
    candidate = Path(path)
    eligible = [root for root in folders if candidate.is_relative_to(root)]
    if not eligible:
        return None
    try:
        target = candidate.resolve(strict=True)
        return target if (target.is_file() and not target.is_relative_to(private)
                          and any(target.is_relative_to(root) for root in eligible)) else None
    except (OSError, ValueError):
        return None


def guest_paths(paths, folders, private):
    """Validate indexed paths in batches by directory, checking links individually."""
    by_directory = {}
    for path in paths:
        candidate = Path(path)
        roots = tuple(root for root in folders if candidate.is_relative_to(root))
        if roots:
            by_directory.setdefault((candidate.parent, roots), {}).setdefault(
                os.path.normcase(candidate.name), []).append(path)
    visible = set()
    for (directory, roots), names in by_directory.items():
        try:
            resolved = directory.resolve(strict=True)
            if not resolved.is_relative_to(private) and any(resolved.is_relative_to(root) for root in roots):
                with os.scandir(resolved) as entries:
                    for entry in entries:
                        matches = names.get(os.path.normcase(entry.name))
                        if not matches:
                            continue
                        info = entry.stat(follow_symlinks=False)
                        reparse = getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                        if stat.S_ISLNK(info.st_mode) or reparse:
                            visible.update(path for path in matches if allowed_file(path, roots, private))
                        elif stat.S_ISREG(info.st_mode):
                            visible.update(matches)
                continue
        except (OSError, ValueError):
            pass
        # Some known paths can be read even when their directory cannot be listed safely.
        visible.update(path for matches in names.values() for path in matches
                       if allowed_file(path, roots, private))
    return visible


def guest_index(index, grants, private):
    roots = tuple(Path(path) for path in grants)
    paths = guest_paths({row['path'] for row in index.records}, roots, private)
    selected = [i for i, row in enumerate(index.records) if row['path'] in paths]
    view = copy.copy(index)
    view.roots = list(grants)
    view.records = [index.records[i] for i in selected]
    view.vectors = [index.vectors[i] for i in selected]
    view._reduced_vectors = ([index._reduced_vectors[i] for i in selected]
                             if index._reduced_vectors is not None else None)
    view._partial = True
    view._matrix = view._reduced_matrix = None
    view._folder_entries = []
    view._catalog = None
    view._allowed_paths = paths
    view.__dict__.pop('_folder_tree', None)
    return view


class AccountHTTP:
    account = None
    token = ''

    @property
    def guest(self):
        return self.account is not None and not self.account.owner

    def authorize(self, path, *, post=False):
        self.account, self.token = None, ''
        host = self.headers.get('Host', '').lower()
        self.request_scheme = self.server.scheme
        self.login_address = self.client_address[0]
        if self.server.tunnel_host and host in (self.server.tunnel_host, self.server.tunnel_host + ':443'):
            try:
                if not ip_address(self.client_address[0]).is_loopback or self.headers.get('X-Forwarded-Proto') != 'https':
                    raise ValueError('Untrusted forwarding')
                self.login_address = str(ip_address(self.headers.get('CF-Connecting-IP', '')))
            except ValueError:
                self._json({'error': 'This address requires an HTTPS Cloudflare Tunnel.'}, 403)
                return False
            self.request_scheme = 'https'
            host = self.server.tunnel_host
        elif host not in self.server.account_hosts or any(header in self.headers for header in PROXY_HEADERS):
            self._json({'error': 'Unrecognized Refseek address.'}, 400)
            return False

        if not self.accounts.enabled:
            if self.accounts.path.exists() and account_mode(self.accounts.path.parent):
                self._json({'error': 'Restart Refseek to activate accounts.'}, 503)
                return False
            if post and self.headers.get('Origin') not in (None, self.request_scheme + '://' + host):
                self._json({'error': 'The request must come from this Refseek page.'}, 403)
                return False
            return True
        if post and self.headers.get('Origin') != self.request_scheme + '://' + host:
            self._json({'error': 'The request must come from this Refseek page.'}, 403)
            return False
        if post and path == '/api/login':
            return True
        if not post and path == '/login':
            return True
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', '')).get(COOKIE)
        except CookieError:
            cookie = None
        if cookie:
            self.token = cookie.value
            self.account = self.accounts.session(self.token)
        if self.account is None:
            if not post and path in ('/', '/index.html', '/users'):
                self._send(303, extra={'Location': '/login'})
            else:
                self._json({'error': 'Sign in to Refseek first.'}, 401)
            return False
        if self.guest and path not in (GUEST_POST if post else GUEST_GET):
            self._json({'error': 'This action is available to the owner only.'}, 403)
            return False
        if post and not hmac.compare_digest(self.headers.get('X-Refseek-CSRF', '').encode(), self.account.csrf.encode()):
            self._json({'error': 'Refresh the Refseek page and try again.'}, 403)
            return False
        return True

    def html(self, page):
        if self.account is not None:
            role = 'guest' if self.guest else 'owner'
            page = page.replace('<html lang="en">', f'<html lang="en" data-account="{role}">', 1)
            meta = f'<meta name="refseek-csrf" content="{self.account.csrf}">'
            # Existing tools use fetch as well. Only add the token on this exact origin.
            setup = '''<script>{const request = window.fetch.bind(window);
                const csrf = document.querySelector('meta[name="refseek-csrf"]').content;
                window.fetch = async (url, options) => {
                  const target = new URL(typeof url === 'string' ? url : url.url, location.href);
                  if (target.origin === location.origin && options?.method?.toUpperCase() === 'POST') {
                    const headers = new Headers(options.headers); headers.set('X-Refseek-CSRF', csrf);
                    options = {...options, headers};
                  }
                  const response = await request(url, options);
                  if (target.origin === location.origin && response.status === 401) location.replace('/login');
                  return response;
                };}</script>'''
            page = page.replace('</head>', meta + setup + '</head>', 1)
        return self._send(200, page, 'text/html; charset=utf-8')

    def cookie(self, token, lifetime):
        secure = '; Secure' if self.request_scheme == 'https' else ''
        return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={lifetime}{secure}'

    def account_get(self, path):
        if path == '/login':
            return self.html(LOGIN)
        if path == '/users':
            return self.html(USERS)
        return self._json(dict(users=self.accounts.guests(), folders=[row['path'] for row in self.index().folder_tree]))

    def account_post(self, path):
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Send a JSON request.')
            length = int(self.headers.get('Content-Length') or 0)
            if not 0 < length <= 65536:
                raise ValueError('Invalid account request size.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Invalid account request.')
            username = payload.get('username')
            if path == '/api/login':
                token = self.accounts.login(username, payload.get('password'), self.login_address)
                if not token:
                    return self._json({'error': 'Incorrect username or password.'}, 401)
                return self._send(200, '{"ok":true}', 'application/json', {'Set-Cookie': self.cookie(token, SESSION_SECONDS)})
            if path == '/api/logout':
                self.accounts.logout(self.token)
                return self._send(200, '{"ok":true}', 'application/json', {'Set-Cookie': self.cookie('', 0)})
            if path == '/api/users/remove':
                self.accounts.delete_guest(username)
            else:
                valid = {row['path'] for row in self.index().folder_tree}
                folders = validate_guest_folders(payload.get('folders'), valid)
                create = payload.get('create')
                if create is not None and not isinstance(create, bool):
                    raise ValueError('Invalid account editing mode.')
                self.accounts.save_guest(username, payload.get('password', ''), folders, create=create)
            return self._json({'ok': True})
        except (ValueError, TypeError) as exc:
            return self._json({'error': str(exc)}, 400)
