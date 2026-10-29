# -*- coding: utf-8 -*-
"""多数据源工作区：新增不覆盖、切源恢复、源级关闭、别名与缓存预算。"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PyQt5.QtWidgets import QApplication

from bandscope.core.source_session import ENTRY_VIEW_RECIPE
from bandscope.exporting.publication_export import capture_snapshot
from bandscope.core.view_recipe import ViewRecipeDocument
from bandscope.processing.compute_backends import recommended_host_cache_budget
from bandscope.ui.result_workspace import AnalysisPageSpec
from tests.support.view_recipe import control_window, synthetic_npz


class _WorkspaceFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.first_path = root / "first.npz"
        self.second_path = root / "second.npz"
        synthetic_npz(self.first_path, seed=1)
        synthetic_npz(self.second_path, seed=2)
        self.window = control_window()
        # 清理顺序（LIFO）：关窗 → 等后台摘要线程读完临时文件 → 删目录。
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._settle_background_readers)
        self.addCleanup(self.window.close)

    def _settle_background_readers(self):
        """加载会在后台线程读取 NPZ 计算摘要；临删除目录前等它结束。

        Windows 上文件仍被打开时 ``unlink`` 会报 WinError 32，这里只负责测试的
        临时数据生命周期，不隐藏任何产品断言。
        """
        deadline = time.monotonic() + 2.0
        pending = []
        for session in list(self.window.source_manager):
            core = (session.state or {}).get("core")
            identity = getattr(core, "source_identity", None)
            if identity is not None:
                pending.append(identity.future)
        identity = getattr(self.window.core, "source_identity", None)
        if identity is not None:
            pending.append(identity.future)
        for future in pending:
            try:
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
        time.sleep(0.05)

    def load_first(self):
        self.assertTrue(self.window.load_data(str(self.first_path)))
        return self.window.source_manager.active_id

    # ------------------------------------------------------------------
    def test_a_second_load_adds_a_workspace_instead_of_replacing(self):
        first_id = self.load_first()
        first_home = self.window.left_workspace.home_page_id
        first_data = self.window.original_raw_data
        self.window.left_workspace.add_page(
            AnalysisPageSpec(
                page_id=self.window._make_page_id(),
                title="第一份的分析",
                page_kind="time_integral",
                source_module="test",
                source_page_id=first_home,
                params={"t_low": 0, "t_up": 1},
            )
        )
        first_pages = set(self.window.left_workspace.page_specs)
        first_current = self.window.left_workspace.current_page_id

        self.assertTrue(self.window.load_data(str(self.second_path)))

        manager = self.window.source_manager
        self.assertEqual(len(manager), 2)
        self.assertEqual(manager.session_ids()[0], first_id)
        self.assertNotEqual(manager.active_id, first_id)
        self.assertEqual(
            [alias for _sid, alias in manager.source_items()],
            ["first", "second"],
        )
        self.assertIsNot(self.window.original_raw_data, first_data)
        # 第一份工作区原样保留，只是暂时不显示。
        first_session = manager.get(first_id)
        self.assertEqual(set(first_session.page_ids), first_pages)
        self.assertEqual(
            set(page.page_id for page in first_session.state["workspace"]["pages"]),
            first_pages,
        )

        switched = self.window.activate_source(first_id)
        self.assertTrue(switched)
        self.assertEqual(set(self.window.left_workspace.page_specs), first_pages)
        self.assertEqual(self.window.left_workspace.current_page_id, first_current)

    def test_reloading_the_same_file_creates_another_independent_workspace(self):
        self.load_first()
        first_home = self.window.left_workspace.home_page_id
        first_data = self.window.original_raw_data
        first_scopes = self.window.data_scopes

        self.assertTrue(self.window.load_data(str(self.first_path)))

        manager = self.window.source_manager
        self.assertEqual(len(manager), 2)
        self.assertNotEqual(self.window.left_workspace.home_page_id, first_home)
        self.assertIsNot(self.window.original_raw_data, first_data)
        self.assertFalse(np.shares_memory(self.window.original_raw_data, first_data))
        self.assertIsNot(self.window.data_scopes, first_scopes)
        self.assertEqual(
            [alias for _sid, alias in manager.source_items()],
            ["first", "first_2"],
        )

    def test_switching_restores_pages_controls_camera_and_plugin_defaults(self):
        first_id = self.load_first()
        self.window.left_workspace.add_page(
            AnalysisPageSpec(
                page_id=self.window._make_page_id(),
                title="第一份的结果",
                page_kind="time_integral",
                source_module="test",
                source_page_id=self.window.left_workspace.home_page_id,
                params={"t_low": 0, "t_up": 2},
            )
        )
        result_page = self.window.left_workspace.current_page_id
        self.window.timeline_bar.slider_time.setValue(1)
        first_camera = self.window._current_3d_camera_position()

        self.window.load_data(str(self.second_path))
        second_id = self.window.source_manager.active_id
        self.window.timeline_bar.slider_time.setValue(2)

        before_switch = self.window._source_activation_count
        self.assertTrue(self.window.activate_source(first_id))
        # 一次切源只绑定 / 刷新一次；选中当前源不重复激活。
        self.assertEqual(self.window._source_activation_count, before_switch + 1)
        self.assertFalse(self.window.activate_source(first_id))
        self.assertEqual(self.window._source_activation_count, before_switch + 1)
        self.assertEqual(self.window.left_workspace.current_page_id, result_page)
        self.assertEqual(self.window.timeline_bar.slider_time.value(), 1)
        np.testing.assert_allclose(
            np.asarray(self.window._current_3d_camera_position(), dtype=float),
            np.asarray(first_camera, dtype=float),
        )

        self.assertTrue(self.window.activate_source(second_id))
        self.assertEqual(self.window.timeline_bar.slider_time.value(), 2)

    def test_switching_clears_pending_denoise_but_keeps_the_configuration(self):
        from bandscope.processing.denoise_engines import DenoiseEngines

        methods = [DenoiseEngines.SG_METHOD_NAME]
        first_id = self.load_first()
        self.window.global_denoise_methods = list(methods)
        self.window.load_data(str(self.second_path))
        second_id = self.window.source_manager.active_id
        self.window._pending_denoise_signature = "pending-signature"
        self.window._pending_denoise_methods = list(methods)

        # 切走使未完成去噪失效，但配置本身保留在各自的数据源里。
        self.window.activate_source(first_id)
        self.assertEqual(self.window.global_denoise_methods, methods)
        self.window.activate_source(second_id)
        self.assertIsNone(self.window._pending_denoise_signature)
        self.assertIsNone(self.window._pending_denoise_methods)

    def test_switching_between_different_shapes_restores_the_time_range(self):
        shapes = {
            "small": (5, 6, 7, 3),
            "larger": (6, 7, 8, 6),
        }
        paths = {}
        for name, shape in shapes.items():
            path = Path(self.temp.name) / f"{name}.npz"
            data = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
            np.savez(
                path,
                sample=data,
                kx=np.linspace(-1, 1, shape[0]),
                ky=np.linspace(-2, 2, shape[1]),
                E=np.linspace(-1, 1, shape[2]),
                time=np.linspace(0, 30, shape[3]),
                axis_mapping_version=1,
                axis_order=np.array(["X", "Y", "E", "T"]),
            )
            paths[name] = path

        self.assertTrue(self.window.load_data(str(paths["small"])))
        small_id = self.window.source_manager.active_id
        self.assertTrue(self.window.load_data(str(paths["larger"])))
        larger_id = self.window.source_manager.active_id
        self.assertEqual(self.window.timeline_bar.slider_time.maximum(), 5)
        self.window.timeline_bar.slider_time.setValue(4)

        self.assertTrue(self.window.activate_source(small_id))
        # 时间轴长度不同的数据源：滑条范围按本源重建，帧号仍是本源上次的值。
        self.assertEqual(self.window.timeline_bar.slider_time.maximum(), 2)
        self.assertEqual(self.window.timeline_bar.slider_time.value(), 0)

        self.assertTrue(self.window.activate_source(larger_id))
        self.assertEqual(self.window.timeline_bar.slider_time.maximum(), 5)
        self.assertEqual(self.window.timeline_bar.slider_time.value(), 4)

    def test_closing_a_source_selects_the_previous_one_and_keeps_the_rest(self):
        first_id = self.load_first()
        self.window.load_data(str(self.second_path))
        second_id = self.window.source_manager.active_id
        self.window.load_data(str(self.second_path))
        third_id = self.window.source_manager.active_id
        self.assertEqual(len(self.window.source_manager), 3)

        self.assertTrue(self.window.close_source(third_id))
        self.assertEqual(self.window.source_manager.active_id, second_id)
        self.assertEqual(self.window.source_manager.session_ids(), [first_id, second_id])

        self.assertTrue(self.window.close_source(second_id))
        self.assertEqual(self.window.source_manager.active_id, first_id)

    def test_closing_the_last_source_returns_to_the_unloaded_window(self):
        self.load_first()
        session_id = self.window.source_manager.active_id
        pages = set(self.window.left_workspace.page_specs)

        self.assertTrue(self.window.close_source(session_id))

        self.assertEqual(len(self.window.source_manager), 0)
        self.assertIsNone(self.window.source_manager.active_id)
        self.assertIsNone(self.window.core.raw_data)
        self.assertIsNone(self.window.original_raw_data)
        self.assertEqual(self.window.data_scopes, {})
        self.assertTrue(self.window.source_switcher_bar.isHidden())
        # 未加载界面保留一个不可删除的占位根页面。
        self.assertEqual(len(self.window.left_workspace.page_specs), 1)
        placeholder = self.window.left_workspace.home_spec()
        self.assertFalse(placeholder.closeable)
        for page_id in pages:
            self.assertIsNone(self.window.source_manager.owner_of(page_id))

    def test_deleting_the_root_page_closes_the_whole_source(self):
        self.load_first()
        session_id = self.window.source_manager.active_id
        home = self.window.left_workspace.home_page_id
        self.window.left_workspace.add_page(
            AnalysisPageSpec(
                page_id=self.window._make_page_id(),
                title="结果",
                page_kind="time_integral",
                source_module="test",
                source_page_id=home,
                params={"t_low": 0, "t_up": 1},
            )
        )
        child = self.window.left_workspace.current_page_id

        self.assertTrue(self.window.left_workspace.delete_page(home))

        self.assertIsNone(self.window.source_manager.get(session_id))
        self.assertIsNone(self.window.source_manager.owner_of(child))
        self.assertIsNone(self.window.core.raw_data)
        self.assertEqual(len(self.window.left_workspace.page_specs), 1)

    def test_alias_edit_is_applied_to_the_switcher_and_dedupes(self):
        first_id = self.load_first()
        self.window.load_data(str(self.second_path))
        second_id = self.window.source_manager.active_id

        self.window._commit_source_alias(second_id, "第一份")
        self.assertEqual(self.window.source_manager.alias_for(second_id), "第一份")
        self.assertEqual(
            self.window.source_switcher_bar.items(),
            [(first_id, "first"), (second_id, "第一份")],
        )

        self.window._commit_source_alias(second_id, "")
        self.assertEqual(self.window.source_manager.alias_for(second_id), "second")

        self.window._commit_source_alias(second_id, "first")
        self.assertEqual(self.window.source_manager.alias_for(second_id), "first_2")

    def test_failed_load_keeps_every_existing_source(self):
        first_id = self.load_first()
        broken = Path(self.temp.name) / "broken.npz"
        broken.write_text("not an npz", encoding="utf-8")
        core = self.window.core
        pages = set(self.window.left_workspace.page_specs)

        with patch.object(self.window, "_show_message") as message:
            self.assertFalse(self.window.load_data(str(broken)))
        message.assert_called_once()

        self.assertEqual(self.window.source_manager.session_ids(), [first_id])
        self.assertIs(self.window.core, core)
        self.assertEqual(set(self.window.left_workspace.page_specs), pages)

    def test_scope_cache_budget_is_shared_between_sources(self):
        self.load_first()
        self.window.load_data(str(self.second_path))
        caches = []
        for session in self.window.source_manager:
            cache = session.state.get("_scope_data_cache")
            if cache is not None:
                caches.append(cache)
        active_cache = self.window._scope_data_cache
        if all(cache is not active_cache for cache in caches):
            caches.append(active_cache)
        total = sum(cache.max_bytes for cache in caches)
        self.assertLessEqual(total, recommended_host_cache_budget())
        self.assertTrue(all(cache.max_bytes <= recommended_host_cache_budget() for cache in caches))

    def test_view_recipe_import_adds_a_source_with_fresh_page_ids(self):
        first_id = self.load_first()
        document = capture_snapshot(self.window).view_recipe
        self.assertIsNotNone(document)
        recipe_path = Path(self.temp.name) / "figure.png.bsview.json"
        recipe_path.write_text(ViewRecipeDocument(document).dumps(), encoding="utf-8")
        old_home = self.window.left_workspace.home_page_id

        self.assertTrue(self.window._load_view_recipe(
            recipe_path, confirm=False, relocate=lambda _: str(self.first_path)
        ))

        manager = self.window.source_manager
        self.assertEqual(len(manager), 2)
        active = manager.active()
        self.assertEqual(active.entry_kind, ENTRY_VIEW_RECIPE)
        self.assertEqual(Path(active.primary_path).resolve(), self.first_path.resolve())
        new_home = self.window.left_workspace.home_page_id
        self.assertNotEqual(new_home, old_home)
        # 旧会话仍持有它自己的页面，两个工作区的页面 ID 不重叠。
        self.assertEqual(manager.owner_of(old_home), first_id)
        self.assertEqual(manager.owner_of(new_home), active.session_id)

        # 同一个复现文件再打开一次：仍是新增数据源，页面 ID 再次重新分配。
        first_import_pages = set(self.window.left_workspace.page_specs)
        self.assertTrue(self.window._load_view_recipe(
            recipe_path, confirm=False, relocate=lambda _: str(self.first_path)
        ))
        self.assertEqual(len(manager), 3)
        self.assertEqual(manager.alias_for(manager.active_id), "figure.png_2")
        self.assertFalse(first_import_pages & set(self.window.left_workspace.page_specs))
        for page_id in first_import_pages:
            self.assertIsNotNone(manager.owner_of(page_id))


if __name__ == "__main__":
    unittest.main()
