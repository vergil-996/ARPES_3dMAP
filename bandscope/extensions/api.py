# -*- coding: utf-8 -*-
"""官方扩展的最小宿主协议。

这一层是主程序与插件之间**唯一**的约定面，刻意保持窄：

1. **上下文**：当前视图、能量坐标与单位、ROI、时间帧、显示方向。只读，插件
   从这里读参数即可，不复制整块强度数据。
2. **面板注册**：插件交出一个参数面板，宿主负责挂载、显隐和生命周期。
3. **效果提交**：插件输出与当前能量轴等长的一维倍率向量，宿主负责校验、缓存
   与应用。三维 alpha 由宿主生成，插件不分配三维数组。
4. **状态与刷新**：宿主按页面保存插件参数，插件请求刷新，宿主处理节流与导出
   快照。

插件不得访问 ``My3DAnalyzer`` 的私有字段、不直接操作 VTK，也不改写强度数组：
渲染、色条、导出数值都由宿主按自己的路径产生。

本模块不导入 Qt / VTK，方便在没有图形环境时单独测试协议本身。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

#: 宿主协议版本。插件清单里的 ``api_version`` 必须与之相等才会被加载。
API_VERSION = 1

#: 能力标识：插件可以提交沿能量轴的不透明度倍率。
CAPABILITY_OPACITY_MULTIPLIER = "opacity_multiplier"

#: 清单必填字段。
REQUIRED_MANIFEST_FIELDS = (
    "id",
    "name",
    "version",
    "api_version",
    "requires_app",
    "entry_point",
    "capabilities",
)

#: 插件 id 允许的字符：与目录名、注册表键共用，避免任何路径解释空间。
_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_")

#: 版本号允许的字符。版本号同样会拼进安装路径，必须排除路径分隔符、盘符和
#: `..`，否则一个构造过的清单就能让安装器删掉扩展根目录之外的任何路径。
_VERSION_ALLOWED = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._-")


class PluginError(Exception):
    """插件安装、加载或运行期的可展示错误。"""


class PluginCompatibilityError(PluginError):
    """清单声明的主程序或接口版本与当前不符。"""


def normalize_plugin_id(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text or any(char not in _ID_ALLOWED for char in text):
        raise PluginError(
            "插件 id 只能由小写字母、数字和下划线组成，且不能为空。"
        )
    return text


def normalize_plugin_version(value: str) -> str:
    """校验版本号可以安全地当作单层目录名使用。

    版本号来自包内清单，却会直接参与 ``installed/<id>/<version>`` 的路径拼接、
    并且旧版本目录会被递归删除。任何路径分隔符、盘符、``..`` 或前导点都必须在
    这里挡掉，不能等路径传下去再判断。
    """
    text = str(value or "").strip()
    if not text or any(char not in _VERSION_ALLOWED for char in text):
        raise PluginError("插件版本号含有不允许的字符（只允许字母、数字、点、下划线和连字符）。")
    if text.startswith(".") or ".." in text:
        raise PluginError("插件版本号不能以点开头，也不能包含 `..`。")
    return text


def parse_version(value: str) -> Tuple[int, ...]:
    """把 ``1.2.3`` 拆成可比较的整数元组；非数字段按 0 处理。"""
    parts = []
    for chunk in str(value or "").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


@dataclass(frozen=True)
class PluginManifest:
    """``plugin.json`` 的解析结果。"""

    plugin_id: str
    name: str
    version: str
    api_version: int
    requires_app: str
    entry_module: str
    entry_object: str
    capabilities: Tuple[str, ...] = ()
    description: str = ""
    author: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PluginManifest":
        missing = [key for key in REQUIRED_MANIFEST_FIELDS if not payload.get(key)]
        if missing:
            raise PluginError(f"插件清单缺少字段：{', '.join(missing)}")

        raw_id = normalize_plugin_id(payload["id"])
        try:
            api_version = int(payload["api_version"])
        except (TypeError, ValueError) as exc:
            raise PluginError("api_version 必须是整数。") from exc

        entry = str(payload["entry_point"])
        if ":" not in entry:
            raise PluginError("entry_point 必须是 `模块:对象` 形式。")
        module_name, _, object_name = entry.partition(":")
        module_name = module_name.strip()
        object_name = object_name.strip()
        if not module_name or not object_name:
            raise PluginError("entry_point 必须是 `模块:对象` 形式。")
        if any(char not in _ID_ALLOWED | {"."} for char in module_name):
            raise PluginError("entry_point 的模块名只能包含小写字母、数字、下划线和点。")

        capabilities = payload["capabilities"]
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        if not isinstance(capabilities, (list, tuple)) or not capabilities:
            raise PluginError("capabilities 必须是非空列表。")

        return cls(
            plugin_id=raw_id,
            name=str(payload["name"]),
            version=normalize_plugin_version(payload["version"]),
            api_version=api_version,
            requires_app=str(payload["requires_app"]),
            entry_module=module_name,
            entry_object=object_name,
            capabilities=tuple(str(item) for item in capabilities),
            description=str(payload.get("description") or ""),
            author=str(payload.get("author") or ""),
        )

    def check_compatibility(self, app_version: str, api_version: int = API_VERSION):
        """加载插件代码之前必须通过的检查。"""
        if int(self.api_version) != int(api_version):
            raise PluginCompatibilityError(
                f"{self.name} 需要宿主接口版本 {self.api_version}，"
                f"当前主程序提供 {api_version}。"
            )
        if parse_version(self.requires_app) != parse_version(app_version):
            raise PluginCompatibilityError(
                f"{self.name} 需要主程序 {self.requires_app}，"
                f"当前为 {app_version}。"
            )


#: 坐标来源取值，与 ``analyzer_core`` 的 ``coord_sources`` 一致。
SOURCE_FILE = "file"
SOURCE_INDEX = "index"

#: 有坐标数组但缺单位元数据时的显示名：不猜单位，也不假装是索引。
UNIT_TEXT_NO_METADATA = "坐标值"
UNIT_TEXT_INDEX = "index"


@dataclass(frozen=True)
class EnergyAxisSpec:
    """插件看到的能量轴。

    坐标已经是**当前显示方向**（E 翻转开关生效后的顺序），插件按此解释峰位；
    换算到体素由宿主负责，插件不额外翻转数据。

    单位只如实转述：文件提供了坐标数组且带单位元数据时才显示该单位；只有坐标
    没有单位时显示「坐标值」；索引回退时显示 index。绝不自动标成 eV。
    """

    values: np.ndarray
    unit: Optional[str] = None
    source: str = SOURCE_INDEX
    roi_range: Tuple[float, float] = (0.0, 0.0)
    full_range: Tuple[float, float] = (0.0, 0.0)

    @property
    def has_coordinates(self) -> bool:
        return self.source == SOURCE_FILE

    @property
    def display_unit(self) -> str:
        if not self.has_coordinates:
            return UNIT_TEXT_INDEX
        unit = (self.unit or "").strip()
        return unit or UNIT_TEXT_NO_METADATA

    @property
    def label(self) -> str:
        return f"E ({self.display_unit})"

    def sample_spacing(self) -> float:
        """相邻采样点的中位间距，用于给出可修改的厚度初值建议。"""
        values = np.asarray(self.values, dtype=np.float64).reshape(-1)
        if values.size < 2:
            return 0.0
        diffs = np.abs(np.diff(values))
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        return float(np.median(diffs)) if diffs.size else 0.0


def align_multiplier_to_voxels(multiplier, *, flipped, e_bounds, stride=1):
    """把插件在**显示方向**能量轴上给出的倍率对齐到渲染用的体素索引。

    - ``flipped``：E 轴翻转开关是否生效。翻转只改变显示方向，体素顺序不变，
      所以要把显示序的倍率倒回来。
    - ``e_bounds``：compact ROI 在**原始体素域**的 ``(起, 止)`` 闭区间；权重按
      绝对范围截取，不因 ROI 缩小而重新拉伸。
    - ``stride``：预览降采样的步长；数据与权重必须用同一组索引。步长从体素 0
      起算，与渲染路径的 ``data[::stride, ::stride, ::stride]`` 一致。

    ``e_bounds`` 与 ``stride`` **不能同时给出**：那会同时存在“先截 ROI 再降采样”
    和“先降采样再截 ROI”两种索引约定，与其猜一个不如直接报错。当前渲染路径只在
    ``data_bounds is None`` 时降采样，两者天然互斥。

    返回 None 表示没有可用的倍率；调用方应走原有渲染路径。
    """
    if multiplier is None:
        return None
    values = np.asarray(multiplier, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return None
    step = int(stride)
    if e_bounds is not None and step > 1:
        raise ValueError(
            "能量 ROI 范围与预览降采样不能同时使用：两者会给出不同的索引约定。"
        )
    if flipped:
        values = values[::-1]
    if e_bounds is not None:
        start, stop = int(e_bounds[0]), int(e_bounds[1])
        if start < 0 or stop < start or stop >= values.size:
            raise ValueError(
                f"能量 ROI 范围 {(start, stop)} 超出倍率长度 {values.size}。"
            )
        values = values[start : stop + 1]
    if step > 1:
        values = values[::step]
    return np.ascontiguousarray(values)


@dataclass(frozen=True)
class PluginContext:
    """插件在每个渲染周期看到的只读上下文快照。"""

    page_id: str = ""
    page_title: str = ""
    view: str = ""
    energy: Optional[EnergyAxisSpec] = None
    frame_index: Optional[int] = None
    frame_label: str = ""
    display_e_flip: bool = False
    roi_label: str = ""

    @property
    def renders_volume(self) -> bool:
        return self.view == "3d"

    def energy_in_range(self, value: float) -> bool:
        if self.energy is None:
            return False
        low, high = self.energy.roi_range
        return min(low, high) <= float(value) <= max(low, high)


class PluginHost(abc.ABC):
    """宿主提供给插件的能力。实现由主程序提供。"""

    api_version = API_VERSION

    @abc.abstractmethod
    def context(self) -> PluginContext:
        """当前渲染周期的上下文快照。"""

    @abc.abstractmethod
    def request_refresh(self, *, immediate: bool = False) -> None:
        """请求重绘 3D 视图；宿主负责节流、失效与导出快照。"""

    @abc.abstractmethod
    def notify(self, message: str, *, level: str = "info") -> None:
        """向用户提示一条与插件有关的信息。"""


class Plugin(abc.ABC):
    """扩展实现的接口。

    宿主只依赖下面这几个方法；面板是普通 ``QWidget``，由宿主挂载和显隐。
    """

    #: 由加载器写入，等于清单里的 id。
    plugin_id: str = ""
    manifest: Optional[PluginManifest] = None

    @abc.abstractmethod
    def create_panel(self, host: PluginHost):
        """创建参数面板（QWidget）。每个页面一份，不得缓存控件实例。"""

    def on_context_changed(self, context: PluginContext) -> None:
        """上下文变化（视图切换、ROI、帧、数据）时调用。"""

    def opacity_multiplier(self, context: PluginContext) -> Optional[Sequence[float]]:
        """与 ``context.energy.values`` 等长的一维非负倍率；None 表示不参与。"""
        return None

    def export_state(self) -> Dict[str, Any]:
        """参数快照，用于按页面保存与预设导入导出。"""
        return {}

    def restore_state(self, state: Mapping[str, Any]) -> None:
        """恢复参数快照；实现必须容忍缺失与多余的字段。"""

    def reset_for_new_data(self) -> None:
        """加载新数据文件时清空与旧数据绑定的峰位。"""

    def release(self) -> None:
        """页面关闭或插件卸载时释放资源。"""


@dataclass
class PluginRecord:
    """注册表里一条已安装插件的状态。"""

    plugin_id: str
    version: str
    path: str
    enabled: bool = True
    manifest: Optional[PluginManifest] = None
    load_error: str = ""
    instance: Optional[Plugin] = None
    #: 已记录卸载请求，下次启动时删除。
    pending_removal: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.instance is not None and not self.load_error
