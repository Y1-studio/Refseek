"""Inference for covariance and warm contrastive artist-reference metrics."""
from __future__ import annotations
import json
import numpy as np
from .paired import unit


class ArtistStyleMetric:
    def __init__(self, path):
        with np.load(path, allow_pickle=False) as data:
            self.config=json.loads(str(data['config']))
            self.model={key:data[key].copy() for key in data.files if key!='config'}
        self.warm=self.config.get('method')=='warm_contrastive'
        required=({'mean','axes','correction','warm_mean','warm_axes','warm_scales'} if self.warm else {'mean','axes','scales'})
        if (not self.warm and self.config.get('kind')!='within_class') or set(self.model)!=required:
            raise ValueError('Unsupported artist adapter')
        if not all(np.isfinite(a).all() for a in self.model.values()):raise ValueError('Invalid artist adapter values')
        self.mean=self.model['mean'];self.axes=self.model['axes']
        if self.mean.shape!=(768,) or self.axes.ndim!=2 or self.axes.shape[1]!=768:
            raise ValueError('Invalid artist adapter dimensions')
        if self.warm:
            m=self.model
            if (m['correction'].shape!=(len(self.axes),768) or m['warm_mean'].shape!=(768,)
                    or m['warm_axes'].ndim!=2 or m['warm_axes'].shape[1]!=768
                    or m['warm_scales'].shape!=(len(m['warm_axes']),)):
                raise ValueError('Invalid contrastive adapter dimensions')
        else:
            self.scales=self.model['scales']
            if self.scales.shape!=(len(self.axes),):raise ValueError('Invalid covariance adapter dimensions')
        self.weight=float(self.config['weight'])
        if not 0<=self.weight<=1:raise ValueError('Invalid artist adapter blend')

    def features(self, vectors):
        vectors=np.asarray(vectors,dtype=np.float64)
        if vectors.ndim not in (1,2) or vectors.shape[-1]!=768:
            raise ValueError('Artist adapter requires native 768d image embeddings')
        if not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors,axis=-1)==0):
            raise ValueError('Artist adapter embeddings must be finite and nonzero')
        vectors=unit(vectors)
        if self.warm:
            m=self.model
            centered=vectors-m['warm_mean']
            vectors=unit(centered+((centered@m['warm_axes'].T)*m['warm_scales'])@m['warm_axes'])
        centered=vectors-self.mean
        if self.warm:return unit(centered+(centered@self.axes.T)@self.model['correction'])
        return unit(centered+((centered@self.axes.T)*self.scales)@self.axes)

    def score(self, query, gallery):
        query,gallery=unit(query),unit(gallery)
        return (1-self.weight)*(gallery@query)+self.weight*(self.features(gallery)@self.features(query))
