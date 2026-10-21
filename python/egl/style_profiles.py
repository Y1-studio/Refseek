"""Verified full-data style profiles, with portable live embedding probes."""
from __future__ import annotations
import hashlib, json, threading
from pathlib import Path
import numpy as np
from .api import image_item
from .artist_metric import ArtistStyleMetric
from .research_compatibility import probe_cosine

FILES={'deployment.json','artist-style-adapter.npz','experimental-style-adapter.npz',
       'fit-diagnostics.json','model-probes.json','probe-text.npy','probe-image.npy','probe-image.bin','README.txt'}
METHODS={'style_current':'artist-style-adapter.npz','style_contrastive':'experimental-style-adapter.npz'}


class StyleProfiles:
    def __init__(self, settings, client):
        self.root=Path(__file__).resolve().parent.parent/'style-models'
        self.settings,self.client=settings,client
        freeze=json.loads((self.root/'deployment-freeze.json').read_text(encoding='utf-8'))
        if set(freeze)!=FILES:raise ValueError('Style model bundle is incomplete; copy the entire style-models folder')
        for name,digest in freeze.items():
            if hashlib.sha256((self.root/name).read_bytes()).hexdigest()!=digest:
                raise ValueError(f'Style model bundle integrity check failed: {name}')
        self.deployment=json.loads((self.root/'deployment.json').read_text(encoding='utf-8'))
        self.preparation=json.loads((self.root/'import.json').read_text(encoding='utf-8'))
        if self.preparation['dim']!=768 or settings.dim not in (None,768):
            raise ValueError('Trained style search needs native 768d embeddings; set EG_DIM=768')
        if settings.image_max!=self.preparation['image_max']:
            raise ValueError(f'Trained style search needs EG_IMAGE_MAX={self.preparation["image_max"]} to match its training images')
        self.metrics={method:ArtistStyleMetric(self.root/name) for method,name in METHODS.items()}
        if (self.metrics['style_current'].config!=self.deployment['current_config']
                or self.metrics['style_contrastive'].config!=self.deployment['experimental_config']):
            raise ValueError('Style model configurations differ from the exported deployment recipes')
        self.probes=json.loads((self.root/'model-probes.json').read_text(encoding='utf-8'))
        if self.probes.get('dim')!=768:raise ValueError('Style model probes require 768d embeddings')
        self.compatibility=None;self.lock=threading.Lock();self.cache={}

    def verify(self):
        with self.lock:
            if self.compatibility is not None:return self.compatibility
            expected_text=np.load(self.root/'probe-text.npy',allow_pickle=False)
            expected_image=np.load(self.root/'probe-image.npy',allow_pickle=False)
            if expected_text.shape!=(768,) or expected_image.shape!=(768,):
                raise ValueError('Invalid style compatibility probe dimensions')
            raw=(self.root/'probe-image.bin').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=self.probes['image_sha256']:
                raise ValueError('Style compatibility image differs from its probe metadata')
            # Only the owned, hash-verified exact Q8 model gets this tolerance.
            # Measured Q8/BF16 image agreement across CPU, Vulkan and two CUDA
            # GPUs is 0.99979–0.99989. This checks space compatibility, not quality.
            minimum=0.9995 if getattr(self.settings,'bundled_q8',False) else 0.9999
            scores={
                'text':probe_cosine(self.client.embed_one(self.probes['text']),expected_text,'style text',minimum),
                'image':probe_cosine(self.client.embed_one(image_item(raw,self.probes['mime'])),expected_image,'style image',minimum)}
            self.compatibility=dict(minimum_cosine=minimum,scores=scores,
                                    quantization_transfer=minimum!=0.9999)
            return self.compatibility

    def status(self):
        return dict(available=True,verified=self.compatibility is not None,
                    default_method='style_contrastive',
                    images=self.deployment['image_count'],artists=self.deployment['artist_count'],
                    weights={method:metric.weight for method,metric in self.metrics.items()},
                    compatibility=self.compatibility,
                    quantization_transfer=bool(getattr(self.settings,'bundled_q8',False)))

    def score(self, query, rows, index, method):
        if method not in METHODS:raise ValueError('Unknown trained style method')
        if index.dim!=768:raise ValueError('Trained style search requires a library indexed with native 768d image embeddings')
        self.verify()
        metric=self.metrics[method]
        with self.lock:
            self.cache={name:entry for name,entry in self.cache.items() if entry[0] is index}
            cached=self.cache.get(method)
        if cached is None:
            # Only transformed image vectors are cached. The saved library stays native.
            native=np.asarray(rows['vectors'],dtype=np.float64).reshape(-1,768)
            matrix=np.empty_like(native)
            for start in range(0,len(native),512):matrix[start:start+512]=metric.features(native[start:start+512])
            with self.lock:self.cache[method]=(index,matrix)
        else:matrix=cached[1]
        native=np.asarray(rows['vectors'],dtype=np.float64).reshape(-1,768)@np.asarray(query)
        values=(1-metric.weight)*native+metric.weight*(matrix@metric.features(query))
        label='Current style adapter' if method=='style_current' else 'Contrastive style adapter'
        note=f'{label}: artist-reference matching fitted on {self.deployment["artist_count"]} artists. Its full-data weights need evaluation on fresh images.'
        return values,note
