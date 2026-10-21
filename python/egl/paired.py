"""PISCO-inspired post-processing of frozen EmbeddingGemma 2 vectors.

Appearance factors use ridge regression on controlled same-frame variants.
Content factors retain variance while penalizing paired appearance changes
(PISCO equation 3.3). No base-model training, file cropping or index writes.
The synthetic variants are appearance interventions, not real artistic media.
"""
from __future__ import annotations
import numpy as np


def unit(x):
    x = np.asarray(x, dtype=np.float64)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12)


def fit_pairs(original, variants, *, ridge=0.1, penalty=1.0, rank=64):
    original, variants = unit(original), unit(variants)
    n, d = original.shape
    if variants.ndim != 3 or variants.shape[1:] != (n, d):
        raise ValueError("expected variants with shape (factors, images, dimension)")
    if not np.isfinite(original).all() or not np.isfinite(variants).all():
        raise ValueError("non-finite paired embeddings")
    factors = []
    for transformed in variants:
        x = np.concatenate([original, transformed])
        mean = x.mean(axis=0)
        xc = x - mean
        y = np.concatenate([-np.ones(n), np.ones(n)])
        # Dual ridge solve keeps the small-data fit inexpensive and regularized.
        coefficient = xc.T @ np.linalg.solve(xc @ xc.T + float(ridge) * len(x) * np.eye(len(x)), y)
        factors.append(coefficient)
    u = np.concatenate([original, *variants])
    delta = variants - original[None, :, :]
    covariance = u.T @ u / len(u)
    nuisance = np.einsum("mnd,mne->de", delta, delta) / (len(variants) * n)
    values, vectors = np.linalg.eigh(covariance - float(penalty) * nuisance)
    rank = min(int(rank), d, len(u))
    content = vectors[:, np.argsort(values)[::-1][:rank]].T
    return {"style": np.asarray(factors), "subject": content,
            "input_dim": d, "ridge": float(ridge), "penalty": float(penalty), "rank": rank}


def transform(x, model, task):
    return unit(unit(x) @ model[task].T)
