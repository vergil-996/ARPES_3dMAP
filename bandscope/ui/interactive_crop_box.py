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


class CropBoxGestureCoordinator:
    def __init__(self):
        self._selectors = []
        self._press_event = None
        self._target = None

    def set_selectors(self, selectors):
        self._selectors = list(selectors)
        if self._target not in self._selectors:
            self._press_event = None
            self._target = None

    def remove(self, selector):
        self._selectors = [candidate for candidate in self._selectors
                           if candidate is not selector]
        if self._target is selector:
            self._press_event = None
            self._target = None

    def claim_press(self, selector, event):
        if self._press_event is not event:
            self._press_event = event
            self._target = None
            for candidate in reversed(self._selectors):
                if candidate._matches_press(event):
                    self._target = candidate
                    break
        return self._target is selector

    def owns_gesture(self, selector):
        return self._target is selector

    def end_gesture(self, selector):
        if self._target is selector:
            self._press_event = None
            self._target = None


class InteractiveCropBox(RectangleSelector):
    def __init__(self, ax, onselect, bounds, on_activate=None, coordinator=None):
        self._on_activate = on_activate
        self._gesture_coordinator = coordinator
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

    def update_theme(self):
        self._selection_artist.set(
            facecolor=theme.ACCENT,
            edgecolor=theme.ACCENT,
            path_effects=[
                patheffects.Stroke(linewidth=4, foreground=theme.BG_2),
                patheffects.Normal(),
            ],
        )
        for artist in getattr(self, "_handles_artists", ()):
            artist.set(markerfacecolor=theme.ACCENT, markeredgecolor=theme.BG_2)

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

    def _handle_at(self, event):
        c_idx, c_dist = self._corner_handles.closest(event.x, event.y)
        e_idx, e_dist = self._edge_handles.closest(event.x, event.y)
        m_idx, m_dist = self._center_handle.closest(event.x, event.y)
        if 'move' in self._state:
            return 'C'
        if m_dist < self.grab_range * 2:
            return 'C'
        if c_dist > self.grab_range and e_dist > self.grab_range:
            if self.drag_from_anywhere and self._contains(event):
                return 'C'
            return None
        if c_dist < e_dist:
            return self._corner_order[c_idx]
        return self._edge_order[e_idx]

    def _matches_press(self, event):
        return not self.ignore(event) and self._handle_at(event) is not None

    def press(self, event):
        coordinator = self._gesture_coordinator
        if coordinator is not None and not coordinator.claim_press(self, event):
            return False
        if self.ignore(event):
            if coordinator is not None:
                coordinator.end_gesture(self)
            return False
        self._set_active_handle(event)
        if self._active_handle is None:
            if coordinator is not None:
                coordinator.end_gesture(self)
            return False
        if self._on_activate is not None:
            self._on_activate()
        return super().press(event)

    def onmove(self, event):
        coordinator = self._gesture_coordinator
        if coordinator is not None and not coordinator.owns_gesture(self):
            return False
        return super().onmove(event)

    def release(self, event):
        coordinator = self._gesture_coordinator
        if coordinator is not None and not coordinator.owns_gesture(self):
            return False
        try:
            return super().release(event)
        finally:
            if coordinator is not None:
                coordinator.end_gesture(self)

    def ignore(self, event):
        ignored = super().ignore(event)
        if (ignored and self.active and self.ax.get_visible()
                and self.canvas.widgetlock.available(self)
                and getattr(event, "button", None) in self.validButtons
                and self._eventpress is None and self.get_visible()):
            # Full-range handles sit on the axes border. Pixel rounding can
            # put the press just outside it; retain their normal grab radius.
            return self._handle_at(event) is None
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
        if self._gesture_coordinator is not None:
            self._gesture_coordinator.remove(self)
        self.set_active(False)
        self.set_visible(False)
        self.disconnect_events()
        for artist in self.artists:
            if artist.axes is not None:
                artist.remove()
