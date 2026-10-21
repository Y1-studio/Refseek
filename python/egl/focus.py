"""Experimental aspect retrieval. No index writes or model training.

Style axes are learned from style contrasts averaged over twelve subjects.
The style channel is the orthogonal projection onto those axes; the subject
channel is its residual. Both query and candidates receive the same transform.
Other aspects use independent matched descriptions (pose, view, composition,
palette, lighting). Text-learned axes approximate semantics, not body keypoints
or a guaranteed image decomposition. Calibration is lazy and cached per aspect.
"""
from __future__ import annotations

import base64
import hashlib
import math
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

from .api import l2norm
from .search import Searcher
from .store import _np

STYLES = (
    "photograph", "watercolor painting", "oil painting", "pencil drawing",
    "ink illustration", "cartoon illustration", "anime illustration",
    "pixel art image", "3D render",
)
SUBJECTS = ("cat", "dog", "horse", "car", "bicycle", "house", "tree",
            "flower", "mountain", "bird", "boat", "chair")

_POSE_CONTEXTS = tuple((style, person) for style in ("photograph", "pencil drawing")
                       for person in ("a person", "a woman", "a man", "a mannequin"))
_VISUAL_CONTEXTS = tuple((style, subject) for style in ("photograph", "painting")
                         for subject in ("a person", "a car", "a tree", "a house"))
