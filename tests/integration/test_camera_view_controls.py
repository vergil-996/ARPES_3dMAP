import math
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyvista as pv
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bandscope.ui.camera_view_controls import (
    DEFAULT_AZIMUTH,
    DEFAULT_ELEVATION,
    DEFAULT_ROLL,
    MAX_DISTANCE,
    MIN_DISTANCE,
    CameraPose,
    CameraViewPanel,
    clamp_elevation,
    pose_from_vectors,
    vectors_from_pose,
    wrap_signed_degrees,
)
from bandscope.app.qt_bootstrap import configure_qt_plugin_path
from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.rendering.render_core import VolumeRenderSession


configure_qt_plugin_path()

FOCAL = (100.0, 100.0, 100.0)


def _plotter_with_volume():
    """离屏 3D 场景：体数据放进 0~200 的立方体，并复刻应用的初始观察方向。

    应用用的是 ``pyvistaqt.QtInteractor``，它在构造时对每个 renderer 调用
    ``view_isometric()``；这里显式重现同一步，让测试场景的默认姿态与真实应用一致。
    """
    plotter = pv.Plotter(off_screen=True, window_size=(240, 180))
    session = VolumeRenderSession(plotter)
    session.render(
        np.ones((8, 9, 10), dtype=np.float32),
        (0, 50, 100),
        "linear",
        show_axes=False,
    )
    plotter.renderer.view_isometric(render=False)
    return plotter


class _RenderPageStub:
    def __init__(self):
        self.poses = []
        self.visibility = []

    def set_camera_view_visible(self, visible, *, animate=True):
        self.visibility.append(bool(visible))

    def sync_camera_pose(self, pose, *, force=False):
        self.poses.append(pose)

    def camera_pose(self):
        return self.poses[-1] if self.poses else None


