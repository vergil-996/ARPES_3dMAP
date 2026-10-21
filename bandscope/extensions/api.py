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
    ANALYSIS_CAPABILITIES,
    API_VERSION,
    CAPABILITY_ANALYSIS_TASK,
    CAPABILITY_DATA_SNAPSHOT_2D,
    CAPABILITY_INTEGRATION_ACTION,
    CAPABILITY_DERIVED_RESULT,
    CAPABILITY_OPACITY_MULTIPLIER,
    CAPABILITY_PLUGIN_SETTINGS,
    CAPABILITY_RESULT_CURVE_1D,
    DERIVED_CAPABILITIES,
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
    """把插件给出的倍率对齐到渲染用的体素索引。

    - ``flipped``：传入的倍率是否按**显示方向**排列（需要倒回体素顺序）。
      宿主现在给插件的是原始能量轴，E 轴翻转只镜像画面，所以宿主传 False；
      这个参数保留给仍按显示方向提供倍率的旧调用方。
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

    # -- API 3：集成动作与派生结果（默认空实现） ---------------------------
    def integration_actions(self) -> Sequence["IntegrationAction"]:
        """向「其他积分」下拉注册动作；默认不注册。

        只对声明了派生能力（``DERIVED_CAPABILITIES``）的插件生效。动作键会以
        ``插件 ID:动作 ID`` 存进下拉状态，动作 ID 必须跨版本稳定。
        """
        return ()

    def compute_derived_result(self, source: "DerivedInput2D") -> "DerivedWaterfallResult":
        """冻结来源 → 派生结果；采样、归一化、参数校验与自动标题都在这里完成。

        失败时抛 ``ValueError``（消息可直接展示）或 :class:`PluginError`；宿主
        会先验证结果再提交页面状态。默认实现视为「未提供派生计算」。
        """
        raise PluginError(
            f"插件 {self.plugin_id or '（未命名）'} 未实现派生计算。"
        )

    def create_settings_panel(self, host):
        """创建插件设置界面（``QWidget``）；返回 None 表示不提供设置。

        面板在「插件管理 → 插件设置」里挂载，用宿主句柄读写默认值；控件生命周期
        由宿主管理，插件不得缓存控件实例。
        """
        return None

    def export_settings(self) -> Mapping[str, Any]:
        """当前设置状态；「保存为新建默认值」与「应用到当前页」都用它。"""
        return {}

    def restore_settings(self, state: Mapping[str, Any]) -> None:
        """恢复设置状态；实现必须容忍缺失与多余字段。"""

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
    """

    _event: Any = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return bool(self._event.is_set())

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise AnalysisCancelled("分析任务已取消。")


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
# 派生结果与集成动作（API 3）
# ---------------------------------------------------------------------------
#
# 这一类插件把「其他积分」下拉里的一项换成宿主托管的派生结果页（当前用于
# EDC 瀑布图）：
#
# 1. **注册动作**：插件用 :meth:`Plugin.integration_actions` 报出动作键与展示
#    文本，宿主把它们并入下拉，以 ``插件 ID:动作 ID`` 稳定分派。
# 2. **冻结来源**：用户执行动作时，宿主把当前 X/Y 积分页及其矩形裁剪结果冻结
#    成 :class:`DerivedSource2D`（时间帧或时间积分区间、积分范围、裁剪范围与
#    数据域都在创建时固定），存进结果页参数。
# 3. **插件计算**：宿主把冻结来源还原成只读的 :class:`DerivedInput2D`，调用
#    :meth:`Plugin.compute_derived_result`；采样、归一化、参数校验与自动标题都
#    由插件负责。V3 输入与结果一律使用**原始坐标排列**，E 翻转只由宿主在显示、
#    出版与数值导出的统一出口处理一次。
# 4. **宿主建页展示**：宿主校验结果后建 ``plugin_derived`` 结果页，负责绘图、
#    裁剪、出版与数值导出；插件不建页、不写文件、不碰主窗口。
# 5. **设置与默认值**：插件提供设置界面（:meth:`Plugin.create_settings_panel`），
#    宿主按插件 ID 把默认值存进 QSettings，与逐页参数分离；「应用当前页」先
#    验证计算结果再提交页面状态，失败时保留上次成功结果。

