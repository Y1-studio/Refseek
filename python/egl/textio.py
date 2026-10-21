from __future__ import annotations

import html.parser
import re
import zipfile
import zlib
from pathlib import Path

TEXT_EXTS = {
    ".txt", ".text", ".md", ".markdown", ".rst", ".org", ".adoc", ".tex", ".log",
    ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".xml", ".svg", ".env", ".srt", ".vtt", ".nfo",
    ".html", ".htm", ".xhtml",".mdx",
}
CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".c", ".h",
    ".cpp", ".cc", ".cxx", ".hpp", ".cs", ".java", ".kt", ".kts", ".go", ".rs",
    ".rb", ".php", ".swift", ".m", ".mm", ".scala", ".clj", ".hs", ".lua", ".r",
    ".jl", ".dart", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".sql", ".graphql", ".gql", ".proto", ".vue", ".svelte", ".css", ".scss",
    ".sass", ".less", ".makefile", ".mk", ".cmake", ".gradle", ".dockerfile",
    ".tf", ".hcl", ".vim", ".el", ".erl", ".ex", ".exs", ".nim", ".zig", ".sol",
}
DOC_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".odt"}
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jpe", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".avif", ".heic", ".heif", ".ico", ".pnm", ".pbm", ".pgm", ".ppm", ".xcf",
}
AUDIO_EXTS = {".wav", ".wave", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus", ".wma", ".aiff", ".aif"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ts"}

MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".avif": "image/avif", ".heic": "image/heic", ".heif": "image/heif",
    ".ico": "image/x-icon", ".pdf": "application/pdf", ".epub": "application/epub+zip",
}


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    name = path.name.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".pptx":
        return "pptx"
    if ext == ".xlsx":
        return "xlsx"
    if ext == ".epub":
        return "epub"
    if ext == ".odt":
        return "odt"
    if ext in TEXT_EXTS:
        return "text"
    if ext in CODE_EXTS:
        return "code"
    if name in {"makefile", "dockerfile", "readme", "license", "authors", "changelog", "notice", "todo"}:
        return "text"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return "skip"


def is_media(kind: str) -> bool:
    return kind in ("image", "audio", "video")


def shrink_image(raw: bytes, max_side: int = 1024) -> bytes:
    """Downscale image bytes so the long side is <= max_side. Returns the original
    bytes when already small, Pillow is missing, or the format isn't a plain
    JPEG/PNG/WEBP (keeps bytes consistent with the file extension)."""
    if not raw or max_side <= 0:
        return raw
    try:
        from PIL import Image
    except Exception:
        return raw
    import io

    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:
        return raw
    if max(img.size) <= max_side:
        return raw
    fmt = (img.format or "").upper()
    if fmt in ("JPEG", "JPG", "MPO"):
        img.draft("RGB", (max_side, max_side))
    try:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        if fmt in ("JPEG", "JPG", "MPO"):
            img.convert("RGB").save(out, "JPEG", quality=85)
        elif fmt == "WEBP":
            img.save(out, "WEBP", quality=85)
        elif fmt == "PNG" or "A" in img.getbands():
            img.save(out, "PNG")
        else:
            return raw
        return out.getvalue()
    except Exception:
        return raw


def mime_of(path: Path, kind: str) -> str:
    if kind in MIME_BY_EXT and kind != "pdf":
        return MIME_BY_EXT[kind]
    if kind == "audio":
        return "audio/" + path.suffix.lstrip(".").lower()
    if kind == "video":
        return "video/" + path.suffix.lstrip(".").lower()
    return MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


_MAX_TEXT_BYTES = 20_000_000


def read_file(path: Path, kind: str) -> tuple[str, str]:
    try:
        if kind in ("text", "code"):
            raw = path.read_bytes()[:_MAX_TEXT_BYTES]
            text = raw.decode("utf-8", "ignore")
            if kind == "text" and path.suffix.lower() in (".xml", ".svg", ".html", ".htm", ".xhtml"):
                text = strip_markup(text)
            return _title_from_text(path, text), text
        if kind == "pdf":
            return path.stem, extract_pdf_text(path)
        if kind == "docx":
            return extract_docx_text(path)
        if kind == "pptx":
            return extract_pptx_text(path)
        if kind == "xlsx":
            return extract_xlsx_text(path)
        if kind == "epub":
            return extract_epub_text(path)
        if kind == "odt":
            return extract_odt_text(path)
    except Exception:
        return path.stem, ""
    return path.stem, ""


def _title_from_text(path: Path, text: str) -> str:
    first = text.strip().split("\n", 1)[0].strip() if text.strip() else ""
    first = first.lstrip("#").strip()
    if not first:
        return path.stem
    return first[:160]


