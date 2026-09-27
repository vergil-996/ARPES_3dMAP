"""3D 图片导出与屏幕采用同一份 alpha 语义（计划 §5、§8「渲染和状态」）。

快照里冻结的 ``opacity_multiplier`` 必须真的改变离屏体渲染：导出走的是另一条
plotter，如果只有主视图接上了插件的倍率，出图就会悄悄退回原图。
"""
import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg

from bandscope.exporting.publication_models import OutputOptions, resolve_style
from bandscope.exporting.publication_renderers import render_3d
from bandscope.rendering.render_core import VisualEngine

SHAPE = (6, 10, 48)
FWHM_FRACTION = 0.08


def _energy_axis(count=SHAPE[2]):
    return np.linspace(-1.0, 1.0, count)


def _volume():
    """强度只沿 kx 变化：同一条能量扫描线上强度一致，便于只看 alpha 的作用。"""
    ramp = np.linspace(0.25, 0.75, SHAPE[0], dtype=np.float32)
    return np.broadcast_to(ramp[:, None, None], SHAPE).copy()


def _multiplier(band_center=0.0, *, background=0.005, gain=0.02):
    energy = _energy_axis()
    profile = np.full(energy.shape, background)
    gauss = np.exp(-4.0 * np.log(2.0) * ((energy - band_center) / FWHM_FRACTION) ** 2)
    return np.maximum(profile, background + gain * gauss)


class _Snapshot:
    """render_3d 真正读取的字段；其余走默认值。"""

    def __init__(self, volume, multiplier):
        from bandscope.exporting.publication_renderers import compute_level_info

        self.snapshot_id = "pluginexport"
        self.source_page_id = "p1"
        self.source_page_title = "3D"
        self.page_kind = "home"
        self.view = "3d"
        self.view_family = "3d"
        self.captured_at = "2026-09-27T10:00:00"
        self.cmap_name = "magma"
        self.levels_params = (10.0, 50.0, 90.0)
        self.locked_range = None
        self.coords = {
            "X": np.linspace(-1.0, 1.0, SHAPE[0]),
            "Y": np.linspace(-1.0, 1.0, SHAPE[1]),
            "E": _energy_axis(),
            "delay": np.array([0.0]),
        }
        self.coord_sources = {"X": "file", "Y": "file", "E": "file", "delay": "index"}
        self.coord_units = {"X": "Å⁻¹", "Y": "Å⁻¹", "E": "eV", "delay": None}
        self.camera = {
            "position": (100.0, -600.0, 100.0),
            "focal_point": (100.0, 100.0, 100.0),
            "view_up": (0.0, 0.0, 1.0),
            "parallel_projection": True,
            "parallel_scale": 110.0,
        }
        self.payload = {
            "volume": volume,
            "data_bounds": (
                0, SHAPE[0] - 1, 0, SHAPE[1] - 1, 0, SHAPE[2] - 1
            ),
            "full_shape": SHAPE,
            "spacing": tuple(200.0 / (n - 1) for n in SHAPE),
            "clip_render_bounds": None,
            "include_zero": False,
            "opacity_mode": "linear",
            "opacity_multiplier": multiplier,
            "show_axes": False,
            "viewport_aspect": 1.0,
            "level_info": compute_level_info(volume, self.levels_params),
            "intensity_label": "Intensity (a.u.)",
            "axis_titles": ["kx", "ky", "E (eV)"],
        }


def _render(volume, multiplier):
    VisualEngine._last_data_range = None
    VisualEngine._locked_data_range = None
    snapshot = _Snapshot(volume, multiplier)
    figure = render_3d(
        snapshot,
        resolve_style("3d", None),
        {},
        OutputOptions(width_mm=80.0, height_mm=80.0, dpi=60, fmt="png"),
        60,
    )
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    image = np.asarray(canvas.buffer_rgba())[..., :3].astype(np.int16)
    figure.clear()
    return image


def _volume_footprint(image):
    """体渲染真正占用的像素：导出画布是白底，纯白处算留白。

    只取内容区左侧的一段列：右侧是色条，它不受效果影响，会把行剖面拉平。
    """
    distance = (255 - image).mean(axis=2)
    columns = np.nonzero((distance > 3).any(axis=0))[0]
    if columns.size == 0:
        return np.zeros_like(distance, dtype=bool)
    stop = columns[0] + int((columns[-1] - columns[0]) * 0.55)
    footprint = np.zeros_like(distance, dtype=bool)
    footprint[:, columns[0] : stop] = distance[:, columns[0] : stop] > 3.0
    return footprint


def _row_profile(image, footprint):
    """每行的平均“离白距离”；被压暗的体素更接近白底，数值更小。"""
    distance = (255 - image).mean(axis=2)
    rows = np.nonzero(footprint.any(axis=1))[0]
    return rows, np.array([distance[r][footprint[r]].mean() for r in rows])


class ExportEffectTests(unittest.TestCase):
    def setUp(self):
        self.volume = _volume()
        self.baseline = _render(self.volume, None)

    def test_frozen_multiplier_changes_the_exported_image(self):
        enhanced = _render(self.volume, _multiplier())
        self.assertGreater(int(np.abs(enhanced - self.baseline).max()), 10)

    def test_exported_volume_keeps_the_band_and_loses_the_background(self):
        enhanced = _render(self.volume, _multiplier())
        footprint = _volume_footprint(self.baseline)
        _, baseline_profile = _row_profile(self.baseline, footprint)
        _, profile = _row_profile(enhanced, footprint)
        # 背景整片被压向白底，而目标能量附近留下一条明显更实的带。
        self.assertGreater(float(profile.max()), float(np.median(profile)) * 1.3)

    def test_exported_peak_follows_the_requested_energy(self):
        footprint = _volume_footprint(self.baseline)
        peaks = {}
        for center in (-0.4, 0.0, 0.4):
            rows, profile = _row_profile(_render(self.volume, _multiplier(center)), footprint)
            peaks[center] = int(rows[int(np.argmax(profile))])
        # 屏幕行号自上而下，E 轴向上；能量越高，峰值行号越小。
        self.assertLess(peaks[0.4], peaks[0.0])
        self.assertLess(peaks[0.0], peaks[-0.4])

    def test_no_multiplier_keeps_the_original_render(self):
        again = _render(self.volume, None)
        np.testing.assert_array_equal(again, self.baseline)

    def test_multiplier_of_ones_stays_close_to_the_original(self):
        neutral = _render(self.volume, np.ones(SHAPE[2]))
        difference = np.abs(neutral - self.baseline)
        self.assertLess(float(difference.mean()), 6.0)

    def test_multiplier_length_is_not_silently_accepted(self):
        """长度不符要显式失败，而不是画出一张看起来正常的错图。"""
        with self.assertRaises(Exception):
            _render(self.volume, np.ones(SHAPE[2] + 3))


if __name__ == "__main__":
    unittest.main()
