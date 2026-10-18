"""Colormaps that evaluate the display curve before palette quantization."""

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import Colormap


class LeveledColormap(Colormap):
    """Apply Gamma to floating-point coordinates, then sample the full palette.

    A sampled Gamma palette quantizes the input before applying the curve.
    In particular, all inputs below 1/256 otherwise share the first color,
    even when Gamma should make their differences visible.  Only the final
    palette lookup is discrete here; the intensity coordinate stays precise.
    """

    def __init__(self, base_cmap, level_info, *, name=None):
        palette = colormaps.get_cmap(base_cmap) if isinstance(base_cmap, str) else base_cmap
        if not isinstance(palette, Colormap):
            raise TypeError("base_cmap must be a Matplotlib colormap or its name.")
        power = float(level_info["gamma_power"])
        if not np.isfinite(power) or power <= 0:
            raise ValueError("Gamma power must be finite and positive.")
        super().__init__(name or f"{palette.name}_levels_{power:.4g}", N=palette.N)
        self.base_cmap = palette.copy()
        self.level_info = dict(level_info)
        self.gamma_power = power
        self._reverse = False
        self._rgba_bad = tuple(palette.get_bad())
        self._rgba_under = tuple(palette.get_under())
        self._rgba_over = tuple(palette.get_over())
        self.colorbar_extend = palette.colorbar_extend

    def _positions(self, values):
        clipped = np.clip(values, 0.0, 1.0)
        if self._reverse:
            clipped = 1.0 - clipped
        return np.power(clipped, self.gamma_power)

    def _init(self):
        # Matplotlib also uses the LUT for inspection and colorbar metadata.
        # Scalar-to-color evaluation below deliberately bypasses this LUT.
        self._lut = np.empty((self.N + 3, 4), dtype=np.float64)
        self._lut[:self.N] = self.base_cmap(self._positions(np.linspace(0.0, 1.0, self.N)))
        self._isinit = True
        self._set_extremes()

    def _set_extremes(self):
        super()._set_extremes()
        self.base_cmap.set_extremes(
            bad=self._rgba_bad, under=self._rgba_under, over=self._rgba_over,
        )

    def _get_rgba_and_mask(self, X, alpha=None, bytes=False):
        if not self._isinit:
            self._init()
        source = np.ma.asarray(X)
        raw = np.asarray(np.ma.getdata(source))
        values = raw.astype(np.float64)
        if raw.dtype.kind in "biu":
            # Retain Colormap's integer-index convention. Floating inputs are
            # continuous coordinates and never pass through these indices.
            values = values / max(self.N - 1, 1)
            over = raw >= self.N
        else:
            over = values > 1.0
        bad = np.ma.getmaskarray(source) | np.isnan(values)
        positions = self._positions(values)
        positions = np.where(values < 0.0, -1.0, np.where(over, 2.0, positions))
        mapped = np.ma.array(positions, mask=bad, copy=False)
        rgba = self.base_cmap(mapped, alpha=alpha, bytes=bytes)
        return np.asarray(rgba), bad

    def __call__(self, X, alpha=None, bytes=False):
        rgba, _ = self._get_rgba_and_mask(X, alpha=alpha, bytes=bytes)
        return rgba if np.iterable(X) else tuple(rgba)

    def __copy__(self):
        result = super().__copy__()
        result.base_cmap = self.base_cmap.copy()
        result.level_info = dict(self.level_info)
        return result

    def resampled(self, lutsize):
        if not self._isinit:
            self._init()
        result = type(self)(self.base_cmap.resampled(lutsize), self.level_info, name=self.name)
        result._reverse = self._reverse
        return result

    def reversed(self, name=None):
        result = type(self)(self.base_cmap, self.level_info, name=name or f"{self.name}_r")
        result._reverse = not self._reverse
        result._rgba_bad = tuple(self.get_bad())
        result._rgba_under = tuple(self.get_over())
        result._rgba_over = tuple(self.get_under())
        return result