FOCUS_GROUPS = {
    "style": {"label": "Style", "levels": STYLES,
              "contexts": tuple(SUBJECTS), "template": "a {level} of a {context}"},
    "pose": {"label": "Pose", "contexts": _POSE_CONTEXTS,
             "template": "a {context[0]} of {context[1]}, {level}",
             "levels": ("standing upright with arms down", "sitting on a chair",
                        "kneeling on one knee", "crouching low", "lying on their back",
                        "lying on their side", "walking with one leg forward", "running",
                        "jumping with bent legs", "standing with arms raised overhead",
                        "standing with arms crossed", "leaning forward",
                        "reaching forward with one arm", "standing with weight on one leg")},
    "viewpoint": {"label": "Viewpoint", "contexts": _VISUAL_CONTEXTS,
                  "template": "a {context[0]} of {context[1]}, {level}",
                  "levels": ("seen from the front", "seen from the back",
                             "seen from the side in profile", "seen in three-quarter view",
                             "seen from above", "seen from below", "seen from directly overhead")},
    "composition": {"label": "Composition", "contexts": _VISUAL_CONTEXTS,
                    "template": "a {context[0]} of {context[1]}, {level}",
                    "levels": ("centered in the frame", "positioned on the left side of the frame",
                               "positioned on the right side of the frame", "filling the frame in close-up",
                               "small in a wide scene with empty space", "in a symmetrical composition",
                               "in a diagonal composition", "with several subjects grouped together")},
    "color": {"label": "Color palette", "contexts": _VISUAL_CONTEXTS,
              "template": "a {context[0]} of {context[1]}, with {level}",
              "levels": ("predominantly red colors", "predominantly blue colors",
                         "predominantly green colors", "predominantly yellow colors",
                         "predominantly purple colors", "predominantly orange colors",
                         "black and white tones", "muted pastel colors", "bright saturated colors",
                         "warm colors", "cool colors", "earthy browns and greens")},
    "lighting": {"label": "Lighting", "contexts": _VISUAL_CONTEXTS,
                 "template": "a {context[0]} of {context[1]}, with {level}",
                 "levels": ("soft diffuse daylight", "hard direct sunlight",
                            "backlighting creating a silhouette", "dramatic side lighting",
                            "bright even studio lighting", "dim low-key lighting",
                            "warm golden-hour lighting", "cool nighttime lighting")},
}


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class FocusEngine:
    def __init__(self, settings, client):
        self.s, self.client = settings, client
        self.searcher = Searcher(settings, client)
        self.lock = threading.Lock()
        self.bases = {}
        self.images = OrderedDict()
        self.library_cache = {}
        self.research_profiles = None
        self.research_lock = threading.Lock()
        self.style_profiles = None

    def trained_styles(self):
        if _np is None:raise ValueError('Trained style search requires NumPy; install the version requirements')
        with self.research_lock:
            if self.style_profiles is None:
                from .style_profiles import StyleProfiles
                self.style_profiles=StyleProfiles(self.s,self.client)
        return self.style_profiles

    def style_status(self):
        try:return dict(self.trained_styles().status(),research_available=not getattr(self.s,'bundled_q8',False))
        except (ValueError,OSError,ImportError,KeyError) as exc:
            return {'available':False,'verified':False,'default_method':'baseline',
                    'research_available':not getattr(self.s,'bundled_q8',False),'error':str(exc)}

    def calibrate(self, focus="style"):
        if focus not in FOCUS_GROUPS:
            raise ValueError("unknown focus aspect")
        with self.lock:
            if focus in self.bases:
                return self.bases[focus]
            group = FOCUS_GROUPS[focus]
            contexts, levels = group["contexts"], group["levels"]
            texts = [group["template"].format(level=level, context=context)
                     for level in levels for context in contexts]
            vectors = [l2norm(v) for v in self.client.embed(
                [self.s.with_prefix_query(t) for t in texts], batch=4)]
            dim = len(vectors[0])
            if any(len(v) != dim or not all(math.isfinite(x) for x in v) for v in vectors):
                raise ValueError("model returned invalid calibration embeddings")
            means = [[sum(vectors[i * len(contexts) + j][d]
                          for j in range(len(contexts))) / len(contexts)
                      for d in range(dim)] for i in range(len(levels))]
            # Contrasts cancel the subject shared across each paired description.
            basis = []
            for mean in means[1:]:
                residual = [a - b for a, b in zip(mean, means[0])]
                # Reorthogonalize to avoid numerical leakage between axes.
                for _ in range(2):
                    for axis in basis:
                        c = dot(residual, axis)
                        residual = [x - c * y for x, y in zip(residual, axis)]
                if dot(residual, residual) > 1e-10:
                    basis.append(l2norm(residual))
            if not basis:
                raise ValueError(f"the model did not produce distinguishable {focus} directions")
            self.bases[focus] = basis
            return basis

    def channels(self, vector, focus="style"):
        basis = self.bases[focus]
        vector = l2norm(vector)
        coords = [dot(vector, axis) for axis in basis]
        residual = [x - sum(c * axis[d] for c, axis in zip(coords, basis))
                    for d, x in enumerate(vector)]
        return l2norm(residual), l2norm(coords), dot(coords, coords)

    def image_vector(self, image):
        if not isinstance(image, dict):
            raise ValueError("upload a query image")
        try:
            raw = base64.b64decode(image.get("base64", ""), validate=True)
        except (ValueError, TypeError) as e:
            raise ValueError("invalid image data") from e
        if not raw or len(raw) > 25 * 1048576:
            raise ValueError("choose an image smaller than 25 MB")
        mime = image.get("mime", "image/png")
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"):
            raise ValueError("use JPEG, PNG, WebP, GIF or BMP")
        key = (hashlib.sha256(raw).digest(), mime)
        with self.lock:
            if key in self.images:
                self.images.move_to_end(key)
                return self.images[key]
        vector = self.searcher.query_vector(image_bytes=raw, image_mime=mime)
        with self.lock:
            self.images[key] = vector
            while len(self.images) > 32:
                self.images.popitem(last=False)
        return vector

    def candidates(self, index, focus="style"):
        # Reload only when the web server has loaded a new Index object.
        with self.lock:
            self.library_cache = {key: value for key, value in self.library_cache.items()
                                  if value[0] is index}
            cached = self.library_cache.get(focus)
            if cached is not None and cached[0] is index:
                return cached[1]
        if focus != "style":
            # Share original and subject matrices instead of retaining one copy
            # of the entire library for each additional aspect.
            base = self.candidates(index)
            if base["numpy"]:
                coords = base["vectors"] @ _np.asarray(self.bases[focus], dtype=_np.float64).T
                coords /= _np.maximum(_np.linalg.norm(coords, axis=1, keepdims=True), 1e-12)
            else:
                coords = [l2norm([dot(v, axis) for axis in self.bases[focus]])
                          for v in base["vectors"]]
            rows = dict(base, styles=coords)
            with self.lock:
                self.library_cache[focus] = (index, rows)
            return rows
        records, vectors = [], []
        seen = set()
        for record, vector in zip(index.records, index.vectors):
            path = record.get("path", "")
            if record.get("kind") != "image" or path in seen:
                continue
            seen.add(path)
            records.append(record)
            vectors.append(vector)
        if _np is not None and vectors:
            matrix = _np.asarray(vectors, dtype=_np.float64)
            matrix /= _np.maximum(_np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
            axes = _np.asarray(self.bases[focus], dtype=_np.float64)
            styles = matrix @ axes.T
            subjects = matrix - styles @ axes
            subjects /= _np.maximum(_np.linalg.norm(subjects, axis=1, keepdims=True), 1e-12)
            styles /= _np.maximum(_np.linalg.norm(styles, axis=1, keepdims=True), 1e-12)
            rows = {"records": records, "vectors": matrix,
                    "subjects": subjects, "styles": styles, "numpy": True}
        else:
            channels = [self.channels(v, focus) for v in vectors]
            rows = {"records": records, "vectors": [l2norm(v) for v in vectors],
                    "subjects": [c[0] for c in channels],
                    "styles": [c[1] for c in channels], "numpy": False}
        with self.lock:
            # Only the current index is retained for each aspect.
            self.library_cache[focus] = (index, rows)
        return rows

    def search(self, payload, index):
        started = time.monotonic()
        strength = float(payload.get("strength", 1.0))
        if not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError("strength must be between 0 and 1")
        focus = payload.get("focus", "style")
        if not isinstance(focus, str) or focus not in FOCUS_GROUPS:
            raise ValueError("focus must be style, pose, viewpoint, composition, color or lighting")
        k = max(1, min(48, int(payload.get("k", 12))))
        path = payload.get("path")
        if path:
            # Reuse indexed vectors; arbitrary file paths are never read.
            vector = next((v for r, v in zip(index.records, index.vectors)
                           if r.get("path") == path and r.get("kind") == "image"), None)
            if vector is None:
                raise ValueError("query image is not in this index")
        else:
            vector = self.image_vector(payload.get("image"))
        self.calibrate()
        basis = self.calibrate(focus)
        if index.records and index.dim != len(vector):
            raise ValueError("query and library dimensions differ; use the matching model/index")
        if len(basis[0]) != len(vector):
            raise ValueError("query and calibration dimensions differ")
        q = l2norm(vector)
        qs, _, _ = self.channels(q)
        _, qt, energy = self.channels(q, focus)
        rows = self.candidates(index, focus)
        if rows["numpy"]:
            originals = rows["vectors"] @ _np.asarray(q)
            subject_scores = rows["subjects"] @ _np.asarray(qs)
            style_scores = rows["styles"] @ _np.asarray(qt)
        else:
            originals = [dot(q, v) for v in rows["vectors"]]
            subject_scores = [dot(qs, v) for v in rows["subjects"]]
            style_scores = [dot(qt, v) for v in rows["styles"]]
        ranked = {"original": [], "subject": [], focus: []}
        root = payload.get("root") or ""
        if root and root not in index.roots:
            raise ValueError("unknown library folder")
        count = 0
        for i, record in enumerate(rows["records"]):
            candidate_path = record.get("path", "")
            if path and candidate_path == path:
                continue
            if root and not Path(candidate_path).is_relative_to(Path(root)):
                continue
            count += 1
            original = float(originals[i])
            scores = {"original": original,
                      "subject": (1 - strength) * original + strength * float(subject_scores[i]),
                      focus: (1 - strength) * original + strength * float(style_scores[i])}
            common = {"name": Path(candidate_path).name, "path": candidate_path,
                      "thumb": "/thumb?path=" + quote(candidate_path),
                      "open": "/file?path=" + quote(candidate_path),
                      "original": round(original, 4)}
            for mode, score in scores.items():
                ranked[mode].append(dict(common, score=round(score, 4)))
        for mode in ranked:
            ranked[mode] = sorted(ranked[mode], key=lambda r: r["score"], reverse=True)[:k]
        return {"rankings": ranked, "count": count, "strength": strength,
                "focus": focus, "label": FOCUS_GROUPS[focus]["label"],
                "axes": len(basis), "seconds": round(time.monotonic() - started, 2),
                "warning": (f"Very little {FOCUS_GROUPS[focus]['label'].lower()} signal detected; results may be unstable."
                            if energy < 1e-6 else ""),
                "note": ("Experimental text-learned directions; aspects can overlap. "
                         + ("Pose matches broad body configurations, not exact joint positions."
                            if focus == "pose" else "Subject focus reduces style differences."))}

    def search_gallery(self, payload, index, *, roots=None, k=24, offset=0):
        """Main GUI aspect search, including text blending, negatives and paging.

        The aspect score is the same one used in the comparison demo. Text and
        exclusions retain their ordinary semantic meaning in the original space.
        With mixed text/image queries, strength interpolates between ordinary
        fused-query cosine and a weighted aspect/text cosine. Both are bounded
        similarities; strength=0 reproduces the ordinary image/text blend.
        """
        started = time.monotonic()
        focus = payload.get("focus", "original")
        if focus not in ("original", "subject", *FOCUS_GROUPS):
            raise ValueError("unknown image aspect")
        method = payload.get("method", "baseline")
        if 'method' not in payload and focus == 'style':
            method = self.style_status().get('default_method', 'baseline')
        if method not in ("baseline", "pisco", "model_aware", "style_original", "style_current", "style_contrastive"):
            raise ValueError("unknown aspect method")
        if method.startswith("style_") and focus != "style":
            raise ValueError("Trained style methods are available in Style mode only")
        trained_style=method in ("style_current","style_contrastive")
        native_style=method == "style_original"

        def weight(name, default, maximum=1):
            value = float(payload.get(name, default))
            if not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"{name} must be between 0 and {maximum}")
            return value

        strength = weight("focus_strength", 1)
        alpha = weight("img_weight", 0.5)
        nw = weight("not_weight", 1, 2)
        niw = weight("neg_image_weight", 1, 2)
        path = payload.get("path")
        if path:
            vector = next((v for r, v in zip(index.records, index.vectors)
                           if r.get("path") == path and r.get("kind") == "image"), None)
            if vector is None:
                raise ValueError("reference image is no longer in this index")
        else:
            vector = self.image_vector(payload)
        q = l2norm(vector)

        def text_list(value):
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise ValueError("text queries must be strings")
            return [x.strip() for x in value if x.strip()]

        def embed_texts(texts):
            vectors = [l2norm(v) for v in self.client.embed(
                [self.s.with_prefix_query(t) for t in texts])]
            for v in vectors:
                if len(v) != index.dim or not all(math.isfinite(x) for x in v):
                    raise ValueError("query and library dimensions differ or embeddings are invalid")
            return vectors

        if len(q) != index.dim or not all(math.isfinite(x) for x in q):
            raise ValueError("query and library dimensions differ; use the matching model/index")
        wants = text_list(payload.get("q", "")) + text_list(payload.get("wants", []))
        if not wants:
            alpha = 1.0
        text_vectors = embed_texts(wants) if wants and alpha < 1 else []
        text_mean = (l2norm([sum(v[d] for v in text_vectors) / len(text_vectors)
                            for d in range(index.dim)]) if text_vectors else None)
        excludes = text_list(payload.get("not", []))
        negatives = embed_texts(excludes) if excludes and nw else []
        negative_mean = ([sum(v[d] for v in negatives) / len(negatives)
                          for d in range(index.dim)] if negatives else None)
        neg_image = self.image_vector(payload["neg_image"]) if payload.get("neg_image") and niw else None
        if neg_image is not None and len(neg_image) != index.dim:
            raise ValueError("ignore-image and library dimensions differ")

        # A zero-strength focus needs no calibration, including on a cold server.
        basis_focus = "style" if focus == "subject" else focus
        focused = focus != "original" and not native_style and strength > 0 and alpha > 0
        energy = 1.0
        if focused and not trained_style:
            self.calibrate("style")
            basis = self.calibrate(basis_focus)
            if len(basis[0]) != index.dim:
                raise ValueError("calibration and library dimensions differ")
            rows = self.candidates(index, basis_focus)
            residual, coords, energy = self.channels(q, basis_focus)
            if focus == "subject":
                energy = max(0.0, 1.0 - energy)
            channel_query = residual if focus == "subject" else coords
            channel_vectors = rows["subjects"] if focus == "subject" else rows["styles"]
        else:
            with self.lock:
                cached = next((self.library_cache[key] for key in ("style", "_original")
                               if key in self.library_cache and self.library_cache[key][0] is index), None)
            if cached:
                rows = cached[1]
            else:
                records, vectors, seen = [], [], set()
                for r, v in zip(index.records, index.vectors):
                    if r.get("kind") == "image" and r.get("path") not in seen:
                        seen.add(r.get("path")); records.append(r); vectors.append(l2norm(v))
                if _np is not None and vectors:
                    vectors = _np.asarray(vectors, dtype=_np.float64)
                rows = {"records": records, "vectors": vectors, "numpy": _np is not None and bool(records)}
                with self.lock:
                    self.library_cache = {key: value for key, value in self.library_cache.items() if value[0] is index}
                    self.library_cache["_original"] = (index, rows)

        def scores(v, matrix=None):
            matrix = rows["vectors"] if matrix is None else matrix
            if rows["numpy"]:
                return matrix @ _np.asarray(v)
            return [dot(v, row) for row in matrix]

        original_scores = scores(q)
        aspect_scores = scores(channel_query, channel_vectors) if focused and not trained_style else original_scores
        method_note = ""
        method_applied = "baseline"
        if native_style:
            method_applied=method
            method_note="Original embeddings match overall similarity, including subject and style."
        elif focused and trained_style:
            aspect_scores,method_note=self.trained_styles().score(q,rows,index,method)
            method_applied=method
        elif focused and method != "baseline":
            if _np is None:
                raise ValueError("Experimental research profiles require NumPy; choose the existing method")
            with self.research_lock:
                if self.research_profiles is None:
                    from .research_channels import ResearchChannels
                    self.research_profiles = ResearchChannels(self.s, self.client)
            aspect_scores, profile, method_note = self.research_profiles.score(q, rows, index, method, focus, aspect_scores)
            method_applied = method
        text_scores = scores(text_mean) if text_mean else None
        if alpha == 0 and payload.get("combine", "avg") == "max" and text_vectors:
            alternatives = [scores(v) for v in text_vectors]
            text_scores = [max(v[i] for v in alternatives) for i in range(len(rows["records"]))]
        negative_scores = scores(negative_mean) if negative_mean else None
        negative_image_scores = scores(neg_image) if neg_image is not None else None
        fused_norm = (sum((alpha * a + (1 - alpha) * b) ** 2
                          for a, b in zip(q, text_mean)) ** 0.5 if text_mean else 1.0) or 1.0
        kindset = set(payload.get("kind") or [])
        hits = []
        for i, r in enumerate(rows["records"]):
            candidate_path = r.get("path", "")
            if path and candidate_path == path:
                continue
            if kindset and "image" not in kindset:
                continue
            if roots and not any(Path(candidate_path).is_relative_to(Path(root)) for root in roots):
                continue
            original = float(original_scores[i])
            ordinary = (alpha * original + (1 - alpha) * float(text_scores[i])) / fused_norm if text_scores is not None else original
            if focused:
                aspect = float(aspect_scores[i])
                targeted = alpha * aspect + (1 - alpha) * float(text_scores[i]) if text_scores is not None else aspect
                positive = (1 - strength) * ordinary + strength * targeted
            else:
                positive = ordinary
            penalty = (nw * float(negative_scores[i]) if negative_scores is not None else 0)
            penalty += niw * float(negative_image_scores[i]) if negative_image_scores is not None else 0
            hits.append((positive - penalty, positive, penalty, r))
        hits.sort(key=lambda h: h[0], reverse=True)
        page = hits[offset:offset + k]
        results = [dict(r, score=round(score, 4), pos=round(pos, 4), penalty=round(pen, 4),
                        thumb="/thumb?path=" + quote(r.get("path", "")),
                        open="/file?path=" + quote(r.get("path", "")))
                   for score, pos, pen, r in page]
        label = "Subject" if focus == "subject" else FOCUS_GROUPS.get(focus, {}).get("label", "Similarity")
        if native_style:label="Original similarity"
        return {"results": results, "more": len(hits) > offset + k, "count": len(hits),
                "focus": focus, "label": label, "seconds": round(time.monotonic() - started, 2),
                "method": method_applied, "method_note": method_note,
                "warning": f"Very little {label.lower()} signal detected; results may be unstable." if focused and energy < 1e-6 else ""}
