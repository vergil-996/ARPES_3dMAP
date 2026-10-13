"""Small synthetic contexts for independent display-region tests."""
import copy

import numpy as np

from bandscope.core.local_enhancement import DEFAULT_STYLE, EnhancementRegion


def context_2d(data=None, *, axis=2):
    data = np.arange(36, dtype=np.float32).reshape(6, 6) if data is None else np.asarray(data, dtype=np.float32)
    keys = [key for i, key in enumerate(("X", "Y", "E")) if i != axis]
    coords = {key: np.arange(6, dtype=float) for key in ("X", "Y", "E")}
    for key, size in zip(keys, data.shape):
        coords[key] = np.arange(size, dtype=float)
    return {"view": "2d", "data": data, "coords": coords,
            "slice_info": {"axis": axis, "index": 0}}


def context_3d():
    data = np.arange(6*5*4, dtype=np.float32).reshape(6, 5, 4)
    return {"view": "3d", "data": data,
            "coords": {key: np.arange(size, dtype=float) for key, size in zip(("X", "Y", "E"), data.shape)}}


def region(number, bounds, *, view="2d", axes=("X", "Y"), **updates):
    result = EnhancementRegion(str(number), f"Region {number}", view, axes, tuple(bounds)).to_dict()
    result.update(style=copy.deepcopy(DEFAULT_STYLE), label=f"R{number}")
    result.update(updates)
    return result


def state_2d():
    return {"regions": [region(1, (0, 1, 0, 5)), region(2, (2, 3, 0, 5))],
            "background": copy.deepcopy(DEFAULT_STYLE), "selected_id": "1",
            "revision": 1, "next_label": 3, "common": False}
