"""宿主曲线复制/叠加/log 链路的出图身份和过滤后的稳定配色。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.ui.result_workspace import AnalysisPageSpec
from bandscope.exporting.curve_presentation import source_revision


def curve(curve_id="", x=(0, 1, 2)):
    return dict(curve_kind="slice_dos", curve_id=curve_id, title="same", source_title="same",
                label="same", xlabel="E", x_data=list(x), y_data=[1, 10, 100])


class PublicationCurveIdentityTests(unittest.TestCase):
    def test_normalization_preserves_identity_role_and_palette_metadata(self):
        source = dict(curve("instance"), is_base=False, palette_slot=9)
        normalized = My3DAnalyzer._normalize_curve_snapshot(source)
        self.assertEqual(normalized["curve_id"], "instance")
        self.assertEqual(normalized["palette_slot"], 9)
        self.assertFalse(normalized["is_base"])

    def test_comparison_filters_invalid_overlay_without_renumbering_legacy_identity(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = AnalysisPageSpec("comparison", "same", My3DAnalyzer.COMPARISON_PAGE_KIND, "test",
                                params={"comparison_kind": "slice_dos", "base_curve": curve(),
                                        "overlay_curves": [curve(x=(5, 6, 7)), curve()]})
        context = analyzer._get_curve_comparison_context(spec)
        self.assertEqual([v["curve_id"] for v in context["curves"]], ["legacy:base", "legacy:overlay:1"])
        self.assertEqual([v["palette_slot"] for v in context["curves"]], [0, 2])

    def test_repeated_paste_assigns_independent_instance_identity(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = AnalysisPageSpec("source", "same", "slice_dos", "test")
        analyzer.curve_clipboard = curve("main")
        analyzer._can_paste_curve_to_current_page = Mock(return_value=True)
        analyzer._current_curve_group_snapshot = Mock(return_value=("slice_dos", curve("main"), []))
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec, add_page=Mock())
        analyzer._make_page_id = Mock(side_effect=["new-1", "new-2"])
        analyzer._make_unique_page_title = Mock(side_effect=lambda value: value)
        analyzer._seed_control_state_for_spec = Mock()
        analyzer._paste_curve_to_comparison_page()
        analyzer._paste_curve_to_comparison_page()
        pages = [call.args[0] for call in analyzer.left_workspace.add_page.call_args_list]
        ids = [page.params["overlay_curves"][0]["curve_id"] for page in pages]
        self.assertNotEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], "main")
        self.assertEqual(analyzer.curve_clipboard["curve_id"], "main")

    def test_log_derivation_preserves_overlay_identity_and_has_new_page_ownership(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = AnalysisPageSpec("source", "same", My3DAnalyzer.COMPARISON_PAGE_KIND, "test",
                                params={"comparison_kind": "slice_dos"})
        analyzer.current_render_context = {"view": "1d_comparison", "curves": [curve("main"), curve("overlay")]}
        analyzer.left_workspace = SimpleNamespace(current_spec=lambda: spec, add_page=Mock())
        analyzer._make_page_id = Mock(return_value="log-result")
        analyzer._make_unique_page_title = Mock(side_effect=lambda value: value)
        analyzer._seed_control_state_for_spec = Mock()
        self.assertTrue(analyzer._apply_log_to_current_curves())
        page = analyzer.left_workspace.add_page.call_args.args[0]
        self.assertEqual(page.params["overlay_curves"][0]["curve_id"], "overlay")
        self.assertEqual(page.page_id, "log-result")
        self.assertEqual(page.params["base_curve"]["y_data"], [0, 1, 2])
        self.assertEqual(analyzer.current_render_context["curves"][0]["y_data"], [1, 10, 100])

    def test_revision_detects_denoise_parameters_flip_and_time_without_qt_initialization(self):
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        spec = AnalysisPageSpec("source", "same", "slice_dos", "test")
        original = source_revision(analyzer, spec)
        analyzer.shared_denoise_version = 1
        self.assertNotEqual(source_revision(analyzer, spec), original)
        original = source_revision(analyzer, spec)
        spec.params["crop_regions"] = [{"x": [0, 1]}]
        self.assertNotEqual(source_revision(analyzer, spec), original)
        analyzer.timeline_bar = SimpleNamespace(switch_flip=SimpleNamespace(isChecked=lambda: True),
                                               slider_time=SimpleNamespace(value=lambda: 3))
        self.assertEqual(source_revision(analyzer, spec)[1:3], (True, 3))
