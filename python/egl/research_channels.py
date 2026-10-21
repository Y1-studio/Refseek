"""Load frozen v1.8B research profiles without retraining or index writes."""
from __future__ import annotations
import hashlib, json, threading
from pathlib import Path
import numpy as np
from .paired import unit
from .research_compatibility import verify_profiles


class ResearchChannels:
    def __init__(self, settings, client):
        self.root=Path(__file__).resolve().parent.parent/'research'
        selection_path=self.root/'selection.json'
        freeze=json.loads((self.root/'selection-freeze.json').read_text())
        if hashlib.sha256(selection_path.read_bytes()).hexdigest()!=freeze['selection_sha256']:
            raise ValueError('Research selection changed after evaluation')
        if hashlib.sha256((self.root/'adapters.npz').read_bytes()).hexdigest()!=freeze['adapters_sha256']:
            raise ValueError('Research adapter weights changed after evaluation')
        for name in ('protocol', 'inputs'):
            suffix = '.json' if name == 'protocol' else '.npz'
            if hashlib.sha256((self.root/(name+suffix)).read_bytes()).hexdigest()!=freeze[name+'_sha256']:
                raise ValueError('Research calibration inputs changed after evaluation')
        self.options=json.loads(selection_path.read_text())['experimental_options']
        self.arrays=np.load(self.root/'adapters.npz',allow_pickle=False)
        manifest=json.loads((self.root/'manifest.json').read_text())
        with np.load(self.root/'inputs.npz',allow_pickle=False) as inputs:
            self.compatibility=verify_profiles(self.root, client, manifest['records'], inputs)
        self.cache={};self.lock=threading.Lock()

    def features(self, x, method, focus):
        entry=self.options[method+'/'+focus]; key=(method+'/'+focus).replace('/','_')
        x=unit(x)
        if method=='pisco':
            projection=self.arrays[key+'_projection']
            if x.shape[-1]!=projection.shape[-1]:raise ValueError('Adapter and image dimensions differ')
            return unit(x@projection.T)
        dim=entry['config']['dim'];axes=self.arrays[key+'_basis']
        x=unit(x[..., :dim]);coords=x@axes.T
        return unit(x-coords@axes) if focus=='subject' else unit(coords)

    def score(self, q, rows, index, method, focus, baseline):
        if method not in ['pisco','model_aware']:raise ValueError('unknown research method')
        option=method+'/'+focus
        if option not in self.options:raise ValueError(f'{method} is not available for {focus}; choose the existing method')
        with self.lock:
            self.cache={k:v for k,v in self.cache.items() if v[0] is index}
            cached=self.cache.get(option)
        if cached is None:
            matrix=self.features(np.asarray(rows['vectors']),method,focus)
            with self.lock:self.cache[option]=(index,matrix)
        else:matrix=cached[1]
        values=matrix@self.features(np.asarray(q),method,focus)
        entry=self.options[option]
        if method=='pisco':
            blend=entry['config']['blend'];values=(1-blend)*np.asarray(baseline)+blend*values
        notes={
            'pisco/subject':'Experimental paired-image content adapter. Pilot AP +1.7 points, top-result accuracy -3.1 points; fresh confirmation pending.',
            'pisco/style':'Experimental synthetic appearance adapter. Pilot style AP -0.3 points; it is not a proven improvement.',
            'model_aware/style':'Experimental model-specific calibration. Pilot style AP +0.2 points; fresh confirmation pending.',
            'model_aware/subject':'Experimental model-specific calibration. Pilot subject AP -0.3 points; it is not a proven improvement.',
            'model_aware/pose':'Experimental model-specific calibration. Pilot pose AP -4.1 points; broad-pose AP -4.6 points. Existing pose search remains recommended.'}
        return values,entry,notes[option]