class PoseConversionTests(unittest.TestCase):
    @staticmethod
    def _screen_up(position, focal_point, view_up):
        """相机真正参与渲染的“上”：view_up 在像平面内的分量（去掉视线方向上的分量）。

        VTK 允许 ``view_up`` 与视线不严格垂直，这时沿视线的分量不影响画面，
        也就不该被往返换算还原出来。
        """
        view_dir = np.asarray(focal_point, dtype=float) - np.asarray(position, dtype=float)
        view_dir = view_dir / np.linalg.norm(view_dir)
        up = np.asarray(view_up, dtype=float)
        up = up - np.dot(up, view_dir) * view_dir
        return up / np.linalg.norm(up)

    def assert_round_trip(self, position, focal_point, view_up):
        pose = pose_from_vectors(position, focal_point, view_up)
        self.assertIsNotNone(pose)
        rebuilt_position, rebuilt_up = vectors_from_pose(
            pose.azimuth, pose.elevation, pose.roll, pose.distance, focal_point
        )
        np.testing.assert_allclose(rebuilt_position, position, atol=1e-9)
        np.testing.assert_allclose(
            rebuilt_up,
            self._screen_up(position, focal_point, view_up),
            atol=1e-9,
        )

    def test_default_constants_match_the_pyvista_isometric_preset(self):
        """默认姿态常量必须等于应用初次展示 3D 数据时的方向。

        pyvistaqt 构造 ``QtInteractor`` 时调用 ``Renderer.view_isometric()``，
        方向来自主题默认的 ``camera['position'] = (1, 1, 1)`` 与 ``viewup = (0, 0, 1)``。
        这里用 ``get_default_cam_pos()`` 反查，pyvista 主题若改动会立刻失败。
        """
        plotter = _plotter_with_volume()
        try:
            position, focal_point, view_up = plotter.renderer.get_default_cam_pos()
            pose = pose_from_vectors(position, focal_point, view_up)
            self.assertAlmostEqual(pose.azimuth, DEFAULT_AZIMUTH, places=9)
            self.assertAlmostEqual(pose.elevation, DEFAULT_ELEVATION, places=9)
            self.assertAlmostEqual(pose.roll, DEFAULT_ROLL, places=9)
            self.assertAlmostEqual(DEFAULT_AZIMUTH, 45.0, places=9)
            self.assertAlmostEqual(DEFAULT_ELEVATION, 35.264389682754654, places=9)
        finally:
            plotter.close()

    def test_default_view_matches_the_documented_defaults(self):
        plotter = _plotter_with_volume()
        try:
            pose = pose_from_vectors(
                plotter.camera.position,
                plotter.camera.focal_point,
                plotter.camera.up,
            )
            self.assertAlmostEqual(pose.azimuth, DEFAULT_AZIMUTH, places=9)
            self.assertAlmostEqual(pose.elevation, DEFAULT_ELEVATION, places=9)
            self.assertAlmostEqual(pose.roll, DEFAULT_ROLL, places=9)
            self.assertGreater(pose.distance, 0.0)
        finally:
            plotter.close()

    def test_representative_poses_round_trip(self):
        panned_focal = (150.0, 90.0, 120.0)
        cases = [
            ((100.0, 100.0, 769.2), FOCAL, (0.0, 1.0, 0.0)),
            ((100.0, 100.0, -569.2), FOCAL, (0.0, -1.0, 0.0)),
            ((769.2, 100.0, 100.0), FOCAL, (0.0, 1.0, 0.0)),
            ((500.0, -200.0, 300.0), panned_focal, (0.3, 0.4, 0.86)),
            ((100.0, 600.0, 100.0), FOCAL, (0.0, 0.0, 1.0)),
            ((100.0, -400.0, 100.0), FOCAL, (0.0, 0.0, -1.0)),
        ]
        for position, focal, up in cases:
            with self.subTest(position=position):
                self.assert_round_trip(position, focal, up)

    def test_extreme_poses_round_trip(self):
        # 覆盖 ±180°/±360° 跨界与接近极点的姿态：数学上等价即通过。
        for azimuth in (-359.0, -180.0, 180.0, 359.0):
            for elevation in (-89.999, -45.0, 0.0, 45.0, 89.999):
                for roll in (-179.0, 0.0, 179.0):
                    with self.subTest(azimuth=azimuth, elevation=elevation, roll=roll):
                        position, up = vectors_from_pose(
                            azimuth, elevation, roll, 320.0, FOCAL
                        )
                        self.assert_round_trip(position, FOCAL, up)

    def test_pose_reads_back_the_normalized_angles(self):
        position, up = vectors_from_pose(270.0, 120.0, -270.0, 250.0, FOCAL)
        pose = pose_from_vectors(position, FOCAL, up)
        self.assertAlmostEqual(pose.azimuth, -90.0, places=8)
        self.assertAlmostEqual(pose.elevation, 90.0, places=8)
        self.assertAlmostEqual(pose.roll, 90.0, places=8)

    def test_degenerate_input_returns_none(self):
        self.assertIsNone(pose_from_vectors(FOCAL, FOCAL, (0, 1, 0)))
        self.assertIsNone(
            pose_from_vectors((float("nan"), 0.0, 0.0), FOCAL, (0, 1, 0))
        )

    def test_non_positive_distance_is_rejected(self):
        for distance in (0.0, -5.0, float("inf")):
            with self.subTest(distance=distance):
                with self.assertRaises(ValueError):
                    vectors_from_pose(0.0, 0.0, 0.0, distance, FOCAL)

    def test_normalization_helpers(self):
        self.assertAlmostEqual(wrap_signed_degrees(270.0), -90.0)
        self.assertAlmostEqual(wrap_signed_degrees(-270.0), 90.0)
        self.assertAlmostEqual(wrap_signed_degrees(180.0), 180.0)
        self.assertAlmostEqual(wrap_signed_degrees(-180.0), 180.0)
        self.assertAlmostEqual(wrap_signed_degrees(360.0), 0.0)
        self.assertEqual(math.copysign(1.0, wrap_signed_degrees(-0.0)), 1.0)
        self.assertAlmostEqual(clamp_elevation(120.0), 90.0)
        self.assertAlmostEqual(clamp_elevation(-120.0), -90.0)
        self.assertAlmostEqual(clamp_elevation(30.0), 30.0)


