"""Interruptible HTTP transport for indexing; ordinary search requests stay unchanged."""
import http.client
import io
import json
import socket
import ssl
import time
import urllib.error
from urllib.parse import urlsplit


def request(method, url, data, headers, timeout, check):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Embedding URL must use HTTP or HTTPS')
    deadline = time.monotonic() + timeout

    def tick():
        check()
        if time.monotonic() >= deadline:
            raise TimeoutError('Embedding request timed out')

    tick()
    sock = socket.create_connection((parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)), timeout=min(3, timeout))
    try:
        if parts.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        sock.settimeout(.2)
        host = parts.netloc.rsplit('@', 1)[-1]
        target = parts.path or '/'
        if parts.query:
            target += '?' + parts.query
        payload = data or b''
        fields = dict(headers, Host=host, Connection='close', **{'Content-Length': str(len(payload))})
        wire = (method + ' ' + target + ' HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n').encode('ascii')
        for block in (wire, payload):
            view = memoryview(block)
            while view:
                tick()
                try:
                    n = sock.send(view[:65536])
                except socket.timeout:
                    continue
                if not n:
                    raise ConnectionError('Embedding connection closed while sending')
                view = view[n:]
        raw = bytearray()
        while True:
            tick()
            try:
                block = sock.recv(65536)
            except socket.timeout:
                continue
            if not block:
                break
            raw.extend(block)
            if len(raw) > 32 * 1024 * 1024:
                raise ValueError('Embedding response exceeds 32 MiB')
            if b'\r\n\r\n' not in raw and len(raw) > 65536:
                raise ValueError('Embedding response headers too large')
            if _complete(raw):
                break
        tick()
        class Buffered:
            def makefile(self, *_):
                return io.BytesIO(raw)
        response = http.client.HTTPResponse(Buffered())
        response.begin()
        body = response.read()
        if response.status >= 400:
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(body))
        if not 200 <= response.status < 300:
            raise ValueError(f'Unexpected embedding HTTP status {response.status}')
        return json.loads(body.decode('utf-8'))
    finally:
        sock.close()


def _complete(raw):
    end = raw.find(b'\r\n\r\n')
    if end < 0:
        return False
    lines = bytes(raw[:end]).split(b'\r\n')
    status = int(lines[0].split()[1])
    if status < 200:
        return False
    headers = dict(line.lower().split(b':', 1) for line in lines[1:] if b':' in line)
    body = raw[end + 4:]
    if b'content-length' in headers:
        size = int(headers[b'content-length'].strip())
        return size >= 0 and len(body) >= size
    if b'chunked' in headers.get(b'transfer-encoding', b''):
        pos = 0
        while True:
            line = body.find(b'\r\n', pos)
            if line < 0:
                return False
            size = int(body[pos:line].split(b';', 1)[0], 16)
            pos = line + 2
            if not size:
                return body[pos:pos + 2] == b'\r\n' or body.find(b'\r\n\r\n', pos) >= 0
            if len(body) < pos + size + 2:
                return False
            pos += size + 2
    return status in (204, 304)