#: 派生结果中的曲线偏移步长默认值，与旧瀑布图行为一致。
DEFAULT_WATERFALL_OFFSET_STEP = 1.2

#: 「其他积分」下拉里插件动作的默认排序权重；内置项按 0/10/20/30/40 排布。
INTEGRATION_ACTION_DEFAULT_ORDER = 50.0


class DerivedSourceUnavailable(PluginError):
    """当前页面不能作为派生来源。

    消息直接面向用户：不是 X/Y 积分页、带擦除区域、裁剪范围内没有有效数据等
    情况都归这里，逐条说明为什么现在不能创建。
    """


class DerivedResultError(PluginError):
    """插件交回的派生结果不符合契约（形状、坐标或采样身份不一致）。"""


@dataclass(frozen=True)
class IntegrationAction:
    """插件注册到「其他积分」下拉的一个动作。

    ``action_id`` 是稳定键：跨版本保持不变，下拉状态按 ``插件 ID:动作 ID``
    保存与恢复；``label`` 只用于展示，可以随版本改写。
    """

    action_id: str
    label: str
    order: float = INTEGRATION_ACTION_DEFAULT_ORDER

    def key(self, plugin_id: str) -> str:
        return f"{str(plugin_id)}:{self.action_id}"


@dataclass(frozen=True)
class DerivedAxis:
    """一根轴的只读描述（原始坐标排列，不做显示翻转）。

    单位只如实转述：没有单位元数据时保持空串，``display_unit`` 依次回退到
    「坐标值」与 ``index``，绝不自动补成 ``eV`` / ``Å⁻¹``。
    """

    key: str
    label: str
    values: np.ndarray
    unit: str = ""
    source: str = SOURCE_INDEX

    @property
    def has_coordinates(self) -> bool:
        return self.source == SOURCE_FILE

    @property
    def display_unit(self) -> str:
        if not self.has_coordinates:
            return UNIT_TEXT_INDEX
        unit = str(self.unit or "").strip()
        return unit or UNIT_TEXT_NO_METADATA

    @property
    def axis_label(self) -> str:
        return f"{str(self.label)} ({self.display_unit})"

    def sample_spacing(self) -> float:
        """相邻采样点的中位间距；不足以计算时返回 0.0。"""
        values = np.asarray(self.values, dtype=np.float64).reshape(-1)
        if values.size < 2:
            return 0.0
        diffs = np.abs(np.diff(values))
        diffs = diffs[np.isfinite(diffs) & (diffs > 1e-12)]
        return float(np.median(diffs)) if diffs.size else 0.0


