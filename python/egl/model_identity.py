"""Research cache namespace, not a portable model compatibility identity.

Excludes llama.cpp's per-request timestamps but intentionally keeps provenance
such as model paths. GUI compatibility uses live text/vision probes instead.
"""
from __future__ import annotations
import hashlib, json


def model_identity(info):
    def stable(value):
        if isinstance(value, dict):
            return {k:stable(v) for k,v in value.items() if k not in ['created','modified_at']}
        if isinstance(value,list):return [stable(x) for x in value]
        return value
    return hashlib.sha256(json.dumps(stable(info),sort_keys=True).encode()).hexdigest()[:20]
