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

from typing import Dict, List, Optional

import numpy as np

from bandscope.extensions.api import (
    SOURCE_FILE,
    EnergyAxisSpec,
    PluginContext,
    PluginHost,
    align_multiplier_to_voxels,
)
from bandscope.extensions.plugin_manager import PluginManager

#: 每页插件参数在 ``spec.params`` 里的键。
PAGE_STATE_KEY = "plugins"

#: 插件效果在宿主刷新管线里的失效原因标签。
EFFECT_CAUSE_LABEL = "plugin"


class _HostBridge(PluginHost):
    """交给插件的宿主句柄；只暴露协议允许的三个动作。"""

    def __init__(self, session: "PluginSession"):
        self._session = session

    def context(self) -> PluginContext:
        return self._session.current_context()

    def request_refresh(self, *, immediate: bool = False) -> None:
        self._session.request_refresh(immediate=immediate)

    def notify(self, message: str, *, level: str = "info") -> None:
        self._session.notify(message, level=level)


class PluginSession:
    """一个主窗口对应一个会话。"""

    def __init__(self, window, *, manager: Optional[PluginManager] = None):
        self.window = window
        self.manager = manager or PluginManager()
        self.host = _HostBridge(self)
        self._context = PluginContext()
        self._cards: Dict[str, object] = {}
        self._failed: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def startup(self) -> None:
        """Qt 与宿主接口就绪后调用：先执行待卸载，再加载扩展。"""
        self.manager.startup()

    def shutdown(self) -> None:
        for record in self.manager.plugins():
            instance = getattr(record, "instance", None)
            if instance is None:
                continue
            try:
                instance.release()
            except Exception:
                pass
        self._cards.clear()
        self.manager.records = {}

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
        """把每个可用插件的面板挂到「渲染控制」页；失败只提示，不影响界面。"""
        for record in self.manager.ready_plugins():
            if record.plugin_id in self._cards:
                continue
            try:
                panel = record.instance.create_panel(self.host)
            except Exception as exc:
                self._failed[record.plugin_id] = f"面板创建失败：{type(exc).__name__}: {exc}"
                continue
            if panel is None:
                continue
            title = str(getattr(record.manifest, "name", "") or record.plugin_id)
            try:
                card = page_render.mount_extension_card(record.plugin_id, title, panel)
            except Exception as exc:
                self._failed[record.plugin_id] = f"面板挂载失败：{type(exc).__name__}: {exc}"
                continue
            self._cards[record.plugin_id] = card

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
        """加载新的数据文件：清空与旧数据绑定的峰位。"""
        for record in self.manager.plugins():
            if record.instance is None:
                continue
            try:
                record.instance.reset_for_new_data()
            except Exception:
                pass

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