class PoseSemanticsTests(unittest.TestCase):
    def test_angle_edit_keeps_focal_point_and_distance(self):
        focal = (150.0, 90.0, 120.0)
        position, up = vectors_from_pose(12.0, 7.0, 0.0, 480.0, focal)
        pose = pose_from_vectors(position, focal, up)

        new_position, new_up = vectors_from_pose(
            pose.azimuth + 40.0, pose.elevation - 15.0, pose.roll, pose.distance, focal
        )
        rebuilt = pose_from_vectors(new_position, focal, new_up)

        self.assertAlmostEqual(rebuilt.distance, 480.0, places=8)
        self.assertAlmostEqual(rebuilt.azimuth, 52.0, places=8)
        self.assertAlmostEqual(rebuilt.elevation, -8.0, places=8)

    def test_distance_edit_keeps_direction(self):
        position, up = vectors_from_pose(35.0, 22.0, 11.0, 480.0, FOCAL)

        new_position, new_up = vectors_from_pose(35.0, 22.0, 11.0, 900.0, FOCAL)

        direction = np.asarray(position) - np.asarray(FOCAL)
        direction = direction / np.linalg.norm(direction)
        new_direction = np.asarray(new_position) - np.asarray(FOCAL)
        new_direction = new_direction / np.linalg.norm(new_direction)
        np.testing.assert_allclose(new_direction, direction, atol=1e-12)
        np.testing.assert_allclose(new_up, up, atol=1e-12)

    def test_roll_edit_keeps_position_and_distance(self):
        position, _up = vectors_from_pose(35.0, 22.0, 0.0, 480.0, FOCAL)

        rolled_position, rolled_up = vectors_from_pose(35.0, 22.0, 65.0, 480.0, FOCAL)

        np.testing.assert_allclose(rolled_position, position, atol=1e-12)
        rebuilt = pose_from_vectors(rolled_position, FOCAL, rolled_up)
        self.assertAlmostEqual(rebuilt.roll, 65.0, places=8)
        self.assertAlmostEqual(rebuilt.distance, 480.0, places=8)


class CameraViewPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = CameraViewPanel()
        self.panel.show()
        self.panel.sync_pose(CameraPose(0.0, 0.0, 0.0, 500.0), force=True)

    def tearDown(self):
        self.panel.hide()
        self.panel.deleteLater()

    def test_fields_and_single_reset_button(self):
        labels = [label.text() for label in self.panel._labels]
        self.assertEqual(labels, ["方位角", "仰角", "滚转角", "距离"])
        self.assertEqual(self.panel.reset_button.text(), "重置视角")
        self.assertEqual(len(self.panel.findChildren(type(self.panel.reset_button))), 1)

    def test_typed_azimuth_wraps_into_signed_range(self):
        box = self.panel._boxes["azimuth"]
        box.lineEdit().setFocus()
        box.lineEdit().selectAll()
        QTest.keyClicks(box.lineEdit(), "270")
        QTest.keyClick(box.lineEdit(), Qt.Key_Return)
        self.assertAlmostEqual(box.value(), -90.0)

    def test_typed_elevation_is_clamped(self):
        box = self.panel._boxes["elevation"]
        box.lineEdit().setFocus()
        box.lineEdit().selectAll()
        QTest.keyClicks(box.lineEdit(), "150")
        QTest.keyClick(box.lineEdit(), Qt.Key_Return)
        self.assertAlmostEqual(box.value(), 90.0)

    def test_distance_below_zero_falls_back_to_minimum(self):
        box = self.panel._boxes["distance"]
        box.lineEdit().setFocus()
        box.lineEdit().selectAll()
        QTest.keyClicks(box.lineEdit(), "0")
        QTest.keyClick(box.lineEdit(), Qt.Key_Return)
        self.assertAlmostEqual(self.panel.current_pose().distance, MIN_DISTANCE)

    def test_arrow_step_commits_immediately(self):
        seen = []
        self.panel.pose_committed.connect(lambda pose, field: seen.append((pose, field)))
        QTest.keyClick(self.panel._boxes["azimuth"].lineEdit(), Qt.Key_Up)
        self.assertEqual(len(seen), 1)
        pose, field = seen[0]
        self.assertEqual(field, "azimuth")
        self.assertAlmostEqual(pose.azimuth, 1.0)

    def test_commit_reports_which_field_the_user_edited(self):
        seen = []
        self.panel.pose_committed.connect(lambda pose, field: seen.append(field))
        self.panel._boxes["distance"].setValue(777.0)
        self.assertEqual(seen, ["distance"])

    def test_uncommitted_text_survives_background_sync(self):
        azimuth = self.panel._boxes["azimuth"]
        azimuth.lineEdit().setFocus()
        azimuth.lineEdit().selectAll()
        QTest.keyClicks(azimuth.lineEdit(), "77")

        self.panel.sync_pose(CameraPose(30.0, 12.0, 0.0, 640.0))

        self.assertEqual(azimuth.lineEdit().text(), "77°")
        self.assertAlmostEqual(self.panel._boxes["elevation"].value(), 12.0)
        self.assertAlmostEqual(self.panel._boxes["distance"].value(), 640.0)

    def test_escape_cancels_the_edit_and_resumes_syncing(self):
        azimuth = self.panel._boxes["azimuth"]
        azimuth.lineEdit().setFocus()
        azimuth.lineEdit().selectAll()
        QTest.keyClicks(azimuth.lineEdit(), "77")
        QTest.keyClick(azimuth.lineEdit(), Qt.Key_Escape)

        self.panel.sync_pose(CameraPose(10.0, 0.0, 0.0, 500.0))
        self.assertAlmostEqual(azimuth.value(), 10.0)
        self.assertEqual(azimuth.lineEdit().text(), "10.0°")

    def test_committed_edit_resumes_syncing(self):
        azimuth = self.panel._boxes["azimuth"]
        azimuth.lineEdit().setFocus()
        azimuth.lineEdit().selectAll()
        QTest.keyClicks(azimuth.lineEdit(), "77")
        QTest.keyClick(azimuth.lineEdit(), Qt.Key_Return)
        self.assertAlmostEqual(azimuth.value(), 77.0)

        self.panel.sync_pose(CameraPose(10.0, 0.0, 0.0, 500.0))
        self.assertAlmostEqual(azimuth.value(), 10.0)

    def test_current_pose_clamps_distance_into_range(self):
        self.panel._boxes["distance"].setValue(MAX_DISTANCE * 10)
        self.assertAlmostEqual(self.panel.current_pose().distance, MAX_DISTANCE)


