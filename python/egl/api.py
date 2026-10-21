from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from .config import PROBE_ORDER, Settings


class ApiError(RuntimeError):
    pass


class EmbedClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.api_type = settings.api_type
        self.api_url = settings.api_url
        if self.api_url == "auto":
            self.api_url, self.api_type = self._probe()
        self.api_url = self.api_url.rstrip("/")
        if self.api_type == "auto":
            self.api_type = self._detect()

    @property
    def base(self) -> str:
        if self.api_type == "ollama":
            return self.api_url.replace("/v1", "").replace("/api", "").rstrip("/")
        return self.api_url

    def _request(self, method: str, url: str, payload=None, timeout=None):
        headers = {"Content-Type": "application/json"}
        if self.api_type != "ollama" and self.s.api_key:
            headers["Authorization"] = f"Bearer {self.s.api_key}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=timeout or self.s.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "ignore")[:400]
                except Exception:
                    pass
                retryable = e.code in (408, 429) or e.code >= 500
                if not retryable or attempt == 2:
                    raise ApiError(f"{method} {url} -> HTTP {e.code}: {body}")
                last = ApiError(f"{method} {url} -> HTTP {e.code}: {body}")
            except urllib.error.URLError as e:
                last = ApiError(f"{method} {url} -> {e.reason}")
                if isinstance(e.reason, ConnectionRefusedError):
                    raise last
            except Exception as e:
                last = ApiError(f"{method} {url} -> {e}")
            time.sleep(1.5 * (attempt + 1))
        raise last

    def get(self, path: str):
        return self._request("GET", self.base + path, None, timeout=20)

    def _probe(self):
        errors = []
        for url, kind in PROBE_ORDER:
            candidate = EmbedClient.__new__(EmbedClient)
            candidate.s = self.s
            candidate.api_type = kind
            candidate.api_url = url.rstrip("/")
            try:
                vec = candidate.embed_one("probe")
                if vec:
                    return url.rstrip("/"), kind
            except Exception as e:
                errors.append(f"{url}: {str(e)[:80]}")
        raise ApiError(
            "no local embedding API that can embed text was found on 127.0.0.1 "
            "(tried 8765, 1234, 11434, 8000, 8080).\n  - bundled server: python server.py\n"
            "  - llama.cpp: llama-server -m model.gguf --embeddings\n"
            "  - LM Studio: enable the local server (localhost:1234)\n"
            "  - or set EG_API_URL / EG_API_TYPE yourself"
            + ("\n  last errors: " + "; ".join(errors) if errors else "")
        )

    def _detect(self) -> str:
        try:
            self.get("/models")
            return "openai"
        except Exception:
            pass
        try:
            root = self.base
            req = urllib.request.Request(root + "/api/version", method="GET")
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return "ollama"
        except Exception:
            pass
        return "openai"

    def describe(self) -> dict:
        info = {"api_type": self.api_type, "api_url": self.base, "model": self.s.model}
        try:
            if self.api_type == "ollama":
                info["tags"] = self.get("/api/tags").get("models", [])
            else:
                data = self.get("/models")
                info["models"] = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
        except ApiError as e:
            info["error"] = str(e)
        return info

    def embed(self, items: list, batch: int | None = None):
        s = self.s
        batch = batch or s.batch
        out: list[list[float]] = []
        for i in range(0, len(items), batch):
            part = items[i : i + batch]
            if self.api_type == "ollama":
                texts = [x if isinstance(x, str) else _parts_to_text(x) for x in part]
                payload = {"model": s.model, "input": texts, "truncate": True}
                if s.dim:
                    payload["dimensions"] = s.dim
                data = self._request("POST", self.base + "/api/embed", payload)
                vecs = data.get("embeddings") or []
            else:
                payload = {"model": s.model, "input": part, "encoding_format": "float"}
                if s.dim:
                    payload["dimensions"] = s.dim
                data = self._request("POST", self.base + "/embeddings", payload)
                rows = data.get("data") or []
                rows = sorted(rows, key=lambda r: r.get("index", 0))
                vecs = [r.get("embedding") for r in rows]
            if len(vecs) != len(part):
                raise ApiError(f"embeddings returned {len(vecs)} vectors for {len(part)} inputs")
            out.extend(vecs)
        return out

    def embed_one(self, item):
        return self.embed([item])[0]

    def embed_query(self, text: str):
        return self.embed_one(self.s.with_prefix_query(text))

    def embed_image_file(self, path, mime: str):
        raw = open(path, "rb").read()
        return image_item(raw, mime)


CONTENT_TYPES = {"image": "image_url", "audio": "input_audio", "video": "input_video"}


def media_item(raw: bytes, mime: str, kind: str = "image") -> dict:
    b64 = base64.b64encode(raw).decode("ascii")
    return content_item(f"data:{mime};base64,{b64}", kind)


def content_item(url: str, kind: str = "image") -> dict:
    ptype = CONTENT_TYPES.get(kind, kind + "_url")
    return {
        "content": [
            {
                "type": ptype,
                ptype: {"url": url, "data": url},
            }
        ]
    }


def image_item(raw: bytes, mime: str) -> dict:
    return media_item(raw, mime, "image")


def _parts_to_text(parts) -> str:
    if isinstance(parts, str):
        return parts
    out = []
    for p in parts:
        if p.get("type") == "text":
            out.append(p.get("text", ""))
    return "\n".join(out)


def l2norm(vec) -> list[float]:
    n = sum(x * x for x in vec) ** 0.5
    if n == 0:
        return list(vec)
    return [x / n for x in vec]