@dataclass(frozen=True)
class DerivedSource2D:
    """宿主在创建时冻结的派生来源（可序列化，随结果页参数保存）。

    冻结之后页面只依赖这份记录重算：之后切换时间帧、改动积分区间或重新裁剪
    来源页都不会影响已有结果页。
    """

    plugin_id: str
    action_id: str
    source_page_id: str
    source_page_kind: str
    axis_index: int
    integral_low: int
    integral_up: int
    integral_mid: int
    source_mode: str = "frame"
    frame_index: int = 0
    time_low: int = 0
    time_up: int = 0
    scope_id: str = "full"
    crop_rect: Optional[Tuple[int, int, int, int]] = None
    source_title: str = ""
    data_generation: int = 0

    def to_mapping(self) -> Dict[str, Any]:
        payload = {
            "plugin_id": self.plugin_id,
            "action_id": self.action_id,
            "source_page_id": self.source_page_id,
            "source_page_kind": self.source_page_kind,
            "axis_index": int(self.axis_index),
            "integral_low": int(self.integral_low),
            "integral_up": int(self.integral_up),
            "integral_mid": int(self.integral_mid),
            "source_mode": str(self.source_mode),
            "frame_index": int(self.frame_index),
            "time_low": int(self.time_low),
            "time_up": int(self.time_up),
            "scope_id": str(self.scope_id),
            "crop_rect": None if self.crop_rect is None else [int(v) for v in self.crop_rect],
            "source_title": str(self.source_title),
            "data_generation": int(self.data_generation),
        }
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DerivedSource2D":
        crop = value.get("crop_rect")
        return cls(
            plugin_id=str(value.get("plugin_id") or ""),
            action_id=str(value.get("action_id") or ""),
            source_page_id=str(value.get("source_page_id") or ""),
            source_page_kind=str(value.get("source_page_kind") or ""),
            axis_index=int(value.get("axis_index") or 0),
            integral_low=int(value.get("integral_low") or 0),
            integral_up=int(value.get("integral_up") or 0),
            integral_mid=int(value.get("integral_mid") or 0),
            source_mode=str(value.get("source_mode") or "frame"),
            frame_index=int(value.get("frame_index") or 0),
            time_low=int(value.get("time_low") or 0),
            time_up=int(value.get("time_up") or 0),
            scope_id=str(value.get("scope_id") or "full"),
            crop_rect=None if crop is None else tuple(int(v) for v in crop),
            source_title=str(value.get("source_title") or ""),
            data_generation=int(value.get("data_generation") or 0),
        )

    def describe(self) -> str:
        parts = [
            self.source_title or self.source_page_id,
            f"积分轴 {'XY'[int(self.axis_index)] if int(self.axis_index) in (0, 1) else self.axis_index}",
            f"{int(self.integral_low)}~{int(self.integral_up)}",
        ]
        if self.source_mode == "time_integral":
            parts.append(f"时间积分 {int(self.time_low)}~{int(self.time_up)}")
        else:
            parts.append(f"帧 {int(self.frame_index)}")
        if self.crop_rect is not None:
            parts.append("矩形裁剪")
        return " · ".join(str(part) for part in parts if part)


@dataclass(frozen=True)
class DerivedInput2D:
    """传给 :meth:`Plugin.compute_derived_result` 的只读输入。

    ``data`` 的顺序是 ``[采样轴, 曲线轴]``（当前为 ``[k, E]``），与 ``k_axis`` /
    ``e_axis`` 一一对应，**原始坐标排列**：宿主不在输入里做 E 翻转。完整轴
    （``k_axis_full`` / ``e_axis_full`` / ``integrated_axis``）保留未裁剪的坐标，
    供自动步长与标题使用。所有数组都是任务独占的只读缓冲区。
    """

    plugin_id: str
    source: DerivedSource2D
    params: Mapping[str, Any]
    data: np.ndarray
    k_axis: DerivedAxis
    e_axis: DerivedAxis
    k_axis_full: DerivedAxis
    e_axis_full: DerivedAxis
    integrated_axis: DerivedAxis
    source_title: str = ""
    frame_label: str = ""
    roi_label: str = ""
    data_generation: int = 0

    @property
    def shape(self) -> Tuple[int, int]:
        return (int(self.data.shape[0]), int(self.data.shape[1]))

    def describe(self) -> str:
        parts = [self.source_title or self.source.source_page_id, f"{self.shape[0]}×{self.shape[1]}"]
        if self.frame_label:
            parts.append(self.frame_label)
        if self.roi_label:
            parts.append(self.roi_label)
        return " · ".join(part for part in parts if part)


