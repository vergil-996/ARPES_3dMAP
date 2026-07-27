import math


class PlotCoordinateTooltip:
    """Show data coordinates in a floating annotation above a Matplotlib plot."""

    SUPPORTED_VIEWS = {"2d", "1d", "1d_comparison", "waterfall"}

    def __init__(
        self,
        canvas,
        axes,
        context_provider,
        active_provider=None,
        external_blit_provider=None,
    ):
        self.canvas = canvas
        self.axes = axes
        self.context_provider = context_provider
        self.active_provider = active_provider
        self.external_blit_provider = external_blit_provider
        self.annotation = None
        self._background = None
        self.motion_cid = canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.leave_cid = canvas.mpl_connect("figure_leave_event", self._on_leave)
        self.draw_cid = canvas.mpl_connect("draw_event", self._on_draw)
        self.resize_cid = canvas.mpl_connect("resize_event", self._on_resize)

    @staticmethod
    def _format_value(value):
        numeric_value = float(value)
        if abs(numeric_value) < 5e-13:
            numeric_value = 0.0
        return f"{numeric_value:.6g}"

    @staticmethod
    def _axis_labels(context):
        view = context.get("view")
        if view == "2d":
            plot_axes = context.get("plot_axes") or {}
            return (
                str(plot_axes.get("x_label") or "x"),
                str(plot_axes.get("y_label") or "y"),
            )
        if view == "waterfall":
            return (
                str(context.get("xlabel") or "x"),
                str(context.get("ylabel") or "y"),
            )
        return (
            str(context.get("xlabel") or "x"),
            str(context.get("ylabel") or "Intensity (a.u.)"),
        )

    def _is_active(self):
        if self.active_provider is None:
            return True
        try:
            return bool(self.active_provider())
        except (AttributeError, RuntimeError):
            return False

    def _current_context(self):
        try:
            context = self.context_provider()
        except (AttributeError, RuntimeError):
            return None
        if not isinstance(context, dict) or context.get("view") not in self.SUPPORTED_VIEWS:
            return None
        return context

    def _ensure_annotation(self):
        if self.annotation is not None and self.annotation in self.axes.texts:
            return self.annotation

        self.annotation = self.axes.annotate(
            "",
            xy=(0.0, 0.0),
            xytext=(14, 14),
            textcoords="offset points",
            color="#FFFFFF",
            fontsize=10,
            ha="left",
            va="bottom",
            bbox={
                "boxstyle": "round,pad=0.45",
                "facecolor": "#2A2A3A",
                "edgecolor": "#FFFFFF",
                "linewidth": 0.8,
                "alpha": 0.96,
            },
            arrowprops={
                "arrowstyle": "->",
                "color": "#FFFFFF",
                "linewidth": 0.8,
            },
            annotation_clip=False,
            zorder=1000,
        )
        self.annotation.set_animated(self._supports_blit())
        self.annotation.set_visible(False)
        return self.annotation

    def _supports_blit(self):
        return bool(
            getattr(self.canvas, "supports_blit", False)
            and hasattr(self.canvas, "copy_from_bbox")
            and hasattr(self.canvas, "restore_region")
            and hasattr(self.canvas, "blit")
        )

    def _on_draw(self, _event):
        if not self._supports_blit():
            self._background = None
            return
        try:
            self._background = self.canvas.copy_from_bbox(self.axes.bbox)
        except (AttributeError, RuntimeError):
            self._background = None

    def _on_resize(self, _event):
        self._background = None

    def _external_blit_will_draw(self, event):
        if self.external_blit_provider is None:
            return False
        try:
            return bool(self.external_blit_provider(event))
        except (AttributeError, RuntimeError, TypeError):
            return False

    def _redraw_overlay(self):
        annotation = self.annotation
        if not self._supports_blit() or self._background is None:
            self.canvas.draw_idle()
            return False

        try:
            self.canvas.restore_region(self._background)
            if annotation is not None and annotation.get_visible():
                self.axes.draw_artist(annotation)
            self.canvas.blit(self.axes.bbox)
            return True
        except (AttributeError, RuntimeError):
            self._background = None
            self.canvas.draw_idle()
            return False

    def _position_annotation(self, annotation, event):
        axes_bounds = self.axes.get_window_extent()
        place_left = event.x > axes_bounds.x0 + axes_bounds.width * 0.62
        place_below = event.y > axes_bounds.y0 + axes_bounds.height * 0.72
        annotation.set_position(((-14 if place_left else 14), (-14 if place_below else 14)))
        annotation.set_ha("right" if place_left else "left")
        annotation.set_va("top" if place_below else "bottom")

    def _on_motion(self, event):
        context = self._current_context()
        if (
            context is None
            or not self._is_active()
            or event.inaxes is not self.axes
            or event.xdata is None
            or event.ydata is None
            or not math.isfinite(float(event.xdata))
            or not math.isfinite(float(event.ydata))
        ):
            self.hide()
            return

        annotation = self._ensure_annotation()
        x_label, y_label = self._axis_labels(context)
        annotation.xy = (float(event.xdata), float(event.ydata))
        annotation.set_text(
            f"{x_label}: {self._format_value(event.xdata)}\n"
            f"{y_label}: {self._format_value(event.ydata)}"
        )
        self._position_annotation(annotation, event)
        annotation.set_visible(True)
        if not self._external_blit_will_draw(event):
            self._redraw_overlay()

    def _on_leave(self, _event):
        self.hide()

    def hide(self, redraw=True):
        if self.annotation is None or not self.annotation.get_visible():
            return
        self.annotation.set_visible(False)
        if redraw:
            self._redraw_overlay()
