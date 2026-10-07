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
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

#: 版本与能力的判定规则统一放在 ``compat``：安装、加载、构建脚本、官方目录和
#: 升级评估共用同一组纯函数。这里重导出协议常量，插件与新代码仍从本模块引用。
from bandscope.extensions.compat import (  # noqa: F401
    ANALYSIS_3D_CAPABILITIES,
    ANALYSIS_CAPABILITIES,
    API_VERSION,
    CAPABILITY_ANALYSIS_TASK,
    CAPABILITY_DATA_SNAPSHOT_2D,
    CAPABILITY_DATA_SNAPSHOT_3D,
    CAPABILITY_OPACITY_MULTIPLIER,
    CAPABILITY_RENDER_OVERLAY_SURFACE,
    CAPABILITY_RESULT_CURVE_1D,
    CAPABILITY_RESULT_SURFACE_2D,
    SUPPORTED_API_VERSIONS,
    SUPPORTED_CAPABILITIES,
    evaluate_compatibility,
    parse_requires_app,
)

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


def resolve_api_version(value) -> Optional[int]:
    """插件声明与路径无关的整数接口版本；非法输入返回 None。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

    def check_compatibility(
        self,
        app_version: str,
        *,
        supported_apis=SUPPORTED_API_VERSIONS,
        supported_capabilities=SUPPORTED_CAPABILITIES,
    ):
        """加载插件代码之前必须通过的检查。

        结论与原因来自 ``compat.evaluate_compatibility``，与安装事务、构建脚本、
        官方目录和升级评估完全相同；这里只负责把失败翻译成可展示的异常。
        """
        verdict = evaluate_compatibility(
            name=self.name,
            requires_app=self.requires_app,
            api_version=self.api_version,
            capabilities=self.capabilities,
            app_version=app_version,
            supported_apis=supported_apis,
            supported_capabilities=supported_capabilities,
        )
        if not verdict.ok:
            raise PluginCompatibilityError(verdict.reason)


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

    def on_analysis_finished(self, handle, status: str, detail: str = "") -> None:
        """分析任务结束（成功 / 失败 / 取消）后的通知。

        默认什么都不做——v1 插件不会因为这个钩子被迫实现新方法。需要恢复按钮
        状态或提示用户的 v2 插件在这里处理，``status`` 取 ``succeeded``、
        ``failed``、``cancelled``。
        """

    def on_analysis_progress(self, handle, fraction: float, message: str = "") -> None:
        """分析任务的进度回传（可选）。

        插件在 ``CancelToken.report_progress`` 里上报，宿主节流后在**主线程**回调
        这里。默认什么都不做：不实现这个钩子的插件照常工作。
        """

    def reset_for_new_data(self) -> None:
        """加载新数据文件时清空与旧数据绑定的峰位。"""

    def release(self) -> None:
        """页面关闭或插件卸载时释放资源。"""


@dataclass
class PluginRecord:
    """登记表里一条已安装插件的状态：期望配置 + 本会话运行状态。

    期望配置（``version`` / ``path`` / ``digest`` / ``enabled`` / ``pending_removal``
    等）来自登记表，可能已经指向“下次启动”的目标；``running`` 与 ``instance``
    只描述当前会话。用户启停或卸载时**只改期望配置**，当前效果保持到重启。
    """

    plugin_id: str
    version: str = ""
    path: str = ""
    enabled: bool = True
    manifest: Optional[PluginManifest] = None
    load_error: str = ""
    instance: Optional[Plugin] = None
    #: 已记录卸载请求，下次启动时删除。
    pending_removal: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- 期望配置（登记表 schema v2） ----------------------------------
    #: 期望内容的摘要与路径（``path`` 相对扩展根）。
    digest: str = ""
    last_good_version: str = ""
    last_good_digest: str = ""
    previous_good_version: str = ""
    previous_good_digest: str = ""
    #: 已登记“恢复上一版本”，重启时执行。
    restore_pending: bool = False
    #: 上次加载失败且尚未重试的候选。
    failed_candidate: Optional[Dict[str, Any]] = None
    source: Dict[str, Any] = field(default_factory=dict)
    #: 最近一次操作错误（例如卸载删除失败）的摘要。
    last_operation_error: str = ""
    #: 登记记录无法确认时的原因；非空即只读，不加载也不允许修改。
    unconfirmed_reason: str = ""

    # -- 本会话运行状态 ------------------------------------------------
    running: bool = False
    running_version: str = ""
    #: 加载时登记表的配置修订与内容摘要，供健康回报核对。
    started_revision: int = -1
    started_digest: str = ""

    @property
    def ready(self) -> bool:
        return self.running and self.instance is not None


# ---------------------------------------------------------------------------
# 分析插件（API 2）
# ---------------------------------------------------------------------------

#: 分析任务队列上限：一个工作线程、最多四个排队任务。
MAX_ANALYSIS_QUEUE = 4

#: 二维快照的数组顺序固定为 ``[x, y]``：第一维沿 x 轴，第二维沿 y 轴。
ANALYSIS_ARRAY_ORDER = "[x, y]"


class AnalysisUnavailable(PluginError):
    """当前状态不满足分析的前置条件。

    消息直接面向用户：预览中、计算未完成、当前不是二维结果页、带擦除区域的
    裁空状态都属于这一类，逐条说明为什么现在不能分析。
    """


class AnalysisCancelled(Exception):
    """分析任务已被取消；插件在检查点抛出它即可立即退出。"""


class AnalysisBusy(Exception):
    """该插件已有未结束的任务，或队列已满。

    这是**预期内**的状态，不是错误：宿主已经向用户提示了忙碌原因，插件可以
    直接忽略（正式接口返回 None，不抛给插件）。
    """


class AnalysisResultError(PluginError):
    """插件交回的结果不符合契约（长度不等、含无穷值等）。"""


@dataclass(frozen=True)
class AnalysisInput2D:
    """一次二维数值结果的只读快照。

    数组顺序统一为 ``[x, y]``，``x`` / ``y`` 与对应维度**等长**并保留输入方向
    （与页面上看到的一致，不做翻转）。``data`` 与坐标数组都是任务独占的只读
    缓冲区：插件改不动它们，也不需要复制一份来保护原始数据。

    单位只如实转述：没有单位元数据时保持空串，界面与结果里显示为 index 或
    坐标值，绝不自动补成 ``eV`` / ``Å⁻¹``。
    """

    plugin_id: str
    page_id: str
    page_title: str
    snapshot_id: str
    data_generation: int
    data: np.ndarray
    x: np.ndarray
    y: np.ndarray
    x_label: str = "x"
    x_unit: str = ""
    y_label: str = "y"
    y_unit: str = ""
    title: str = ""
    frame_index: Optional[int] = None
    frame_label: str = ""
    roi_label: str = ""
    scope_id: str = "full"
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int]:
        return (int(self.data.shape[0]), int(self.data.shape[1]))

    def x_axis_label(self) -> str:
        return _axis_label(self.x_label, self.x_unit)

    def y_axis_label(self) -> str:
        return _axis_label(self.y_label, self.y_unit)

    def describe(self) -> str:
        """一行可读的来源说明，直接进结果页与日志。"""
        parts = [self.title or self.page_title or self.page_id, f"{self.shape[0]}×{self.shape[1]}"]
        if self.frame_label:
            parts.append(self.frame_label)
        if self.roi_label:
            parts.append(self.roi_label)
        return " · ".join(part for part in parts if part)


def _axis_label(name: str, unit: str) -> str:
    label = str(name or "").strip() or "index"
    unit = str(unit or "").strip()
    return f"{label} ({unit})" if unit else label


@dataclass(frozen=True)
class AnalysisCurve1D:
    """插件交回的一维结果：宿主负责建页、展示与导出。

    ``params`` 必须是可序列化的普通数据（数字、字符串、列表、字典）；它随结果
    一起登记，用于复现和对照，不参与计算。
    """

    x: Sequence[float]
    y: Sequence[float]
    x_label: str = "x"
    x_unit: str = ""
    y_label: str = "Intensity"
    y_unit: str = ""
    title: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)

    def x_axis_label(self) -> str:
        return _axis_label(self.x_label, self.x_unit)

    def y_axis_label(self) -> str:
        return _axis_label(self.y_label, self.y_unit)


@dataclass(frozen=True)
class AnalysisTaskHandle:
    """一次已登记分析任务的句柄。"""

    task_id: str
    plugin_id: str
    page_id: str
    snapshot_id: str
    title: str = ""


@dataclass
class CancelToken:
    """协作取消信号。

    插件在循环里调用 :meth:`raise_if_cancelled`（或读 :attr:`cancelled`）主动退出；
    宿主不会强制终止线程，也不会在窗口销毁后回调控件。

    :meth:`report_progress` 是**可选**的进度回传通道：宿主没接线时它是无副作用的
    空操作，接线后进度会被节流送到主线程（面板状态行与
    ``Plugin.on_analysis_progress``）。加在令牌上而不是工作函数签名上，旧插件的
    三参数工作函数完全不受影响。
    """

    _event: Any = field(default_factory=threading.Event)
    #: 宿主注入的进度接收器 ``(fraction, message) -> None``；None 表示未接线。
    progress_sink: Any = None

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return bool(self._event.is_set())

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AnalysisCancelled("分析任务已取消。")

    def report_progress(self, fraction: float, message: str = "") -> None:
        """汇报进度（``0..1``）；越界夹到端点，非有限值直接忽略。"""
        sink = self.progress_sink
        if sink is None:
            return
        try:
            value = float(fraction)
        except (TypeError, ValueError):
            return
        if not np.isfinite(value):
            return
        sink(min(1.0, max(0.0, value)), str(message or ""))


#: 插件提供的分析工作函数：``(快照, 冻结参数, 取消信号) -> AnalysisCurve1D``。
AnalysisWork = Callable[..., AnalysisCurve1D]


def validate_analysis_curve(curve: AnalysisCurve1D) -> AnalysisCurve1D:
    """校验插件结果并转成宿主拥有的数值缓冲区。

    - 必须是 :class:`AnalysisCurve1D`；
    - x / y 必须是一维、非空且等长；
    - x 必须有限（坐标不能缺测）；
    - y 允许 NaN 表示缺测，但拒绝正负无穷——缺测不能被伪造成 0。
    """
    if not isinstance(curve, AnalysisCurve1D):
        raise AnalysisResultError(
            f"分析结果必须是 AnalysisCurve1D，收到 {type(curve).__name__}。"
        )
    try:
        x = np.asarray(curve.x, dtype=np.float64).reshape(-1)
        y = np.asarray(curve.y, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise AnalysisResultError(f"分析结果不是数值数组：{exc}") from exc
    if x.size == 0 or y.size == 0:
        raise AnalysisResultError("分析结果为空。")
    if x.size != y.size:
        raise AnalysisResultError(
            f"分析结果的 x（{x.size}）与 y（{y.size}）长度不一致。"
        )
    if not np.all(np.isfinite(x)):
        raise AnalysisResultError("分析结果的 x 含缺测或非有限值，坐标必须完整。")
    if np.any(np.isinf(y)):
        raise AnalysisResultError("分析结果的 y 含无穷值；缺测请用 NaN 表示。")
    x = np.array(x, dtype=np.float64, copy=True)
    y = np.array(y, dtype=np.float64, copy=True)
    x.setflags(write=False)
    y.setflags(write=False)
    return AnalysisCurve1D(
        x=x,
        y=y,
        x_label=str(curve.x_label or "x"),
        x_unit=str(curve.x_unit or ""),
        y_label=str(curve.y_label or "Intensity"),
        y_unit=str(curve.y_unit or ""),
        title=str(curve.title or ""),
        params=dict(curve.params or {}),
    )


def read_only_array(values, *, dtype=np.float64) -> np.ndarray:
    """复制成任务独占的只读缓冲区。"""
    array = np.array(values, dtype=dtype, copy=True)
    array.setflags(write=False)
    return array


class PluginHostV2(PluginHost):
    """API 2 宿主：在 v1 的上下文/刷新/提示之外增加分析能力。

    v1 插件拿到的仍是 v1 接口，不会被要求实现这里的方法；声明了分析能力并
    使用 API 2 的插件才需要它。实现由主程序提供，图形操作一律由宿主在主线程完成。
    """

    api_version = 2

    @abc.abstractmethod
    def capture_analysis_input(self) -> AnalysisInput2D:
        """抓取当前二维结果的只读快照。

        不可用时抛 :class:`AnalysisUnavailable`，消息可直接展示给用户。
        """

    @abc.abstractmethod
    def submit_analysis(
        self, work, *, title: str = "", params: Optional[Mapping[str, Any]] = None
    ) -> Optional[AnalysisTaskHandle]:
        """提交一次后台分析；返回句柄。

        返回 ``None`` 表示当前不能提交（该插件上一个任务还没结束，或队列已满），
        原因已经提示给用户。
        """

    @abc.abstractmethod
    def cancel_analysis(self, handle: Optional[AnalysisTaskHandle]) -> None:
        """请求取消；协作取消，未退出前不会启动该插件的下一个任务。"""


# ---------------------------------------------------------------------------
# 三维分析插件（API 3）
# ---------------------------------------------------------------------------

#: 三维快照的数组顺序：``[X, Y, E]``（前两维是动量平面，第三维是能量）。
ANALYSIS_VOLUME_ORDER = "[X, Y, E]"

#: 面结果的数组顺序：``z[i, j]`` 对应 ``(x[i], y[j])``。
ANALYSIS_SURFACE_ORDER = "[i, j] = [x, y]"

#: 一次分析允许交回的带面数量上限。
MAX_SURFACE_BANDS = 64


@dataclass(frozen=True)
class AnalysisInput3D:
    """当前三维体数据的只读快照。

    取数语义与三维视图所见一致：当前页的**数据域**（ROI 裁剪后）→ 当前**时间帧**
    → 若开启了全局去噪则取去噪后的体数据。**不含显示旋转**：``rotation_angle``
    只如实记录当前显示角度，叠加渲染时由宿主把同一旋转施加到几何上。

    数组固定为 ``[X, Y, E]``；``x`` / ``y`` / ``e`` 与对应维等长且已按同一数据域
    切片。单位只如实转述，缺单位时保持空串。``volume`` 与坐标都是任务独占的
    **只读**缓冲区，插件改不动；体数据里的 NaN 表示缺测（不是 0）。

    生命周期：宿主只在任务存续期持有快照，任务结束即释放；插件不要在工作函数
    返回后继续持有 ``volume``。
    """

    plugin_id: str
    page_id: str
    page_title: str
    snapshot_id: str
    data_generation: int
    volume: np.ndarray
    x: np.ndarray
    y: np.ndarray
    e: np.ndarray
    x_label: str = "X"
    x_unit: str = ""
    y_label: str = "Y"
    y_unit: str = ""
    e_label: str = "E"
    e_unit: str = ""
    frame_index: Optional[int] = None
    frame_label: str = ""
    scope_id: str = "full"
    scope_label: str = "完整数据"
    rotation_angle: float = 0.0
    title: str = ""
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int, int]:
        return (
            int(self.volume.shape[0]),
            int(self.volume.shape[1]),
            int(self.volume.shape[2]),
        )

    @property
    def nbytes(self) -> int:
        return int(self.volume.nbytes)

    def describe(self) -> str:
        """一行可读的来源说明，直接进结果页与日志。"""
        parts = [
            self.title or self.page_title or self.page_id,
            "×".join(str(size) for size in self.shape),
        ]
        if self.frame_label:
            parts.append(self.frame_label)
        if self.scope_label:
            parts.append(self.scope_label)
        return " · ".join(part for part in parts if part)


@dataclass(frozen=True)
class BandSurface:
    """一条带的能量面：``z[i, j]`` 是 ``(x[i], y[j])`` 处的能量。

    ``z`` 用 NaN 表示无解/未定；``color`` 与 ``opacity`` 只是显示偏好，阶段 3 的
    三维叠加会用它们，宿主不认识时忽略即可。
    """

    z: np.ndarray
    label: str = ""
    color: str = ""
    opacity: Optional[float] = None


@dataclass(frozen=True)
class AnalysisSurface2D:
    """插件交回的二维面结果：同一动量网格上的若干条带面。

    一批带面共用 ``x`` / ``y`` 坐标与 ``z`` 的标签单位；宿主据此建面结果页
    （逐个带面一页或带选择器由宿主决定），并负责展示与导出。``params`` 必须是
    可序列化的普通数据。
    """

    x: Sequence[float]
    y: Sequence[float]
    surfaces: Sequence[BandSurface]
    x_label: str = "kx"
    x_unit: str = ""
    y_label: str = "ky"
    y_unit: str = ""
    z_label: str = "E"
    z_unit: str = ""
    title: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)

    def x_axis_label(self) -> str:
        return _axis_label(self.x_label, self.x_unit)

    def y_axis_label(self) -> str:
        return _axis_label(self.y_label, self.y_unit)

    def z_axis_label(self) -> str:
        return _axis_label(self.z_label, self.z_unit)


def _monotonic_axis(values, name: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0:
        raise AnalysisResultError(f"面结果的 {name} 坐标为空。")
    if not np.all(np.isfinite(array)):
        raise AnalysisResultError(f"面结果的 {name} 坐标含缺测或非有限值，坐标必须完整。")
    diffs = np.diff(array)
    if not (np.all(diffs > 0) or np.all(diffs < 0)):
        raise AnalysisResultError(f"面结果的 {name} 坐标必须严格单调。")
    return array


def validate_analysis_surface(surface: AnalysisSurface2D) -> AnalysisSurface2D:
    """校验面结果并转成宿主拥有的只读缓冲区。

    - ``x`` / ``y`` 一维、非空、有限、严格单调；
    - 每条带的 ``z`` 形状必须是 ``(len(x), len(y))``；允许 NaN（无解），拒绝 ±inf；
    - 至少一条带、不超过 :data:`MAX_SURFACE_BANDS` 条；标签去重（重复时补序号）。
    """
    if not isinstance(surface, AnalysisSurface2D):
        raise AnalysisResultError(
            f"面结果必须是 AnalysisSurface2D，收到 {type(surface).__name__}。"
        )
    x = _monotonic_axis(surface.x, "x")
    y = _monotonic_axis(surface.y, "y")
    bands = list(surface.surfaces or ())
    if not bands:
        raise AnalysisResultError("面结果里至少需要一条带。")
    if len(bands) > MAX_SURFACE_BANDS:
        raise AnalysisResultError(
            f"面结果最多支持 {MAX_SURFACE_BANDS} 条带，收到 {len(bands)} 条。"
        )

    normalized = []
    used_labels = set()
    for position, band in enumerate(bands):
        if not isinstance(band, BandSurface):
            raise AnalysisResultError(
                f"第 {position + 1} 条带必须是 BandSurface，收到 {type(band).__name__}。"
            )
        try:
            z = np.asarray(band.z, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise AnalysisResultError(f"第 {position + 1} 条带的数值无法解析：{exc}") from exc
        if z.ndim != 2:
            raise AnalysisResultError(f"第 {position + 1} 条带的 z 必须是二维数组。")
        if z.shape != (x.size, y.size):
            raise AnalysisResultError(
                f"第 {position + 1} 条带的 z 形状 {z.shape} 与坐标网格 "
                f"{(x.size, y.size)} 不一致。"
            )
        if np.any(np.isinf(z)):
            raise AnalysisResultError(
                f"第 {position + 1} 条带的 z 含无穷值；无解请用 NaN 表示。"
            )
        label = str(band.label or "").strip() or f"Band {position + 1}"
        if label in used_labels:
            suffix = 2
            while f"{label} ({suffix})" in used_labels:
                suffix += 1
            label = f"{label} ({suffix})"
        used_labels.add(label)

        opacity = band.opacity
        if opacity is not None:
            try:
                opacity = float(opacity)
            except (TypeError, ValueError):
                opacity = None
            else:
                opacity = min(1.0, max(0.0, opacity))
        normalized.append(
            BandSurface(
                z=read_only_array(z),
                label=label,
                color=str(band.color or "").strip(),
                opacity=opacity,
            )
        )

    return AnalysisSurface2D(
        x=read_only_array(x),
        y=read_only_array(y),
        surfaces=tuple(normalized),
        x_label=str(surface.x_label or "kx"),
        x_unit=str(surface.x_unit or ""),
        y_label=str(surface.y_label or "ky"),
        y_unit=str(surface.y_unit or ""),
        z_label=str(surface.z_label or "E"),
        z_unit=str(surface.z_unit or ""),
        title=str(surface.title or ""),
        params=dict(surface.params or {}),
    )


class PluginHostV3(PluginHostV2):
    """API 3 宿主：在 API 2 之上增加三维体数据快照。

    API 1 / API 2 插件看到的句柄行为不变；声明 ``data_snapshot_3d`` 的插件才需要
    这个方法。面结果（``result_surface_2d``）不新增调用接口：宿主在任务成功回调里
    按结果类型建页，插件只负责交回 :class:`AnalysisSurface2D`。
    """

    api_version = 3

    @abc.abstractmethod
    def capture_analysis_input_3d(self) -> AnalysisInput3D:
        """抓取当前三维体数据的只读快照。

        不可用时抛 :class:`AnalysisUnavailable`：未加载数据、结果未完成、数据域为
        空、快照超过宿主上限都会给出可直接展示的原因。
        """