@dataclass(frozen=True)
class DerivedWaterfallResult:
    """插件交回的瀑布类派生结果：宿主负责建页、展示与导出。

    - ``k`` / ``sample_indices``：抽样后的物理动量坐标与它们在**完整未裁剪**动量
      轴上的下标（物理 k 升序）；采样身份由 ``sample_indices`` 唯一确定。
    - ``energy``：原始坐标排列的能量轴；``curves`` / ``raw_curves`` 的第二维与
      它一一对应。
    - ``curve_offsets`` 为 None 时宿主按 ``offset_step`` 生成均匀偏移。
    - ``title`` 是自动标题（含裁剪后缀），宿主只读展示，不识别具体插件；
      ``plot_title`` 是绘图/出版用的 ASCII 安全标题，为空时回退 ``title``
      （宿主画布不含中文字形，中文标题会出方框）。
    - ``params`` 记录解析后的参数（例如自动步长实际取到的值），供回显与复现。
    """

    k: Sequence[float]
    sample_indices: Sequence[int]
    energy: Sequence[float]
    raw_curves: np.ndarray
    curves: np.ndarray
    curve_offsets: Optional[Sequence[float]] = None
    offset_step: float = DEFAULT_WATERFALL_OFFSET_STEP
    title: str = ""
    plot_title: str = ""
    xlabel: str = "Intensity (normalized, arb. u.)"
    ylabel: str = "Energy"
    k_label: str = "k"
    integrated_label: str = ""
    integrated_range: Optional[Tuple[float, float]] = None
    integrated_center: Optional[float] = None
    params: Mapping[str, Any] = field(default_factory=dict)


