# -*- coding: utf-8 -*-
"""把扩展接进主窗口的适配层。

主程序只在这里认识插件。渲染、页面状态、导出快照这些核心路径只消费
:class:`plugin_api.PluginContext` 与一维倍率，不感知具体插件。

四类能力（计划 §4）分别落在这里：

1. **上下文**：:meth:`PluginSession.build_context` 从渲染上下文取显示方向的
   能量轴、ROI、帧号和显示翻转，交给插件。
2. **面板注册**：:meth:`PluginSession.mount_cards` 把插件面板挂成「渲染控制」
   页里的卡片，宿主负责显隐与生命周期。
3. **效果提交**：:meth:`PluginSession.voxel_multiplier` 把各插件的一维倍率合成、
   校验并对齐到体素索引；三维 alpha 由 ``render_core`` 生成。
4. **状态与刷新**：:meth:`PluginSession.store_page_state` /
   :meth:`restore_page_state` 按页面保存参数，刷新走宿主既有的节流管线。
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Mapping, Optional

import numpy as np
from PyQt5.QtCore import QObject, pyqtSlot

from bandscope.extensions.analysis_host import AnalysisTaskRunner
from bandscope.extensions.api import (
    ANALYSIS_ARRAY_ORDER,
    ANALYSIS_CAPABILITIES,
    SOURCE_FILE,
    AnalysisInput2D,
    AnalysisResultError,
    AnalysisTaskHandle,
    AnalysisUnavailable,
    EnergyAxisSpec,
    PluginContext,
    PluginHostV2,
    align_multiplier_to_voxels,
    read_only_array,
    validate_analysis_curve,
)
from bandscope.extensions.plugin_manager import PluginManager

#: 每页插件参数在 ``spec.params`` 里的键。
PAGE_STATE_KEY = "plugins"

#: 插件效果在宿主刷新管线里的失效原因标签。
EFFECT_CAUSE_LABEL = "plugin"

#: 结果页里记录插件分析来源的参数键。
ANALYSIS_PARAMS_KEY = "plugin_analysis"


class _HostBridge(PluginHostV2):
    """交给插件的宿主句柄；只暴露协议允许的动作。

    每个插件拿到的句柄绑定自己的 ``plugin_id``：分析任务的排队与限制是按插件
    计的，句柄必须知道它在替谁提交。
    """

    api_version = 2

    def __init__(self, session: "PluginSession", plugin_id: str = ""):
        self._session = session
        self._plugin_id = str(plugin_id or "")

    # -- v1 -------------------------------------------------------------
    def context(self) -> PluginContext:
        return self._session.current_context()

    def request_refresh(self, *, immediate: bool = False) -> None:
        self._session.request_refresh(immediate=immediate)

    def notify(self, message: str, *, level: str = "info") -> None:
        self._session.notify(message, level=level)

    # -- v2 -------------------------------------------------------------
    def capture_analysis_input(self) -> AnalysisInput2D:
        return self._session.capture_analysis_input(self._plugin_id)

    def submit_analysis(self, snapshot, work, *, title="", params=None):
        return self._session.submit_analysis(
            self._plugin_id, snapshot, work, title=title, params=params
        )

    def cancel_analysis(self, handle) -> None:
        self._session.cancel_analysis(handle)


def _region_operation(region) -> str:
    """裁剪/擦除记录的操作类型；页面参数里存的是 ``CropSelection.to_dict()``。"""
    if isinstance(region, Mapping):
        return str(region.get("operation") or "")
    return str(getattr(region, "operation", "") or "")


class _AnalysisGateway(QObject):
    """把工作线程的结果带回主线程的接收者。

    信号由工作线程发出；这里是一个主线程里的 QObject，Qt 因此自动用队列连接，
    建结果页等图形操作一定发生在主线程。
    """

    def __init__(self, session: "PluginSession", parent=None):
        super().__init__(parent)
        self._session = session

    @pyqtSlot(object, object)
    def on_succeeded(self, handle, curve) -> None:
        self._session.on_analysis_succeeded(handle, curve)

    @pyqtSlot(object, str)
    def on_failed(self, handle, message) -> None:
        self._session.on_analysis_failed(handle, message)

    @pyqtSlot(object, str)
    def on_cancelled(self, handle, reason) -> None:
        self._session.on_analysis_cancelled(handle, reason)

    @pyqtSlot(str, str)
    def on_busy(self, plugin_id, reason) -> None:
        self._session.on_analysis_busy(plugin_id, reason)


class PluginSession:
    """一个主窗口对应一个会话。"""

    def __init__(self, window, *, manager: Optional[PluginManager] = None):
        self.window = window
        self.manager = manager or PluginManager()
        self.host = _HostBridge(self)
        self._hosts: Dict[str, _HostBridge] = {"": self.host}
        self._context = PluginContext()
        self._cards: Dict[str, object] = {}
        self._analysis_cards: Dict[str, object] = {}
        self._failed: Dict[str, str] = {}
        # 分析任务：快照只留在宿主手里，结果页也由宿主创建。
        self._snapshots: Dict[str, AnalysisInput2D] = {}
        self._analysis_runner = AnalysisTaskRunner()
        self._analysis_gateway = _AnalysisGateway(self)
        self._analysis_runner.succeeded.connect(self._analysis_gateway.on_succeeded)
        self._analysis_runner.failed.connect(self._analysis_gateway.on_failed)
        self._analysis_runner.cancelled.connect(self._analysis_gateway.on_cancelled)
        self._analysis_runner.busy.connect(self._analysis_gateway.on_busy)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def startup(self) -> None:
        """Qt 与宿主接口就绪后调用：先执行待卸载，再加载扩展。"""
        self.manager.startup()

    def shutdown(self) -> None:
        """实例释放只由 PluginManager 的统一入口执行，恰好一次。"""
        self._cards.clear()
        self._analysis_cards.clear()
        self._snapshots.clear()
        self._analysis_runner.shutdown()
        self.manager.shutdown()

    def host_for(self, plugin_id: str) -> _HostBridge:
        """按插件取宿主句柄；句柄知道自己在替哪个插件提交分析任务。"""
        key = str(plugin_id or "")
        bridge = self._hosts.get(key)
        if bridge is None:
            bridge = _HostBridge(self, key)
            self._hosts[key] = bridge
        return bridge

    # ------------------------------------------------------------------
    # 能力 1：上下文
    # ------------------------------------------------------------------
    def current_context(self) -> PluginContext:
        return self._context

    def build_context(self, window, render_context) -> PluginContext:
        """按当前渲染上下文生成插件上下文；不触发任何渲染。"""
        spec = window.left_workspace.current_spec() or window.left_workspace.home_spec()
        view = str((render_context or {}).get("view") or "")
        axis = self._energy_axis(window, render_context) if view == "3d" else None
        frame_index = None
        frame_label = ""
        try:
            frame_index = int(window.timeline_bar.slider_time.value())
            frame_label = str(window._current_delay_text(frame_index))
        except Exception:
            frame_index = None

        context = PluginContext(
            page_id=str(getattr(spec, "page_id", "") or ""),
            page_title=str(getattr(spec, "title", "") or ""),
            view=view,
            energy=axis,
            frame_index=frame_index,
            frame_label=frame_label,
            display_e_flip=self._e_flip(window),
            roi_label=self._roi_label(axis),
        )
        self._context = context
        return context

    @staticmethod
    def _e_flip(window) -> bool:
        try:
            return bool(window.timeline_bar.switch_flip.isChecked())
        except Exception:
            return False

    @staticmethod
    def _roi_label(axis) -> str:
        if axis is None:
            return ""
        low, high = axis.roi_range
        return f"{min(low, high):.5g} ~ {max(low, high):.5g} {axis.display_unit}".strip()

    def _energy_axis(self, window, render_context) -> Optional[EnergyAxisSpec]:
        """显示方向的完整能量轴 + 当前 ROI 的能量范围。

        倍率始终定义在**完整**能量轴上：compact ROI 只是截取，不重新拉伸。
        """
        if not render_context:
            return None
        coords = render_context.get("coords") or {}
        values = coords.get("E")
        true_values = self._true_energy_axis(window)
        if values is None or true_values is None:
            return None
        values = np.asarray(values, dtype=np.float64).reshape(-1)
        if values.size == 0:
            return None

        bounds = render_context.get("data_bounds")
        if bounds is not None and len(bounds) >= 6:
            start, stop = int(bounds[4]), int(bounds[5])
            start = max(0, min(start, true_values.size - 1))
            stop = max(start, min(stop, true_values.size - 1))
            roi = (float(true_values[start]), float(true_values[stop]))
        else:
            roi = (float(np.min(values)), float(np.max(values)))

        source, unit = self._energy_meta(window)
        return EnergyAxisSpec(
            values=values,
            unit=unit,
            source=source,
            roi_range=roi,
            full_range=(float(np.min(true_values)), float(np.max(true_values))),
        )

    @staticmethod
    def _true_energy_axis(window) -> Optional[np.ndarray]:
        """未经显示翻转的能量轴：体素索引到能量的唯一权威映射。"""
        try:
            values = window.core.coords.get("E")
        except Exception:
            values = None
        if values is None:
            return None
        array = np.asarray(values, dtype=np.float64).reshape(-1)
        return array if array.size else None

    @staticmethod
    def _energy_meta(window):
        """能量轴的单位元数据；只如实转述，缺单位不猜。"""
        core = getattr(window, "core", None)
        sources = dict(getattr(core, "coord_sources", {}) or {})
        units = dict(getattr(core, "coord_units", {}) or {})
        if str(sources.get("E", "index")) != SOURCE_FILE:
            return "index", None
        raw_unit = units.get("E")
        unit = str(raw_unit).strip() if raw_unit else ""
        return SOURCE_FILE, (unit or None)

    def notify_context(self, context: PluginContext) -> None:
        """把上下文交给每个插件；单个插件出错不影响其它插件与渲染。"""
        for record in self.manager.ready_plugins():
            try:
                record.instance.on_context_changed(context)
            except Exception as exc:
                self._failed[record.plugin_id] = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # 能力 3：效果提交
    # ------------------------------------------------------------------
    def voxel_multiplier(
        self,
        context: PluginContext,
        render_context,
        *,
        stride: int = 1,
    ):
        """合成各插件的倍率并对齐到渲染体素的能量轴。

        返回 ``None`` 表示不使用任何效果（未安装插件、当前不是 3D、没有插件
        提交倍率，或所有插件都选择不参与）。
        """
        records = self.manager.ready_plugins()
        if not records or context.energy is None or context.view != "3d":
            return None

        combined = None
        for record in records:
            try:
                vector = record.instance.opacity_multiplier(context)
            except Exception as exc:
                self._failed[record.plugin_id] = f"{type(exc).__name__}: {exc}"
                continue
            if vector is None:
                continue
            values = np.asarray(vector, dtype=np.float64).reshape(-1)
            if values.size != context.energy.values.size:
                self._failed[record.plugin_id] = (
                    f"倍率长度 {values.size} 与能量轴 {context.energy.values.size} 不一致，已忽略。"
                )
                continue
            if not np.all(np.isfinite(values)):
                values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
            values = np.maximum(values, 0.0)
            combined = values if combined is None else np.maximum(combined, values)

        if combined is None:
            return None

        bounds = render_context.get("data_bounds") if render_context else None
        e_bounds = (int(bounds[4]), int(bounds[5])) if bounds is not None and len(bounds) >= 6 else None
        try:
            aligned = align_multiplier_to_voxels(
                combined,
                flipped=bool(context.display_e_flip),
                e_bounds=e_bounds,
                stride=int(stride),
            )
        except ValueError as exc:
            self._failed["__align__"] = str(exc)
            return None
        return aligned

    # ------------------------------------------------------------------
    # 能力 2：面板注册
    # ------------------------------------------------------------------
    def mount_cards(self, page_render) -> None:
        """把每个可用插件的面板挂到「渲染控制」页；失败只提示，不影响界面。

        实例化与面板初始化全部成功之后才向登记表回报健康（``last_good``）；
        面板创建或挂载失败时立即释放实例并记录失败候选，等重启后由用户
        「重试加载」或「恢复上一版本」。
        """
        for record in self.manager.ready_plugins():
            if record.plugin_id in self._cards:
                continue
            if self.is_analysis_plugin(record):
                # 声明了分析能力的插件走「处理分析」页的挂载点，面板只在二维
                # 结果页显示；这里跳过，避免同一插件挂出两份面板。
                continue
            try:
                panel = record.instance.create_panel(self.host_for(record.plugin_id))
            except Exception as exc:
                message = f"面板创建失败：{type(exc).__name__}: {exc}"
                self._failed[record.plugin_id] = message
                self.manager.report_init_failure(record, message)
                continue
            if panel is None:
                # 返回无面板是合法插件，允许成功。
                self.manager.mark_healthy(record.plugin_id)
                continue
            title = str(getattr(record.manifest, "name", "") or record.plugin_id)
            try:
                card = page_render.mount_extension_card(record.plugin_id, title, panel)
            except Exception as exc:
                message = f"面板挂载失败：{type(exc).__name__}: {exc}"
                self._failed[record.plugin_id] = message
                self.manager.report_init_failure(record, message)
                continue
            self._cards[record.plugin_id] = card
            self.manager.mark_healthy(record.plugin_id)

    def set_cards_visible(self, visible: bool, *, animate: bool = True) -> None:
        for plugin_id, card in self._cards.items():
            try:
                self.window.page_render.set_extension_card_visible(
                    plugin_id, visible, animate=animate
                )
            except Exception:
                pass

    def card_ids(self) -> List[str]:
        return list(self._cards)

    # ------------------------------------------------------------------
    # 能力 4：状态与刷新
    # ------------------------------------------------------------------
    def store_page_state(self, spec) -> None:
        if spec is None or not self.manager.records:
            return
        payload = dict(spec.params.get(PAGE_STATE_KEY) or {})
        for record in self.manager.plugins():
            if record.instance is None:
                continue
            try:
                state = record.instance.export_state()
            except Exception:
                continue
            if state:
                payload[record.plugin_id] = state
        if payload:
            spec.params[PAGE_STATE_KEY] = payload

    def restore_page_state(self, spec) -> None:
        """恢复该页参数；没有记录时保持当前内存状态（不重置已有设置）。"""
        if spec is None:
            return
        payload = spec.params.get(PAGE_STATE_KEY) or {}
        for record in self.manager.plugins():
            if record.instance is None:
                continue
            state = payload.get(record.plugin_id)
            if not state:
                continue
            try:
                record.instance.restore_state(state)
            except Exception as exc:
                self._failed[record.plugin_id] = f"参数恢复失败：{type(exc).__name__}: {exc}"

    def reset_for_new_data(self) -> None:
        """加载新的数据文件：清空与旧数据绑定的峰位，并作废进行中的分析。"""
        # 先取消：正在跑的任务会在检查点退出，晚到的结果也会被数据代次核对丢弃。
        self._analysis_runner.cancel_all()
        for record in self.manager.plugins():
            if record.instance is None:
                continue
            try:
                record.instance.reset_for_new_data()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 能力 5：分析（API 2）
    # ------------------------------------------------------------------
    @staticmethod
    def is_analysis_plugin(record) -> bool:
        """是否声明了完整的分析能力（三项缺一不可）。"""
        manifest = getattr(record, "manifest", None)
        capabilities = set(getattr(manifest, "capabilities", ()) or ())
        return set(ANALYSIS_CAPABILITIES).issubset(capabilities)

    @staticmethod
    def _analysis_unavailable(spec, context) -> str:
        """能不能分析；能则返回空串，不能则返回可展示的原因。"""
        if spec is None:
            return "当前没有可分析的页面。"
        if context is None:
            return "当前页还没有完成的计算结果，请等计算完成后重试。"
        if context.get("view") != "2d":
            return "分析只支持二维数值结果页；当前页不是二维结果。"
        if context.get("crop_empty"):
            return "当前裁剪范围内没有有效数据。"
        if any(_region_operation(region) == "erase" for region in (spec.params.get("crop_regions") or ())):
            # 擦除把数据打成 NaN 掩膜，形状还在但已经不是矩形结果；首版不支持。
            return "首版分析不支持带擦除区域的结果，请改用矩形裁剪后的二维结果。"
        return ""

    @staticmethod
    def _slice_axis(values, low, high, length: int):
        """按页面记录的逻辑区间从坐标数组中取出与结果维度等长的一段。"""
        if values is None:
            return None
        array = np.asarray(values, dtype=np.float64).reshape(-1)
        try:
            start = max(0, int(low if low is not None else 0))
            stop = int(high) if high is not None else length - 1
        except (TypeError, ValueError):
            return None
        stop = min(stop, array.size - 1)
        if stop < start:
            return None
        sliced = array[start : stop + 1]
        return sliced if sliced.size == length else None

    @staticmethod
    def _coordinate_meta(window, key: str):
        """坐标键对应的单位元数据；只如实转述，缺单位不猜。"""
        core = getattr(window, "core", None)
        sources = dict(getattr(core, "coord_sources", {}) or {})
        units = dict(getattr(core, "coord_units", {}) or {})
        if str(sources.get(key, "index")) != SOURCE_FILE:
            return "index", None
        raw_unit = units.get(key)
        unit = str(raw_unit).strip() if raw_unit else ""
        return SOURCE_FILE, (unit or None)

    @staticmethod
    def _current_frame(window):
        try:
            index = int(window.timeline_bar.slider_time.value())
            return index, str(window._current_delay_text(index))
        except Exception:
            return None, ""

    def capture_analysis_input(self, plugin_id: str) -> AnalysisInput2D:
        """抓取当前二维结果的只读快照。

        只接受**已完成**的精确结果：预览中、计算未完成、非二维页，以及带擦除
        区域的裁空状态都会抛出 :class:`AnalysisUnavailable`，原因直接面向用户。
        """
        window = self.window
        spec = window.left_workspace.current_spec() or window.left_workspace.home_spec()
        context = getattr(window, "current_render_context", None)
        reason = self._analysis_unavailable(spec, context)
        if reason:
            raise AnalysisUnavailable(reason)
        if not getattr(window, "_render_exact_ready", False):
            raise AnalysisUnavailable("当前结果正在计算中，请等计算完成后重试。")

        data = np.asarray(context.get("data"), dtype=np.float64)
        if data.ndim != 2 or data.size == 0:
            raise AnalysisUnavailable("当前结果不是二维数组，无法分析。")
        coords = context.get("coords") or {}
        plot_axes = context.get("plot_axes") or {}
        bounds = context.get("plot_logical_bounds") or {}
        x_key = str(plot_axes.get("x_key") or "")
        y_key = str(plot_axes.get("y_key") or "")
        x_values = self._slice_axis(
            coords.get(x_key), bounds.get("x_low"), bounds.get("x_up"), data.shape[0]
        )
        y_values = self._slice_axis(
            coords.get(y_key), bounds.get("y_low"), bounds.get("y_up"), data.shape[1]
        )
        if x_values is None or y_values is None:
            raise AnalysisUnavailable("坐标与结果维度不一致，无法生成可分析的快照。")

        _, x_unit = self._coordinate_meta(window, x_key)
        _, y_unit = self._coordinate_meta(window, y_key)
        frame_index, frame_label = self._current_frame(window)
        slice_info = dict(context.get("slice_info") or {})
        return AnalysisInput2D(
            plugin_id=str(plugin_id or ""),
            page_id=str(spec.page_id),
            page_title=str(spec.title),
            snapshot_id=uuid.uuid4().hex,
            data_generation=int(getattr(window, "shared_denoise_version", 0) or 0),
            # 复制成任务独占的只读缓冲区：插件改不动，也不必自己再复制一份。
            data=read_only_array(data),
            x=read_only_array(x_values),
            y=read_only_array(y_values),
            x_label=str(plot_axes.get("x_label") or x_key or "x"),
            x_unit=str(x_unit or ""),
            y_label=str(plot_axes.get("y_label") or y_key or "y"),
            y_unit=str(y_unit or ""),
            title=str(spec.title),
            frame_index=frame_index,
            frame_label=frame_label,
            roi_label=str(slice_info.get("title_override") or ""),
            scope_id=str(getattr(spec, "data_scope_id", "full") or "full"),
            source={
                "page_kind": str(spec.page_kind),
                "scope_label": str(spec.params.get("data_scope_label") or ""),
                "plot_logical_bounds": dict(bounds),
                "slice_info": slice_info,
                "data_order": ANALYSIS_ARRAY_ORDER,
            },
        )

    def submit_analysis(self, plugin_id, snapshot, work, *, title="", params=None):
        """提交一次后台分析；忙碌时提示用户并返回 None。"""
        if not isinstance(snapshot, AnalysisInput2D):
            self.notify("分析快照无效，请重新抓取后再提交。", level="warning")
            return None
        handle = self._analysis_runner.submit(
            str(plugin_id or ""), snapshot, work, title=title, params=params
        )
        if handle is not None:
            self._snapshots[handle.task_id] = snapshot
        return handle

    def cancel_analysis(self, handle) -> None:
        self._analysis_runner.cancel(handle)

    def cancel_page_analysis(self, page_id: str) -> int:
        """来源页关闭时取消它名下的分析任务。"""
        return self._analysis_runner.cancel_page(str(page_id))

    # -- 任务回调（均在主线程） -------------------------------------------
    def on_analysis_busy(self, plugin_id, reason) -> None:
        self.notify(f"插件「{plugin_id}」暂时不能提交分析：{reason}", level="warning")

    def on_analysis_failed(self, handle, message) -> None:
        self._snapshots.pop(getattr(handle, "task_id", ""), None)
        self._failed[str(getattr(handle, "plugin_id", ""))] = f"分析失败：{message}"
        self.notify(f"分析任务失败：{message}", level="error")
        self._notify_plugin_task(handle, "failed", message)

    def on_analysis_cancelled(self, handle, reason) -> None:
        self._snapshots.pop(getattr(handle, "task_id", ""), None)
        self._notify_plugin_task(handle, "cancelled", reason)

    def _notify_plugin_task(self, handle, status: str, detail: str = "") -> None:
        """把任务结束通知回插件；插件回调出错不影响别的插件。"""
        plugin_id = str(getattr(handle, "plugin_id", ""))
        record = self.manager.record(plugin_id)
        instance = getattr(record, "instance", None) if record is not None else None
        if instance is None:
            return
        try:
            instance.on_analysis_finished(handle, status, detail)
        except Exception as exc:
            self._failed[plugin_id] = f"分析回调失败：{type(exc).__name__}: {exc}"

    def on_analysis_succeeded(self, handle, curve) -> None:
        """主线程收结果：校验 → 核对来源 → 建结果页（不切换当前页）。"""
        snapshot = self._snapshots.pop(getattr(handle, "task_id", ""), None)
        if snapshot is None:
            return
        try:
            validated = validate_analysis_curve(curve)
        except AnalysisResultError as exc:
            self.on_analysis_failed(handle, str(exc))
            return
        window = self.window
        if self._result_is_stale(snapshot):
            self._notify_plugin_task(handle, "cancelled", "数据已更新，结果已作废。")
            return
        source_spec = window.left_workspace.page_by_id(snapshot.page_id)
        if source_spec is None:
            # 来源页已关闭：结果没有归属，直接丢弃。
            self._notify_plugin_task(handle, "cancelled", "来源页已关闭，结果已丢弃。")
            return
        title = validated.title or f"{snapshot.page_title} 分析曲线"
        params = {
            "curve_kind": "plugin_curve",
            ANALYSIS_PARAMS_KEY: {
                "plugin_id": snapshot.plugin_id,
                "plugin_version": self._plugin_version(snapshot.plugin_id),
                "task_id": str(getattr(handle, "task_id", "")),
                "snapshot_id": snapshot.snapshot_id,
                "data_generation": snapshot.data_generation,
                "source_page_id": snapshot.page_id,
                "source_title": source_spec.title,
                "source_description": snapshot.describe(),
                "scope_id": snapshot.scope_id,
                "params": dict(validated.params),
            },
            # 展示与导出都走宿主既有的 1D 路径，直接存标准曲线快照。
            "base_curve": {
                "curve_kind": "plugin_curve",
                "title": title,
                "source_title": source_spec.title,
                "label": title,
                "xlabel": validated.x_axis_label(),
                "x_data": np.asarray(validated.x, dtype=float).tolist(),
                "y_data": np.asarray(validated.y, dtype=float).tolist(),
            },
        }
        try:
            window.add_plugin_result_page(
                title=title,
                source_page_id=snapshot.page_id,
                source_title=source_spec.title,
                data_scope_id=snapshot.scope_id,
                params=params,
            )
        except Exception as exc:
            self._failed[snapshot.plugin_id] = f"结果页创建失败：{type(exc).__name__}: {exc}"
            self._notify_plugin_task(handle, "failed", "结果页创建失败。")
            return
        self._notify_plugin_task(handle, "succeeded")

    def _plugin_version(self, plugin_id: str) -> str:
        record = self.manager.record(plugin_id)
        if record is None:
            return ""
        return str(record.running_version or record.version or "")

    def _result_is_stale(self, snapshot: AnalysisInput2D) -> bool:
        """完成回调再次核对数据代次：加载新数据后过期结果一律丢弃。

        退出应用时不必在这里判断：``shutdown()`` 会先清掉所有快照，晚到的结果
        找不到归属，自然被丢弃。
        """
        current = int(getattr(self.window, "shared_denoise_version", 0) or 0)
        return current != int(snapshot.data_generation)

    # ------------------------------------------------------------------
    # 能力 2b：分析面板（只在二维结果页显示）
    # ------------------------------------------------------------------
    def mount_analysis_cards(self, page_data) -> None:
        """把分析插件的面板挂到「处理分析」页；失败只记录，不影响界面。"""
        for record in self.manager.ready_plugins():
            if record.plugin_id in self._analysis_cards:
                continue
            if not self.is_analysis_plugin(record):
                continue
            try:
                panel = record.instance.create_panel(self.host_for(record.plugin_id))
            except Exception as exc:
                message = f"分析面板创建失败：{type(exc).__name__}: {exc}"
                self._failed[record.plugin_id] = message
                self.manager.report_init_failure(record, message)
                continue
            if panel is None:
                self.manager.mark_healthy(record.plugin_id)
                continue
            title = str(getattr(record.manifest, "name", "") or record.plugin_id)
            try:
                card = page_data.mount_analysis_card(record.plugin_id, title, panel)
            except Exception as exc:
                message = f"分析面板挂载失败：{type(exc).__name__}: {exc}"
                self._failed[record.plugin_id] = message
                self.manager.report_init_failure(record, message)
                continue
            self._analysis_cards[record.plugin_id] = card
            self.manager.mark_healthy(record.plugin_id)

    def set_analysis_cards_visible(self, visible: bool, *, animate: bool = True) -> None:
        page_data = self._analysis_page()
        if page_data is None:
            return
        for plugin_id in self._analysis_cards:
            try:
                page_data.set_analysis_card_visible(plugin_id, visible, animate=animate)
            except Exception:
                pass

    def analysis_card_ids(self) -> List[str]:
        return list(self._analysis_cards)

    def _analysis_page(self):
        return getattr(self.window, "page_data", None)

    # ------------------------------------------------------------------
    # 刷新
    # ------------------------------------------------------------------
    def request_refresh(self, *, immediate: bool = False) -> None:
        """复用宿主既有的预览/正式刷新节流，不新建平行的刷新系统。"""
        window = self.window
        try:
            from bandscope.rendering.refresh_pipeline import RefreshCause, RenderQuality

            if immediate:
                window.request_refresh(
                    RefreshCause.TRANSFER_FUNCTION, RenderQuality.EXACT, immediate=True
                )
            else:
                window.request_refresh(RefreshCause.TRANSFER_FUNCTION, interactive=True)
        except Exception as exc:
            print(f"Plugin refresh request failed: {exc}")

    def notify(self, message: str, *, level: str = "info") -> None:
        window = self.window
        toast = getattr(window, "toast_manager", None)
        if toast is not None and hasattr(toast, "show"):
            try:
                toast.show(str(message))
                return
            except Exception:
                pass

    # -- 诊断 -----------------------------------------------------------
    def errors(self) -> Dict[str, str]:
        return dict(self._failed)

    def incompatible_records(self):
        return self.manager.incompatible()
