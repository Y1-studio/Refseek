from __future__ import annotations

from pathlib import Path

from .api import EmbedClient, l2norm, media_item
from .config import Settings
from .store import Index
from .textio import kind_of, mime_of, shrink_image


class Searcher:
    def __init__(self, settings: Settings, client: EmbedClient):
        self.s = settings
        self.client = client

    def index(self, roots=None) -> Index:
        return Index.load(self.s.data_dir,roots=roots)

    def query_vector(self, text=None, image_path=None, image_bytes=None, image_mime="image/png"):
        if image_bytes is not None:
            raw = shrink_image(image_bytes, self.s.image_max)
            vec = self.client.embed_one(media_item(raw, image_mime, "image"))
        elif image_path:
            p = Path(image_path).expanduser()
            raw = p.read_bytes()
            kind = kind_of(p)
            if kind == "image":
                raw = shrink_image(raw, self.s.image_max)
            vec = self.client.embed_one(media_item(raw, mime_of(p, kind), kind))
        elif text:
            vec = self.client.embed_query(text)
        else:
            raise ValueError("provide a text query or an image")
        return l2norm(vec)

    def search(self, text=None, image_path=None, image_bytes=None, image_mime="image/png",
               img_weight=0.5, neg_image_bytes=None, neg_image_mime="image/png",
               neg_image_weight=1.0, k=20, kind=None, index=None,
               exclude=None, neg_weight=1.0, wants=None, combine="avg", roots=None,
               offset=0):
        index = index or self.index(roots=roots)
        if not index.records:
            return []
        kinds = None
        if kind:
            kinds = [x.strip() for x in kind.split(",") if x.strip()]
        all_wants = []
        if text and text.strip():
            all_wants.append(text.strip())
        all_wants.extend(e for e in (wants or []) if e and e.strip())
        negatives = None
        excludes = [e for e in (exclude or []) if e and e.strip()]
        if excludes:
            raw = self.client.embed([self.s.with_prefix_query(e) for e in excludes])
            negatives = [l2norm(v) for v in raw]
        if neg_image_bytes is not None:
            ivec = self.query_vector(image_bytes=neg_image_bytes, image_mime=neg_image_mime)
            neg_image_vecs = [ivec]
        else:
            neg_image_vecs = None
        try:
            niw = min(2.0, max(0.0, float(neg_image_weight)))
        except (TypeError, ValueError):
            niw = 1.0
        try:
            alpha = min(1.0, max(0.0, float(img_weight)))
        except (TypeError, ValueError):
            alpha = 0.5
        want_image = bool(image_path or image_bytes is not None)
        if want_image and all_wants and 0.0 < alpha < 1.0:
            if len(all_wants) == 1:
                tvec = self.query_vector(text=all_wants[0])
            else:
                raw = self.client.embed([self.s.with_prefix_query(w) for w in all_wants])
                vecs = [l2norm(v) for v in raw]
                dim = len(vecs[0])
                tvec = l2norm([sum(v[d] for v in vecs) / len(vecs) for d in range(dim)])
            ivec = self.query_vector(image_path=image_path, image_bytes=image_bytes,
                                     image_mime=image_mime)
            fused = l2norm([a * alpha + t * (1.0 - alpha) for a, t in zip(ivec, tvec)])
            return index.search(fused, k=k, kinds=kinds, negatives=negatives,
                                neg_weight=neg_weight, roots=roots, offset=offset,
                                neg_images=neg_image_vecs, neg_image_weight=niw)
        if want_image and (not all_wants or alpha >= 1.0):
            vec = self.query_vector(image_path=image_path, image_bytes=image_bytes,
                                    image_mime=image_mime)
            return index.search(vec, k=k, kinds=kinds, negatives=negatives, neg_weight=neg_weight,
                                roots=roots, offset=offset,
                                neg_images=neg_image_vecs, neg_image_weight=niw)
        if not all_wants:
            raise ValueError("provide a text query or an image")
        if len(all_wants) == 1:
            vec = self.query_vector(text=all_wants[0])
            return index.search(vec, k=k, kinds=kinds, negatives=negatives, neg_weight=neg_weight,
                                roots=roots, offset=offset,
                                neg_images=neg_image_vecs, neg_image_weight=niw)
        if combine not in ("avg", "max"):
            raise ValueError("combine must be 'avg' or 'max'")
        raw = self.client.embed([self.s.with_prefix_query(w) for w in all_wants])
        vecs = [l2norm(v) for v in raw]
        if combine == "avg":
            dim = len(vecs[0])
            mean = [sum(v[d] for v in vecs) / len(vecs) for d in range(dim)]
            return index.search(mean, k=k, kinds=kinds, negatives=negatives, neg_weight=neg_weight,
                                roots=roots, offset=offset,
                                neg_images=neg_image_vecs, neg_image_weight=niw)
        best: dict[tuple, dict] = {}
        for v in vecs:
            for hit in index.search(v, k=k + offset, kinds=kinds, negatives=negatives,
                                    neg_weight=neg_weight, roots=roots,
                                    neg_images=neg_image_vecs, neg_image_weight=niw):
                key = (hit.get("path"), hit.get("chunk", 0))
                if key not in best or hit["score"] > best[key]["score"]:
                    best[key] = hit
        merged = sorted(best.values(), key=lambda h: h["score"], reverse=True)
        return merged[offset:offset + k]