def validate_derived_waterfall(
    result: DerivedWaterfallResult,
    source: Optional[DerivedInput2D] = None,
) -> DerivedWaterfallResult:
    """校验插件派生结果并转成宿主拥有的只读数值缓冲区。

    - 必须是 :class:`DerivedWaterfallResult`；
    - ``k`` / ``sample_indices`` / ``energy`` 必须一维、非空、等长；
    - ``curves`` / ``raw_curves`` 必须是 ``[len(k), len(energy)]`` 的二维数组；
    - ``k`` 与 ``energy`` 必须有限且严格排序（物理 k 升序、能量原始方向单调）；
    - 曲线允许 NaN 表示缺测，但拒绝正负无穷；
    - ``sample_indices`` 必须整数、非负，并落在完整动量轴范围内（有输入时）。
    """
    if not isinstance(result, DerivedWaterfallResult):
        raise DerivedResultError(
            f"派生结果必须是 DerivedWaterfallResult，收到 {type(result).__name__}。"
        )
    try:
        k = np.asarray(result.k, dtype=np.float64).reshape(-1)
        sample_indices = np.asarray(result.sample_indices)
        energy = np.asarray(result.energy, dtype=np.float64).reshape(-1)
        raw_curves = np.asarray(result.raw_curves, dtype=np.float64)
        curves = np.asarray(result.curves, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise DerivedResultError(f"派生结果不是数值数组：{exc}") from exc
    if k.size == 0 or energy.size == 0:
        raise DerivedResultError("派生结果为空：至少需要一条曲线和一根能量轴。")
    if k.size != sample_indices.size:
        raise DerivedResultError("采样身份（sample_indices）与 k 坐标长度不一致。")
    if raw_curves.shape != curves.shape:
        raise DerivedResultError("归一化曲线与原始曲线形状不一致。")
    if curves.ndim != 2 or curves.shape != (k.size, energy.size):
        raise DerivedResultError(
            f"曲线形状 {curves.shape} 与坐标（{k.size} 条 × {energy.size} 点）不一致。"
        )
    if not np.all(np.isfinite(k)):
        raise DerivedResultError("动量坐标含缺测或非有限值，坐标必须完整。")
    if not np.all(np.isfinite(energy)):
        raise DerivedResultError("能量坐标含缺测或非有限值，坐标必须完整。")
    if k.size > 1 and np.any(np.diff(k) < 0):
        raise DerivedResultError("动量坐标必须按物理 k 升序排列。")
    if energy.size > 1 and np.any(np.diff(energy) == 0):
        raise DerivedResultError("能量坐标出现重复采样点。")
    if np.any(np.isinf(raw_curves)) or np.any(np.isinf(curves)):
        raise DerivedResultError("曲线含无穷值；缺测请用 NaN 表示。")
    if np.issubdtype(sample_indices.dtype, np.floating):
        if not np.all(sample_indices == np.floor(sample_indices)):
            raise DerivedResultError("采样身份（sample_indices）必须是整数下标。")
    sample_indices = sample_indices.astype(np.int64, copy=False)
    if np.any(sample_indices < 0):
        raise DerivedResultError("采样身份（sample_indices）不能为负数。")
    if source is not None:
        limit = int(np.asarray(source.k_axis_full.values).size)
        if limit and np.any(sample_indices >= limit):
            raise DerivedResultError(
                f"采样身份超出完整动量轴范围（0~{limit - 1}）。"
            )

    offsets = result.curve_offsets
    if offsets is not None:
        offsets = np.asarray(offsets, dtype=np.float64).reshape(-1)
        if offsets.size != k.size:
            raise DerivedResultError("曲线偏移与曲线数量不一致。")
        if not np.all(np.isfinite(offsets)):
            raise DerivedResultError("曲线偏移含非有限值。")
    offset_step = float(result.offset_step)
    if not np.isfinite(offset_step) or offset_step <= 0:
        raise DerivedResultError("偏移步长必须是有限正数。")

    def _frozen(values: np.ndarray) -> np.ndarray:
        array = np.array(values, dtype=np.float64, copy=True)
        array.setflags(write=False)
        return array

    integrated_range = result.integrated_range
    if integrated_range is not None:
        integrated_range = tuple(float(v) for v in integrated_range)
    integrated_center = (
        None if result.integrated_center is None else float(result.integrated_center)
    )
    return DerivedWaterfallResult(
        k=_frozen(k),
        sample_indices=np.array(sample_indices, dtype=np.int64, copy=True),
        energy=_frozen(energy),
        raw_curves=_frozen(raw_curves),
        curves=_frozen(curves),
        curve_offsets=None if offsets is None else _frozen(offsets),
        offset_step=offset_step,
        title=str(result.title or ""),
        plot_title=str(result.plot_title or ""),
        xlabel=str(result.xlabel or "Intensity (normalized, arb. u.)"),
        ylabel=str(result.ylabel or "Energy"),
        k_label=str(result.k_label or "k"),
        integrated_label=str(result.integrated_label or ""),
        integrated_range=integrated_range,
        integrated_center=integrated_center,
        params=dict(result.params or {}),
    )


@dataclass(frozen=True)
class DerivedPage:
    """当前派生结果页的只读描述（插件查询用）。"""

    plugin_id: str
    action_id: str
    page_id: str
    title: str
    params: Mapping[str, Any]
    source: DerivedSource2D


class PluginHostV3(PluginHostV2):
    """API 3 宿主：在 v2 的分析能力之外增加派生结果与设置。

    v1 / v2 插件拿到的仍是各自版本的接口；实现由主程序提供，图形操作一律由
    宿主在主线程完成。
    """

    api_version = 3

    # -- 默认值（宿主按插件 ID 保存，与逐页参数分离） ----------------------
    @abc.abstractmethod
    def defaults(self) -> Mapping[str, Any]:
        """该插件保存为「新建默认值」的参数；没有保存过时返回空字典。"""

    @abc.abstractmethod
    def set_defaults(self, values: Mapping[str, Any]) -> None:
        """保存默认值；只影响之后新建的派生页，已有页保持各自参数。"""

    # -- 当前派生结果页 ----------------------------------------------------
    @abc.abstractmethod
    def current_derived_page(self) -> Optional[DerivedPage]:
        """当前页是本插件创建的派生结果页时返回描述，否则返回 None。"""

    @abc.abstractmethod
    def update_current_derived_page(self, params: Mapping[str, Any]) -> bool:
        """把参数应用到当前派生页：先验证计算结果，通过后再提交。

        失败时保留上次成功结果与原有参数，原因由宿主展示；返回是否提交成功。
        """