class CameraResetTests(unittest.TestCase):
    """重置与 E 轴翻转的配合：用真实离屏相机 + 桩化的窗口属性。"""

    def _make_analyzer(self, *, flip):
        plotter = _plotter_with_volume()
        analyzer = My3DAnalyzer.__new__(My3DAnalyzer)
        analyzer.plotter = plotter
        analyzer.volume_session = VolumeRenderSession(plotter)
        analyzer.left_display_stack = SimpleNamespace(currentWidget=lambda: plotter)
        analyzer.timeline_bar = SimpleNamespace(
            switch_flip=SimpleNamespace(isChecked=lambda: flip)
        )
        analyzer.current_render_context = {"view": "3d"}
        analyzer.page_render = _RenderPageStub()
        analyzer._saved_3d_camera_position = None
        analyzer._camera_e_flip_applied = False
        analyzer._camera_view_sync_timer = None
        return analyzer

    def _pose(self, analyzer):
        return pose_from_vectors(
            analyzer.plotter.camera.position,
            analyzer.plotter.camera.focal_point,
            analyzer.plotter.camera.up,
        )

    def _assert_same_pose(self, actual, expected):
        self.assertAlmostEqual(actual.azimuth, expected.azimuth, places=9)
        self.assertAlmostEqual(actual.elevation, expected.elevation, places=9)
        self.assertAlmostEqual(actual.roll, expected.roll, places=9)
        self.assertAlmostEqual(actual.distance, expected.distance, places=9)

    def test_reset_restores_default_direction_and_framing(self):
        analyzer = self._make_analyzer(flip=False)
        try:
            # 先做一个明显的用户操作：平移观察中心 + 环绕。
            analyzer.plotter.camera.focal_point = (140.0, 80.0, 130.0)
            analyzer.plotter.camera.position = (600.0, -250.0, 320.0)
            analyzer.plotter.camera.up = (0.2, 0.5, 0.84)
            analyzer.plotter.camera.OrthogonalizeViewUp()

            analyzer.on_camera_view_reset()

            pose = self._pose(analyzer)
            self.assertAlmostEqual(pose.azimuth, DEFAULT_AZIMUTH, places=6)
            self.assertAlmostEqual(pose.elevation, DEFAULT_ELEVATION, places=6)
            self.assertAlmostEqual(pose.roll, DEFAULT_ROLL, places=6)
            # 观察中心回到当前展示内容（体数据的包围盒中心）。
            self.assertAlmostEqual(analyzer.plotter.camera.focal_point[0], 100.0, places=6)
            self.assertAlmostEqual(analyzer.plotter.camera.focal_point[1], 100.0, places=6)
            self.assertAlmostEqual(analyzer.plotter.camera.focal_point[2], 100.0, places=6)
            # 距离重新取景到能完整显示场景。
            self.assertGreater(pose.distance, 200.0)
            self.assertFalse(analyzer._camera_e_flip_applied)
            # 重置结果同步回面板，并写进相机快照供下一次刷新恢复。
            self.assertTrue(analyzer.page_render.poses)
            synced = analyzer.page_render.poses[-1]
            self.assertAlmostEqual(synced.azimuth, pose.azimuth, places=9)
            self.assertAlmostEqual(synced.elevation, pose.elevation, places=9)
            self.assertAlmostEqual(synced.roll, pose.roll, places=9)
            self.assertAlmostEqual(synced.distance, pose.distance, places=9)
            snapshot = analyzer._current_3d_camera_position()
            np.testing.assert_allclose(
                np.asarray(analyzer._saved_3d_camera_position), snapshot, atol=1e-9
            )
        finally:
            analyzer.plotter.close()

    def test_reset_is_idempotent_with_e_flip_enabled(self):
        analyzer = self._make_analyzer(flip=True)
        try:
            analyzer.on_camera_view_reset()
            first = self._pose(analyzer)
            self.assertTrue(analyzer._camera_e_flip_applied)
            # E 轴翻转打开时，重置给出“默认姿态 + 180° 翻转”，而不是把翻转再叠一层。
            # 翻转是绕相机水平轴转 180°，本约定下等价于方位角 -135°、仰角 -35.264°、
            # 滚转 180°（见 tests 里的默认常量推导）。
            self.assertAlmostEqual(first.azimuth, -135.0, places=6)
            self.assertAlmostEqual(first.elevation, -DEFAULT_ELEVATION, places=6)
            self.assertAlmostEqual(first.roll, 180.0, places=6)

            analyzer.on_camera_view_reset()
            second = self._pose(analyzer)
            self._assert_same_pose(second, first)

            # 下一次 3D 刷新会重放 _apply_pending_e_flip_camera：不能再转 180°。
            analyzer._apply_pending_e_flip_camera()
            third = self._pose(analyzer)
            self._assert_same_pose(third, second)
        finally:
            analyzer.plotter.close()

    def test_reset_keeps_flip_switch_consistent_after_toggling(self):
        analyzer = self._make_analyzer(flip=True)
        try:
            analyzer.on_camera_view_reset()
            flipped = self._pose(analyzer)

            # 关闭 E 轴翻转后重放一次：回到未翻转的默认姿态。
            analyzer.timeline_bar.switch_flip = SimpleNamespace(isChecked=lambda: False)
            analyzer._apply_pending_e_flip_camera()
            unflipped = self._pose(analyzer)
            self.assertAlmostEqual(unflipped.azimuth, DEFAULT_AZIMUTH, places=6)
            self.assertAlmostEqual(unflipped.elevation, DEFAULT_ELEVATION, places=6)
            self.assertAlmostEqual(unflipped.roll, DEFAULT_ROLL, places=6)
            self.assertNotAlmostEqual(flipped.roll, unflipped.roll, places=6)
        finally:
            analyzer.plotter.close()

    def test_pose_commit_preserves_panned_center(self):
        analyzer = self._make_analyzer(flip=False)
        try:
            analyzer.plotter.camera.focal_point = (150.0, 90.0, 120.0)
            analyzer.plotter.camera.position = (500.0, -200.0, 300.0)
            analyzer.plotter.camera.up = (0.3, 0.4, 0.86)
            analyzer.plotter.camera.OrthogonalizeViewUp()

            analyzer.on_camera_view_pose_committed(CameraPose(45.0, 20.0, 0.0, 400.0))

            camera = analyzer.plotter.camera
            np.testing.assert_allclose(
                np.asarray(camera.focal_point), (150.0, 90.0, 120.0), atol=1e-9
            )
            pose = self._pose(analyzer)
            self.assertAlmostEqual(pose.azimuth, 45.0, places=6)
            self.assertAlmostEqual(pose.elevation, 20.0, places=6)
            self.assertAlmostEqual(pose.roll, 0.0, places=6)
            self.assertAlmostEqual(pose.distance, 400.0, places=6)
            # 相机快照跟着更新，下一次刷新不会跳回旧姿态。
            self.assertIsNotNone(analyzer._saved_3d_camera_position)
        finally:
            analyzer.plotter.close()

    def test_distance_only_edit_keeps_observation_center(self):
        analyzer = self._make_analyzer(flip=False)
        try:
            analyzer.plotter.camera.focal_point = (150.0, 90.0, 120.0)
            analyzer.plotter.camera.position = (500.0, -200.0, 300.0)
            analyzer.plotter.camera.up = (0.3, 0.4, 0.86)
            analyzer.plotter.camera.OrthogonalizeViewUp()
            before = self._pose(analyzer)

            analyzer.on_camera_view_pose_committed(
                CameraPose(before.azimuth, before.elevation, before.roll, before.distance * 1.5)
            )

            after = self._pose(analyzer)
            np.testing.assert_allclose(
                np.asarray(analyzer.plotter.camera.focal_point),
                (150.0, 90.0, 120.0),
                atol=1e-9,
            )
            self.assertAlmostEqual(after.distance, before.distance * 1.5, places=6)
            self.assertAlmostEqual(after.azimuth, before.azimuth, places=6)
            self.assertAlmostEqual(after.elevation, before.elevation, places=6)
        finally:
            analyzer.plotter.close()

    def test_single_field_edit_keeps_the_other_values_exact(self):
        """只改一项时，其余各项必须沿用相机的精确值而不是输入框的舍入值。"""
        analyzer = self._make_analyzer(flip=False)
        try:
            # 造一个距离带很多小数位的姿态：输入框只显示两位小数。
            analyzer.plotter.camera.focal_point = (150.0, 90.0, 120.0)
            analyzer.plotter.camera.position = (500.123456789, -200.987654321, 300.5)
            analyzer.plotter.camera.up = (0.3, 0.4, 0.86)
            analyzer.plotter.camera.OrthogonalizeViewUp()
            before = self._pose(analyzer)
            rounded_distance = round(before.distance, 2)
            self.assertNotAlmostEqual(before.distance, rounded_distance, places=6)

            analyzer.on_camera_view_pose_committed(
                CameraPose(120.0, round(before.elevation, 1), round(before.roll, 1),
                           rounded_distance),
                "azimuth",
            )

            after = self._pose(analyzer)
            self.assertAlmostEqual(after.azimuth, 120.0, places=9)
            self.assertAlmostEqual(after.distance, before.distance, places=9)
            self.assertAlmostEqual(after.elevation, before.elevation, places=9)
            self.assertAlmostEqual(after.roll, before.roll, places=9)
        finally:
            analyzer.plotter.close()

    def test_named_field_distance_edit_keeps_angles_exact(self):
        analyzer = self._make_analyzer(flip=False)
        try:
            analyzer.plotter.camera.focal_point = (150.0, 90.0, 120.0)
            analyzer.plotter.camera.position = (500.123456789, -200.987654321, 300.5)
            analyzer.plotter.camera.up = (0.3, 0.4, 0.86)
            analyzer.plotter.camera.OrthogonalizeViewUp()
            before = self._pose(analyzer)

            analyzer.on_camera_view_pose_committed(
                CameraPose(round(before.azimuth, 1), round(before.elevation, 1),
                           round(before.roll, 1), 400.0),
                "distance",
            )

            after = self._pose(analyzer)
            self.assertAlmostEqual(after.distance, 400.0, places=9)
            self.assertAlmostEqual(after.azimuth, before.azimuth, places=9)
            self.assertAlmostEqual(after.elevation, before.elevation, places=9)
            np.testing.assert_allclose(
                np.asarray(analyzer.plotter.camera.focal_point),
                (150.0, 90.0, 120.0),
                atol=1e-9,
            )
        finally:
            analyzer.plotter.close()

    def test_pose_commit_is_ignored_outside_a_3d_view(self):
        analyzer = self._make_analyzer(flip=False)
        try:
            analyzer.current_render_context = {"view": "2d"}
            before = analyzer._current_3d_camera_position()

            analyzer.on_camera_view_pose_committed(CameraPose(90.0, 40.0, 0.0, 100.0))
            analyzer.on_camera_view_reset()

            np.testing.assert_allclose(
                np.asarray(analyzer._current_3d_camera_position()),
                np.asarray(before),
                atol=1e-12,
            )
        finally:
            analyzer.plotter.close()

    def test_pose_commit_does_not_rebuild_the_volume(self):
        analyzer = self._make_analyzer(flip=False)
        try:
            session = analyzer.volume_session
            before = (session.rebuild_count, session.data_update_count)

            analyzer.on_camera_view_pose_committed(CameraPose(30.0, 25.0, 5.0, 420.0))

            self.assertEqual((session.rebuild_count, session.data_update_count), before)
        finally:
            analyzer.plotter.close()


if __name__ == "__main__":
    unittest.main()
