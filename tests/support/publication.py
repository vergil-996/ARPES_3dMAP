"""Deterministic publication snapshots shared by export tests."""
import numpy as np
from bandscope.exporting.publication_models import PublicationSnapshot
from bandscope.exporting.publication_renderers import compute_level_info
from bandscope.rendering.render_core import VisualEngine

def _fresh_class_state():
    VisualEngine._last_data_range = None
    VisualEngine._locked_data_range = None


def _make_2d_snapshot(**overrides):
    _fresh_class_state()
    x = np.linspace(-1.0, 1.0, 120)
    # 冻结态 payload 已由 _freeze_2d 归一化：坐标轴与 extent 均为升序
    e = np.linspace(-2.0, 2.0, 80)
    X, E = np.meshgrid(x, e, indexing="ij")
    data = np.exp(-(((X - 0.3) ** 2) / 0.05 + ((E + 0.8) ** 2) / 0.2))
    payload = {
        "image": data.T,
        "extent": [-1.0, 1.0, -2.0, 2.0],
        "title": "X-Integral (40~60)",
        "xlabel": "kx (Å⁻¹)",
        "ylabel": "E",
        "level_info": compute_level_info(data, (10.0, 50.0, 90.0)),
        "intensity_label": "Intensity (a.u.)",
    }
    payload.update(overrides.pop("payload", {}))
    snap = PublicationSnapshot(
        snapshot_id="test2d",
        source_page_id="p1",
        source_page_title="X轴积分",
        page_kind="axis_integral",
        view="2d",
        view_family="2d",
        captured_at="2026-09-22T10:00:00",
        cmap_name="magma",
        levels_params=(10.0, 50.0, 90.0),
        locked_range=None,
        coords={"X": x, "Y": x, "E": e, "delay": np.array([0.0])},
        coord_sources={"X": "file", "Y": "file", "E": "file", "delay": "index"},
        coord_units={"X": "Å⁻¹", "Y": "Å⁻¹", "E": None, "delay": None},
        payload=payload,
    )
    for key, value in overrides.items():
        setattr(snap, key, value)
    return snap


class _FakeVTKRenderer:
    """_world_to_display 用到的三个 VTK 接口的最小替身。

    3D 轴名测试只驱动轴线绘制，不接真实 VTK/OpenGL；投影用一个固定的
    线性映射代替，只需保证角点投影后仍在画布内、能选出"最外侧边"。
    """

    def SetWorldPoint(self, x, y, z, w):
        self._world = (float(x), float(y), float(z))

    def WorldToDisplay(self):
        # 斜投影：三个轴都必须改变屏幕坐标，否则某条轴会被当成零长度边跳过
        x, y, z = self._world
        self._display = (400.0 + 40.0 * x + 20.0 * z, 400.0 + 40.0 * y + 20.0 * z, 0.0)

    def GetDisplayPoint(self):
        return self._display


def _make_3d_axes_snapshot():
    """_draw_3d_axes 的最小输入：只填轴线绘制真正读取的字段。"""
    _fresh_class_state()
    coords = {key: np.linspace(-1.0, 1.0, 10) for key in ("X", "Y", "E")}
    return PublicationSnapshot(
        snapshot_id="test3d",
        source_page_id="p1",
        source_page_title="3D",
        page_kind="home",
        view="3d",
        view_family="3d",
        captured_at="2026-09-26T10:00:00",
        cmap_name="magma",
        levels_params=(10.0, 50.0, 90.0),
        locked_range=None,
        coords=coords,
        coord_sources={"X": "file", "Y": "file", "E": "file", "delay": "index"},
        coord_units={"X": "Å⁻¹", "Y": "Å⁻¹", "E": "eV", "delay": None},
        camera={"position": (30.0, 30.0, 30.0), "focal_point": (0.0, 0.0, 0.0)},
        payload={
            "data_bounds": (0, 9, 0, 9, 0, 9),
            "spacing": (1.0, 1.0, 1.0),
            "axis_titles": ["kx (Å⁻¹)", "ky (Å⁻¹)", "E (eV)"],
            "show_axes": True,
            "show_box": False,
        },
    )


def _make_1d_comparison_snapshot():
    _fresh_class_state()
    energy = np.linspace(-2.0, 2.0, 200)
    return PublicationSnapshot(
        snapshot_id="test1dc",
        source_page_id="p2",
        source_page_title="曲线比较",
        page_kind="curve_comparison_1d",
        view="1d_comparison",
        view_family="1d",
        captured_at="2026-09-22T10:00:00",
        cmap_name="magma",
        levels_params=(10.0, 50.0, 90.0),
        locked_range=None,
        coords={"X": energy, "Y": energy, "E": energy, "delay": np.array([0.0])},
        coord_sources={"E": "file"},
        coord_units={"E": "eV"},
        payload={
            "curves": [
                {"x": energy, "y": np.exp(-(energy ** 2) / 0.5), "label": "基准曲线A"},
                {"x": energy, "y": 0.8 * np.exp(-((energy - 0.5) ** 2) / 0.3), "label": "曲线B"},
            ],
            "title": "曲线比较",
            "xlabel": "E (eV)",
            "ylabel": "Intensity (a.u.)",
            "comparison_kind": "energy_dos",
        },
    )


def make_single_curve_snapshot():
    snapshot = _make_1d_comparison_snapshot()
    snapshot.view = "1d"
    snapshot.source_page_id = "single"
    snapshot.snapshot_id = "single-snapshot"
    snapshot.payload["curve"] = dict(snapshot.payload.pop("curves")[0], label=None, curve_id="main")
    return snapshot


def make_waterfall_snapshot(count=5):
    snapshot = _make_1d_comparison_snapshot()
    energy = np.linspace(-2, 2, 100)
    snapshot.view = "waterfall"
    snapshot.source_page_id = "waterfall"
    snapshot.snapshot_id = "waterfall-snapshot"
    snapshot.payload = dict(energy_axis=energy, curves=np.array([np.exp(-(energy - i / count) ** 2) for i in range(count)]),
                            k_values=np.linspace(-1, 1, count), offset_step=1.2,
                            curve_offsets=np.arange(count) * 1.2,
                            curve_ids=[f"X:{i * 2}" for i in range(count)],
                            title="瀑布图", xlabel="Intensity (normalized, arb. u.)", ylabel="E (eV)")
    return snapshot


class MemoryPublicationSettings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None, type=None):
        result = self.data.get(key, default)
        return type(result) if type is not None and result is not None else result

    def setValue(self, key, value):
        self.data[key] = value


