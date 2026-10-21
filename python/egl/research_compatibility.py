"""Check exported profiles by embedding behavior, independent of server paths."""
from __future__ import annotations
import hashlib
from pathlib import Path
import numpy as np
from .api import image_item
from .focus import FOCUS_GROUPS

# Same full-frame pilot inputs as the frozen calibration: photo, painting, pose.
IMAGE_PROBE_INDICES = (0, 13, 20)
MINIMUM_COSINE = 0.9999


def package_probes(root, records, source):
    """Copy only the three compatibility inputs into the portable release."""
    destination = Path(root) / 'probes'
    destination.mkdir(parents=True, exist_ok=True)
    for index in IMAGE_PROBE_INDICES:
        record = records[index]
        raw = (Path(source) / record['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('Research reference image changed')
        (destination / (record['id'] + '.jpg')).write_bytes(raw)


def probe_cosine(live, expected, label, minimum_cosine=MINIMUM_COSINE):
    live, expected = np.asarray(live, dtype=float), np.asarray(expected, dtype=float)
    if live.shape != expected.shape or live.ndim != 1:
        raise ValueError(f'Research embedding dimensions differ for {label}: '
                         f'live {live.shape}, expected {expected.shape}')
    if not np.isfinite(live).all() or not np.isfinite(expected).all():
        raise ValueError(f'Research compatibility probe returned invalid values for {label}')
    live_norm, expected_norm = np.linalg.norm(live), np.linalg.norm(expected)
    if live_norm == 0 or expected_norm == 0:
        raise ValueError(f'Research compatibility probe returned a zero vector for {label}')
    cosine = float(np.dot(live / live_norm, expected / expected_norm))
    if cosine < minimum_cosine:
        raise ValueError(f'Research profiles are incompatible with the live embeddings '
                         f'({label}: cosine {cosine:.6f}, requires {minimum_cosine}). '
                         'Check the model, vision projector, precision and server settings; '
                         'use the existing method until they match.')
    return cosine


def verify_profiles(root, client, records, inputs):
    """Verify three text and three image vectors; metadata is provenance only."""
    scores = {}
    for prefix, focus in [('search result', 'style'), ('sentence similarity', 'style'),
                          ('search result', 'pose')]:
        group = FOCUS_GROUPS[focus]
        text = f'task: {prefix} | query: ' + group['template'].format(
            level=group['levels'][0], context=group['contexts'][0])
        label = f'text {prefix}/{focus}'
        scores[label] = probe_cosine(client.embed_one(text), inputs[prefix+'/'+focus][0], label)
    for index in IMAGE_PROBE_INDICES:
        record = records[index]
        path = Path(root) / 'probes' / (record['id']+'.jpg')
        if not path.is_file():
            raise ValueError('Research compatibility images are missing. Copy the updated '
                             'v1.8B/research/probes folder along with the application.')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError(f'Research compatibility image changed: {path.name}')
        label = 'image '+record['id']
        scores[label] = probe_cosine(client.embed_one(image_item(raw, 'image/jpeg')),
                                    inputs['native'][index], label)
    return {'minimum_cosine': MINIMUM_COSINE, 'scores': scores}
