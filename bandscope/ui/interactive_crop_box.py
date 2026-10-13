"""Persistent, axis-aligned crop draft for Matplotlib result views.

RectangleSelector supplies handle hit testing and shared blitting. The small
overrides below keep a draft alive, prohibit drawing a replacement rectangle,
and constrain translation as a whole rather than clipping individual edges.
"""
import copy

import numpy as np
from matplotlib import patheffects
from matplotlib.widgets import RectangleSelector

from bandscope.ui import theme


class InteractiveCropBox(RectangleSelector):
    def __init__(self, ax, onselect, bounds):
        super().__init__(
            ax, onselect, useblit=True, button=[1], interactive=True,
            drag_from_anywhere=True, ignore_event_outside=True, grab_range=10,
            props={"facecolor": theme.ACCENT, "edgecolor": theme.ACCENT,
                   "alpha": 1.0, "fill": False, "linewidth": 2.0,
                   "linestyle": "-", "zorder": 100,
                   "path_effects": [patheffects.Stroke(linewidth=4, foreground=theme.BG_2),
                                    patheffects.Normal()]},
            handle_props={"markerfacecolor": theme.ACCENT,
                          "markeredgecolor": theme.BG_2, "markeredgewidth": 1.2,
                          "markersize": 8, "alpha": 1.0, "zorder": 101},
            state_modifier_keys={"move": "__disabled__"},
        )
        self.sync_bounds(bounds)

    def sync_bounds(self, bounds):
        """Update geometry in place without rewriting the numeric draft."""
        self._valid_bounds = tuple(bounds)
        self._selection_completed = True
        self.set_visible(True)
        if self.extents != tuple(bounds):
            self.extents = bounds

    def _draw_shape(self, extents):
        # Numeric bounds may exceed the axes. Artist clipping is display-only;
        # the default RectangleSelector instead mutates the actual extents.
        x0, x1, y0, y1 = extents
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        self._selection_artist.set_bounds(x0, y0, x1 - x0, y1 - y0)

    def press(self, event):
        if self.ignore(event):
            return False
        self._set_active_handle(event)
        if self._active_handle is None:
            return False
        return super().press(event)

    def ignore(self, event):
        ignored = super().ignore(event)
        if (ignored and self.active and self.ax.get_visible()
                and self.canvas.widgetlock.available(self)
                and getattr(event, "button", None) in self.validButtons
                and self._eventpress is None and self.get_visible()):
            # Full-range handles sit on the axes border. Pixel rounding can
            # put the press just outside it; retain their normal grab radius.
            self._set_active_handle(event)
            return self._active_handle is None
        return ignored

    def _clean_event(self, event):
        # Continue to the axes edge when the pointer leaves the plot instead
        # of using Matplotlib's previous in-axes motion event.
        if event.xdata is None:
            event = copy.copy(event)
            event.xdata, event.ydata = self.ax.transData.inverted().transform(
                (event.x, event.y))
            event.inaxes = self.ax
        return super()._clean_event(event)

    def _onmove(self, event):
        if self._active_handle != "C":
            return super()._onmove(event)
        x0, x1, y0, y1 = self._extents_on_press
        # onmove has already cleaned the event into this axes' data coordinates.
        # AxesWidget._get_data_coords was removed in Matplotlib 3.11.
        x, y = event.xdata, event.ydata
        dx, dy = x - self._eventpress.xdata, y - self._eventpress.ydata
        shifts = []
        for delta, low, high, limits in (
            (dx, x0, x1, self.ax.get_xlim()),
            (dy, y0, y1, self.ax.get_ylim()),
        ):
            lower, upper = sorted(limits)
            # An oversized numeric draft cannot fit; preserve its size and
            # permit only movement that does not increase the overflow.
            lower, upper = min(lower, low), max(upper, high)
            shifts.append(float(np.clip(delta, lower - low, upper - high)))
        dx, dy = shifts
        self.extents = x0 + dx, x1 + dx, y0 + dy, y1 + dy

    def _release(self, event):
        x0, x1, y0, y1 = self.extents
        eps = np.finfo(float).eps * 32
        if (x1 - x0 <= eps * max(1, abs(x0), abs(x1))
                or y1 - y0 <= eps * max(1, abs(y0), abs(y1))):
            self.extents = self._valid_bounds
            self._active_handle = None
            self._extents_on_press = None
            return False
        result = super()._release(event)
        self._valid_bounds = self.extents
        return result

    def on_key_press(self, event):
        # Crop mode's Qt event filter owns Esc; modifier shortcuts must not
        # rotate, clear, or force a new gesture on this persistent rectangle.
        pass

    def on_key_release(self, event):
        pass

    def dispose(self):
        self.set_active(False)
        self.set_visible(False)
        self.disconnect_events()
        for artist in self.artists:
            if artist.axes is not None:
                artist.remove()