class _MarkupStripper(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def strip_markup(text: str) -> str:
    stripper = _MarkupStripper()
    try:
        stripper.feed(text)
    except Exception:
        pass
    out = "".join(stripper.parts)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n\s*\n+", "\n\n", out)
    return out.strip()


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            step = max(1, size - max(0, overlap))
            for i in range(0, len(para), step):
                piece = para[i : i + size]
                if piece.strip():
                    chunks.append(piece)
            continue
        cand = f"{cur}\n\n{para}" if cur else para
        if len(cand) <= size:
            cur = cand
        else:
            if cur:
                chunks.append(cur)
            cur = para
    if cur.strip():
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _xml_text(xml_bytes: bytes) -> str:
    xml = xml_bytes.decode("utf-8", "ignore")
    xml = re.sub(r"<(w:p|a:p|p|si|row|div|br|/w:p|/a:p|/p|/div)[^>]*>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    xml = html.unescape(xml)
    xml = re.sub(r"[ \t]+", " ", xml)
    xml = re.sub(r"\n\s*\n+", "\n\n", xml)
    return xml.strip()


def _zip_read(zf: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return zf.read(name)
    except KeyError:
        return None


def extract_docx_text(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as zf:
        title = ""
        core = _zip_read(zf, "docProps/core.xml")
        if core:
            m = re.search(rb"<dc:title[^>]*>(.*?)</dc:title>", core, re.S)
            if m:
                title = m.group(1).decode("utf-8", "ignore").strip()
        body = _zip_read(zf, "word/document.xml") or b""
        return title or path.stem, _xml_text(body)


def extract_pptx_text(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        title = ""
        core = _zip_read(zf, "docProps/core.xml")
        if core:
            m = re.search(rb"<dc:title[^>]*>(.*?)</dc:title>", core, re.S)
            if m:
                title = m.group(1).decode("utf-8", "ignore").strip()
        text = "\n\n".join(_xml_text(zf.read(n)) for n in names)
        return title or path.stem, text


def extract_xlsx_text(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as zf:
        shared = _zip_read(zf, "xl/sharedStrings.xml")
        strings = []
        if shared:
            strings = re.findall(rb"<t[^>]*>(.*?)</t>", shared, re.S)
            strings = [s.decode("utf-8", "ignore") for s in strings]
        sheets = [n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        sheets.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        parts = []
        for name in sheets:
            data = zf.read(name)
            for m in re.finditer(rb"<c\b[^>]*>.*?</c>", data, re.S):
                cell = m.group(0)
                tattr = re.search(rb'\st="(\w+)"', cell)
                ctype = tattr.group(1).decode() if tattr else ""
                v = re.search(rb"<v[^>]*>(.*?)</v>", cell, re.S)
                inline = re.search(rb"<is>.*?<t[^>]*>(.*?)</t>", cell, re.S)
                if ctype == "s" and v:
                    idx = int(v.group(1))
                    if idx < len(strings):
                        parts.append(strings[idx])
                elif inline:
                    parts.append(inline.group(1).decode("utf-8", "ignore"))
                elif v:
                    parts.append(v.group(1).decode("utf-8", "ignore"))
        return path.stem, "\n".join(p for p in parts if p.strip())


def extract_epub_text(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as zf:
        names = [
            n for n in zf.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm", ".xml")) and not n.startswith("META-INF")
        ]
        names.sort()
        metas = {
            "dc:title": re.compile(rb"<dc:title[^>]*>(.*?)</dc:title>", re.S),
            "dc:creator": re.compile(rb"<dc:creator[^>]*>(.*?)</dc:creator>", re.S),
        }
        title = ""
        for name in ("EPUB/content.opf", "OEBPS/content.opf", "content.opf"):
            opf = _zip_read(zf, name)
            if opf:
                m = metas["dc:title"].search(opf)
                if m:
                    title = m.group(1).decode("utf-8", "ignore").strip()
                break
        texts = []
        for name in names:
            if name.endswith((".xhtml", ".html", ".htm")):
                texts.append(_xml_text(zf.read(name)))
        return title or path.stem, "\n\n".join(t for t in texts if t.strip())


def extract_odt_text(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as zf:
        body = _zip_read(zf, "content.xml") or b""
        title = ""
        m = re.search(rb"<dc:title[^>]*>(.*?)</dc:title>", _zip_read(zf, "meta.xml") or b"", re.S)
        if m:
            title = m.group(1).decode("utf-8", "ignore").strip()
        return title or path.stem, _xml_text(body)


_TOKEN_RE = re.compile(r"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>|T[jJ]|T\*|Td|TD|ET|BT")


def _pdf_unescape(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    simple = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < len(raw):
        c = raw[i]
        if c != 0x5C:
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(raw):
            break
        c = raw[i]
        if c in simple:
            out.append(simple[c])
            i += 1
        elif 0x30 <= c <= 0x37:
            j = i
            while j < len(raw) and j < i + 3 and 0x30 <= raw[j] <= 0x37:
                j += 1
            out.append(int(raw[i:j], 8) & 0xFF)
            i = j
        elif c == 0x0A:
            i += 1
        else:
            out.append(c)
            i += 1
    return bytes(out)


def _pdf_stream_text(content: bytes) -> str:
    text = content.decode("latin-1", "ignore")
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.startswith("("):
            out.append(_pdf_unescape(tok[1:-1].encode("latin-1", "ignore")).decode("latin-1", "ignore"))
        elif tok.startswith("<"):
            try:
                out.append(bytes.fromhex(re.sub(r"\s", "", tok[1:-1])).decode("latin-1", "ignore"))
            except Exception:
                pass
        elif tok in ("Td", "TD", "T*", "ET", "BT"):
            out.append("\n")
    joined = "".join(out)
    joined = re.sub(r"[ \t]+", " ", joined)
    joined = re.sub(r"\n\s*\n+", "\n\n", joined)
    return joined.strip()


def _pdf_payload(raw: bytes) -> bytes:
    try:
        out = zlib.decompress(raw)
        if out:
            return out
    except Exception:
        pass
    try:
        out = zlib.decompressobj().decompress(raw)
        if out:
            return out
    except Exception:
        pass
    return raw


def extract_pdf_text(path: Path) -> str:
    data = path.read_bytes()
    chunks: list[str] = []
    pos = 0
    limit = 4000
    while limit > 0:
        start = data.find(b"stream", pos)
        if start < 0:
            break
        limit -= 1
        p = start + 6
        if data[p : p + 2] == b"\r\n":
            p += 2
        elif data[p : p + 1] in (b"\n", b"\r"):
            p += 1
        end = data.find(b"endstream", p)
        if end < 0:
            break
        raw = data[p:end]
        pos = end + 9
        text = _pdf_stream_text(_pdf_payload(raw))
        if text:
            chunks.append(text)
    return "\n".join(chunks)
