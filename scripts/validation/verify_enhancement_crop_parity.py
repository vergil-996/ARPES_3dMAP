"""Real-window crop/region parity and unchanged background checks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.validation._common import arguments


def main():
    args = arguments("enhancement_crop_parity", default_data=".local/data/WSe2_step.npz")
    import json
    import traceback
    import numpy as np
    from matplotlib.colors import Normalize
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    from bandscope.core.crop_model import CropSelection, apply_selection
    from bandscope.core.local_enhancement import STATE_KEY, physical_bounds, update_region_bounds
    from bandscope.rendering.render_core import VisualEngine
    from bandscope.exporting.publication_export import capture_snapshot, render_and_save
    from bandscope.exporting.publication_models import OutputOptions, resolve_style
    import bandscope.app.refactored_app as app_module

    class NoUpdates:
        def __init__(self, *args, **kwargs):
            pass

        def check_automatically(self):
            pass

        def shutdown(self, **kwargs):
            pass

    app_module.UpdateController = NoUpdates
    app = QApplication([])
    window = app_module.My3DAnalyzer()
    window._save_splitter_sizes = lambda *_args: None
    window.show()
    progress = dict(step=0, wait=0)
    checks = []
    bounds3d = (10, 50, 20, 70, 30, 80)

    def check(name, condition):
        assert condition, name
        checks.append(name)
        print(f"[PASS] {name}", flush=True)

    def screenshot(name):
        app.processEvents()
        window.grab().save(str(args.output_dir / (name + "_window.png")))
        if window.current_render_context["view"] == "3d":
            window.plotter.screenshot(str(args.output_dir / (name + "_scene.png")))

    def pixels():
        window.canvas_2d.draw()
        return np.array(window.canvas_2d.buffer_rgba(), copy=True)

    def tick():
        try:
            if progress["wait"]:
                progress["wait"] -= 1
                return
            if not window._render_exact_ready:
                return
            step = progress["step"]
            if step == 0:
                window.page_render.s_low.setValue(12)
                window.page_render.s_gamma.setValue(68)
                window.page_render.s_up.setValue(87)
                window.page_render.combo_map.setCurrentIndex(3)
                window.flush_render_levels_refresh()
                progress["source"] = window.left_workspace.current_spec()
                progress["science"] = window.core.raw_data[::25, ::25, ::25, :].copy()
                progress["background_info"] = dict(window.volume_session.level_info)
                progress["before_actor"] = window.volume_session.volume
                point = (100, 100, 120)
                rgb = [0., 0., 0.]
                value = float(window.current_render_context["data"][point])
                window.volume_session.volume.GetProperty().GetRGBTransferFunction().GetColor(value, rgb)
                progress.update(bg_point=point, bg_rgb=rgb,
                    bg_alpha=window.volume_session.volume.GetProperty().GetScalarOpacity().GetValue(value))
                window.page_render.btn_lock.setChecked(True)
                progress["background_lock"] = VisualEngine.locked_data_range()
                window._add_enhancement_region()
                update_region_bounds(window._enhancement_selected(), bounds3d)
                window._enhancement_refresh()
            elif step == 1:
                context = window.current_render_context
                fields = window._enhancement_display(context)
                check("new region inherits current crop settings", window._enhancement_selected()["style"] == dict(levels=(12., 68., 87.), opacity_mode="sigmoid"))
                check("adding 3D region keeps background lock", VisualEngine.locked_data_range() == progress["background_lock"])
                check("adding 3D region keeps original background range", fields["background_level_info"] == progress["background_info"])
                check("adding 3D region retains volume actor", window.volume_session.volume is progress["before_actor"])
                prop = window.volume_session.volume.GetProperty()
                rgb = [0., 0., 0.]
                point = progress["bg_point"]
                prop.GetRGBTransferFunction().GetColor(float(fields["color"][point]), rgb)
                check("3D background color transfer unchanged", np.allclose(rgb, progress["bg_rgb"], atol=2e-5))
                check("3D background opacity transfer unchanged", np.isclose(fields["alpha"][point], progress["bg_alpha"], atol=1e-6))
                progress["roi_info"] = dict(fields["regions"][0]["level_info"])
                progress["full_distance"] = prop.GetScalarOpacityUnitDistance()
                samples = []
                for point in ((10, 20, 30), (30, 45, 55), (50, 70, 80)):
                    rgb = [0., 0., 0.]
                    prop.GetRGBTransferFunction().GetColor(float(fields["color"][point]), rgb)
                    samples.append(dict(value=float(context["data"][point]), rgb=rgb, alpha=float(fields["alpha"][point])))
                progress["samples"] = samples
                screenshot("3d_enhanced")
                # 锁定按钮跟随下拉框目标：先切回「全局（背景）」再解除全局锁定。
                window.page_render.region_combo.setCurrentIndex(0)
                window.page_render.btn_lock.setChecked(False)
                selection = CropSelection("3d", ("X", "Y", "E"), physical_bounds(context, bounds3d))
                window.btn_tb_crop.setChecked(True)
                window.crop_controller.set_selection(selection)
                window.on_cut()
                # Reference the ordinary crop path, without inheriting display ROIs.
                window.left_workspace.current_spec().params.pop(STATE_KEY, None)
                window.global_refresh()
            elif step == 2:
                check("actual 3D crop has identical level range and thresholds", window.volume_session.level_info == progress["roi_info"])
                prop = window.volume_session.volume.GetProperty()
                ratio = progress["full_distance"] / prop.GetScalarOpacityUnitDistance()
                for i, sample in enumerate(progress["samples"], 1):
                    rgb = [0., 0., 0.]
                    prop.GetRGBTransferFunction().GetColor(sample["value"], rgb)
                    check(f"3D sample {i} crop color equals enhancement", np.allclose(rgb, sample["rgb"], atol=2e-5))
                    a = prop.GetScalarOpacity().GetValue(sample["value"])
                    check(f"3D sample {i} crop attenuation equals enhancement", np.isclose(sample["alpha"], 1-(1-a)**ratio, atol=1e-6))
                screenshot("3d_crop")
                window.btn_tb_crop.setChecked(False)
                window.left_workspace.activate_page(progress["source"].page_id)
            elif step == 3:
                window.page_data.combo_ax.setCurrentIndex(1)
                window.page_data.s_ax_low.setValue(0)
                window.page_data.s_ax_up.setValue(window.page_data.s_ax_up.maximum())
                window.on_apply_axis_integral()
            elif step == 4:
                state = window._enhancement_state()
                for roi in list(state["regions"]):
                    window._delete_enhancement_region(roi["region_id"])
            elif step == 5:
                before = pixels()
                context = window.current_render_context
                x, y = window.ax_2d.transData.transform((context["coords"]["X"][100], context["coords"]["E"][120]))
                progress["patch"] = (slice(before.shape[0]-int(y)-12, before.shape[0]-int(y)+12), slice(int(x)-12, int(x)+12))
                progress["before_pixels"] = before[progress["patch"]].copy()
                progress["2d_background_info"] = VisualEngine._level_info(context["data"], window._get_display_levels())
                window._add_enhancement_region()
                update_region_bounds(window._enhancement_selected(), (10, 50, 30, 80))
                window._enhancement_refresh()
            elif step == 6:
                context = window.current_render_context
                fields = window._enhancement_display(context)
                check("adding 2D region preserves full background range", fields["background_level_info"] == progress["2d_background_info"])
                check("adding 2D region preserves background pixels exactly", np.array_equal(pixels()[progress["patch"]], progress["before_pixels"]))
                check("2D uses scalar crop layer", len(window.ax_2d._arpes_enhancement_images) == 1)
                region = window._enhancement_selected()
                crop = apply_selection(context, CropSelection("2d", ("X", "E"), physical_bounds(context, region["bounds"])))
                layer = window.ax_2d._arpes_enhancement_images[0]
                check("2D enhancement data equal same-range crop", np.array_equal(layer.get_array(), crop["data"].T))
                info = VisualEngine._level_info(crop["data"], region["style"]["levels"])
                norm = Normalize(info["black_value"], info["white_value"])
                expected = VisualEngine._leveled_cmap(window.page_render.get_selected_cmap(), info)(norm(crop["data"].T), bytes=True)
                actual = layer.to_rgba(layer.get_array(), bytes=True)
                check("2D crop and enhancement colors and alpha match", np.array_equal(actual, expected))
                check("2D valid samples are opaque", np.all(actual[..., 3] == 255))
                screenshot("2d_enhanced")
                snapshot = capture_snapshot(window)
                check("publication freezes scalar crop layer", len(snapshot.payload["enhancement"]["layers"]) == 1)
                render_and_save(snapshot, resolve_style("2d", ""), {}, OutputOptions(width_mm=150, height_mm=110, dpi=120), str(args.output_dir / "2d_publication.png"))
                window._delete_enhancement_region(region["region_id"])
            else:
                check("deletion restores original background pixels", np.array_equal(pixels()[progress["patch"]], progress["before_pixels"]))
                check("last deletion removes scalar layers and region entries", not window.ax_2d._arpes_enhancement_images and window.page_render.region_combo.count() == 1)
                check("scientific data unchanged", np.array_equal(progress["science"], window.core.raw_data[::25, ::25, ::25, :], equal_nan=True))
                screenshot("2d_restored")
                (args.output_dir / "acceptance.json").write_text(json.dumps(dict(data=str(args.data.resolve()), checks=checks), ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[RESULT] {len(checks)} checks passed", flush=True)
                timer.stop()
                window.close()
                app.exit(0)
                return
            progress["step"] += 1
            progress["wait"] = 4
        except Exception:
            traceback.print_exc()
            timer.stop()
            window.close()
            app.exit(1)

    timer = QTimer()
    timer.timeout.connect(tick)
    window.load_data(str(args.data))
    timer.start(250)
    QTimer.singleShot(150000, lambda: app.exit(2))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
