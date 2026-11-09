import json
import math
from pathlib import Path


DIMENSIONS = (768, 512, 256, 128)
DEFAULT_DIMENSION = 768


def preferred_dimension(data_dir):
    try:
        value = json.loads((Path(data_dir) / 'search-settings.json').read_text(encoding='utf-8'))['dimension']
    except (OSError, ValueError, TypeError, KeyError):
        return DEFAULT_DIMENSION
    return value if type(value) is int and value in DIMENSIONS else DEFAULT_DIMENSION


def prefix_vector(vector, dimension):
    prefix = vector[:dimension]
    norm = math.sqrt(sum(x * x for x in prefix)) or 1.0
    return [x / norm for x in prefix]
