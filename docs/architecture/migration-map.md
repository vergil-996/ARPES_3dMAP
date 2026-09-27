# 2026-09-27 目录迁移清单

本表以迁移前工作区为来源，包含当时尚未提交的插件实现。已有删除的相机交接文档未恢复。应用版本仍为 1.8.0。

根目录 `start.py` 保留为启动入口；`plugin_api.py`、`theme.py`、`ui_controls.py` 保留为薄兼容模块。其余旧源码路径不再作为导入入口。

## 主程序

| 旧路径 | 新路径 |
|---|---|
| `refactored_app.py` | [bandscope/app/refactored_app.py](../../bandscope/app/refactored_app.py) |
| `crop_integration.py` | [bandscope/app/crop_integration.py](../../bandscope/app/crop_integration.py) |
| `qt_bootstrap.py` | [bandscope/app/qt_bootstrap.py](../../bandscope/app/qt_bootstrap.py) |
| `analyzer_core.py` | [bandscope/core/analyzer_core.py](../../bandscope/core/analyzer_core.py) |
| `axis_mapping.py` | [bandscope/core/axis_mapping.py](../../bandscope/core/axis_mapping.py) |
| `data_scope.py` | [bandscope/core/data_scope.py](../../bandscope/core/data_scope.py) |
| `data_trans.py` | [bandscope/core/data_trans.py](../../bandscope/core/data_trans.py) |
| `crop_model.py` | [bandscope/core/crop_model.py](../../bandscope/core/crop_model.py) |
| `compute_backends.py` | [bandscope/processing/compute_backends.py](../../bandscope/processing/compute_backends.py) |
| `denoise_config.py` | [bandscope/processing/denoise_config.py](../../bandscope/processing/denoise_config.py) |
| `denoise_engines.py` | [bandscope/processing/denoise_engines.py](../../bandscope/processing/denoise_engines.py) |
| `render_core.py` | [bandscope/rendering/render_core.py](../../bandscope/rendering/render_core.py) |
| `refresh_pipeline.py` | [bandscope/rendering/refresh_pipeline.py](../../bandscope/rendering/refresh_pipeline.py) |
| `blank_control_page.py` | [bandscope/ui/blank_control_page.py](../../bandscope/ui/blank_control_page.py) |
| `camera_view_controls.py` | [bandscope/ui/camera_view_controls.py](../../bandscope/ui/camera_view_controls.py) |
| `control_layout_utils.py` | [bandscope/ui/control_layout_utils.py](../../bandscope/ui/control_layout_utils.py) |
| `control_page_base.py` | [bandscope/ui/control_page_base.py](../../bandscope/ui/control_page_base.py) |
| `crop_controls.py` | [bandscope/ui/crop_controls.py](../../bandscope/ui/crop_controls.py) |
| `denoise_control_utils.py` | [bandscope/ui/denoise_control_utils.py](../../bandscope/ui/denoise_control_utils.py) |
| `page_data_process_v2.py` | [bandscope/ui/page_data_process_v2.py](../../bandscope/ui/page_data_process_v2.py) |
| `page_render_control.py` | [bandscope/ui/page_render_control.py](../../bandscope/ui/page_render_control.py) |
| `plot_coordinate_tooltip.py` | [bandscope/ui/plot_coordinate_tooltip.py](../../bandscope/ui/plot_coordinate_tooltip.py) |
| `result_workspace.py` | [bandscope/ui/result_workspace.py](../../bandscope/ui/result_workspace.py) |
| `settings_popups.py` | [bandscope/ui/settings_popups.py](../../bandscope/ui/settings_popups.py) |
| `theme.py` | [bandscope/ui/theme.py](../../bandscope/ui/theme.py) |
| `timeline_bar.py` | [bandscope/ui/timeline_bar.py](../../bandscope/ui/timeline_bar.py) |
| `toast.py` | [bandscope/ui/toast.py](../../bandscope/ui/toast.py) |
| `ui_controls.py` | [bandscope/ui/ui_controls.py](../../bandscope/ui/ui_controls.py) |
| `publication_dialog.py` | [bandscope/exporting/publication_dialog.py](../../bandscope/exporting/publication_dialog.py) |
| `publication_export.py` | [bandscope/exporting/publication_export.py](../../bandscope/exporting/publication_export.py) |
| `publication_models.py` | [bandscope/exporting/publication_models.py](../../bandscope/exporting/publication_models.py) |
| `publication_renderers.py` | [bandscope/exporting/publication_renderers.py](../../bandscope/exporting/publication_renderers.py) |
| `plugin_manager.py` | [bandscope/extensions/plugin_manager.py](../../bandscope/extensions/plugin_manager.py) |
| `plugin_host.py` | [bandscope/extensions/plugin_host.py](../../bandscope/extensions/plugin_host.py) |
| `plugin_dialog.py` | [bandscope/extensions/plugin_dialog.py](../../bandscope/extensions/plugin_dialog.py) |
| `update_controller.py` | [bandscope/updates/update_controller.py](../../bandscope/updates/update_controller.py) |
| `update_service.py` | [bandscope/updates/update_service.py](../../bandscope/updates/update_service.py) |
| `app_metadata.py` | [bandscope/app_metadata.py](../../bandscope/app_metadata.py) |
| `plugin_api.py` | [bandscope/extensions/api.py](../../bandscope/extensions/api.py) |
| `start.py` | [bandscope/app/startup.py](../../bandscope/app/startup.py) |

## 自动化测试

| 旧路径 | 新路径 |
|---|---|
| `tests/test_analysis_control_refresh.py` | [tests/integration/test_analysis_control_refresh.py](../../tests/integration/test_analysis_control_refresh.py) |
| `tests/test_axis_crop_blit.py` | [tests/integration/test_axis_crop_blit.py](../../tests/integration/test_axis_crop_blit.py) |
| `tests/test_axis_mapping.py` | [tests/core/test_axis_mapping.py](../../tests/core/test_axis_mapping.py) |
| `tests/test_camera_persistence.py` | [tests/integration/test_camera_persistence.py](../../tests/integration/test_camera_persistence.py) |
| `tests/test_camera_view_controls.py` | [tests/integration/test_camera_view_controls.py](../../tests/integration/test_camera_view_controls.py) |
| `tests/test_color_lock.py` | [tests/rendering/test_color_lock.py](../../tests/rendering/test_color_lock.py) |
| `tests/test_compute_backends.py` | [tests/processing/test_compute_backends.py](../../tests/processing/test_compute_backends.py) |
| `tests/test_control_layout_utils.py` | [tests/ui/test_control_layout_utils.py](../../tests/ui/test_control_layout_utils.py) |
| `tests/test_control_page_scrolling.py` | [tests/ui/test_control_page_scrolling.py](../../tests/ui/test_control_page_scrolling.py) |
| `tests/test_control_page_state.py` | [tests/ui/test_control_page_state.py](../../tests/ui/test_control_page_state.py) |
| `tests/test_crop_controls.py` | [tests/integration/test_crop_controls.py](../../tests/integration/test_crop_controls.py) |
| `tests/test_crop_integration.py` | [tests/integration/test_crop_integration.py](../../tests/integration/test_crop_integration.py) |
| `tests/test_crop_model.py` | [tests/core/test_crop_model.py](../../tests/core/test_crop_model.py) |
| `tests/test_curve_log.py` | [tests/integration/test_curve_log.py](../../tests/integration/test_curve_log.py) |
| `tests/test_data_scope.py` | [tests/integration/test_data_scope.py](../../tests/integration/test_data_scope.py) |
| `tests/test_denoise_pipeline.py` | [tests/integration/test_denoise_pipeline.py](../../tests/integration/test_denoise_pipeline.py) |
| `tests/test_energy_dos_time_integral.py` | [tests/integration/test_energy_dos_time_integral.py](../../tests/integration/test_energy_dos_time_integral.py) |
| `tests/test_erase_regions.py` | [tests/integration/test_erase_regions.py](../../tests/integration/test_erase_regions.py) |
| `tests/test_export_payloads.py` | [tests/integration/test_export_payloads.py](../../tests/integration/test_export_payloads.py) |
| `tests/test_e_axis_flip.py` | [tests/integration/test_e_axis_flip.py](../../tests/integration/test_e_axis_flip.py) |
| `tests/test_memory_sharing.py` | [tests/integration/test_memory_sharing.py](../../tests/integration/test_memory_sharing.py) |
| `tests/test_plot_coordinate_tooltip.py` | [tests/ui/test_plot_coordinate_tooltip.py](../../tests/ui/test_plot_coordinate_tooltip.py) |
| `tests/test_publication_axis_label_ui.py` | [tests/exporting/test_publication_axis_label_ui.py](../../tests/exporting/test_publication_axis_label_ui.py) |
| `tests/test_publication_export.py` | [tests/exporting/test_publication_export.py](../../tests/exporting/test_publication_export.py) |
| `tests/test_publication_output_options_ui.py` | [tests/exporting/test_publication_output_options_ui.py](../../tests/exporting/test_publication_output_options_ui.py) |
| `tests/test_publication_title_ui.py` | [tests/exporting/test_publication_title_ui.py](../../tests/exporting/test_publication_title_ui.py) |
| `tests/test_refresh_pipeline.py` | [tests/rendering/test_refresh_pipeline.py](../../tests/rendering/test_refresh_pipeline.py) |
| `tests/test_render_2d_aspect.py` | [tests/rendering/test_render_2d_aspect.py](../../tests/rendering/test_render_2d_aspect.py) |
| `tests/test_render_2d_fast_path.py` | [tests/rendering/test_render_2d_fast_path.py](../../tests/rendering/test_render_2d_fast_path.py) |
| `tests/test_render_status_overlay.py` | [tests/integration/test_render_status_overlay.py](../../tests/integration/test_render_status_overlay.py) |
| `tests/test_rotation_preview.py` | [tests/integration/test_rotation_preview.py](../../tests/integration/test_rotation_preview.py) |
| `tests/test_second_derivative.py` | [tests/integration/test_second_derivative.py](../../tests/integration/test_second_derivative.py) |
| `tests/test_tabular_export.py` | [tests/integration/test_tabular_export.py](../../tests/integration/test_tabular_export.py) |
| `tests/test_time_integral_derivation.py` | [tests/integration/test_time_integral_derivation.py](../../tests/integration/test_time_integral_derivation.py) |
| `tests/test_time_integrated_slice.py` | [tests/core/test_time_integrated_slice.py](../../tests/core/test_time_integrated_slice.py) |
| `tests/test_time_value_box_sync.py` | [tests/integration/test_time_value_box_sync.py](../../tests/integration/test_time_value_box_sync.py) |
| `tests/test_toast.py` | [tests/integration/test_toast.py](../../tests/integration/test_toast.py) |
| `tests/test_update_auto_check.py` | [tests/updates/test_update_auto_check.py](../../tests/updates/test_update_auto_check.py) |
| `tests/test_update_context_menu.py` | [tests/integration/test_update_context_menu.py](../../tests/integration/test_update_context_menu.py) |
| `tests/test_update_service.py` | [tests/updates/test_update_service.py](../../tests/updates/test_update_service.py) |
| `tests/test_volume_render_session.py` | [tests/rendering/test_volume_render_session.py](../../tests/rendering/test_volume_render_session.py) |
| `tests_plugins/test_plugin_manager.py` | [tests/extensions/test_plugin_manager.py](../../tests/extensions/test_plugin_manager.py) |
| `tests_plugins/test_plugin_session.py` | [tests/extensions/test_plugin_session.py](../../tests/extensions/test_plugin_session.py) |
| `tests_plugins/test_flat_band_panel.py` | [tests/plugins/flat_band_opacity/test_flat_band_panel.py](../../tests/plugins/flat_band_opacity/test_flat_band_panel.py) |
| `tests_plugins/test_flat_band_effect.py` | [tests/plugins/flat_band_opacity/test_flat_band_effect.py](../../tests/plugins/flat_band_opacity/test_flat_band_effect.py) |
| `tests_plugins/test_alpha_channel.py` | [tests/rendering/test_alpha_channel.py](../../tests/rendering/test_alpha_channel.py) |
| `tests_plugins/test_export_effect.py` | [tests/exporting/test_export_effect.py](../../tests/exporting/test_export_effect.py) |

## 脚本与构建

| 旧路径 | 新路径 |
|---|---|
| `scripts/build_plugin.py` | [scripts/release/build_plugin.py](../../scripts/release/build_plugin.py) |
| `scripts/check_release_version.py` | [scripts/release/check_release_version.py](../../scripts/release/check_release_version.py) |
| `scripts/export_acceptance.py` | [scripts/validation/export_acceptance.py](../../scripts/validation/export_acceptance.py) |
| `scripts/probe_frame_ranges.py` | [scripts/diagnostics/probe_frame_ranges.py](../../scripts/diagnostics/probe_frame_ranges.py) |
| `scripts/sign_windows.ps1` | [scripts/release/sign_windows.ps1](../../scripts/release/sign_windows.ps1) |
| `scripts/smoke_test.py` | [scripts/validation/smoke_test.py](../../scripts/validation/smoke_test.py) |
| `scripts/verify_2d_aspect.py` | [scripts/validation/verify_2d_aspect.py](../../scripts/validation/verify_2d_aspect.py) |
| `scripts/verify_camera_view_panel.py` | [scripts/validation/verify_camera_view_panel.py](../../scripts/validation/verify_camera_view_panel.py) |
| `scripts/verify_color_lock.py` | [scripts/validation/verify_color_lock.py](../../scripts/validation/verify_color_lock.py) |
| `scripts/verify_flat_band_alpha.py` | [scripts/validation/verify_flat_band_alpha.py](../../scripts/validation/verify_flat_band_alpha.py) |
| `scripts/verify_time_axis_visibility.py` | [scripts/validation/verify_time_axis_visibility.py](../../scripts/validation/verify_time_axis_visibility.py) |
| `scripts/_verify_settled_grab.py` | [scripts/diagnostics/_verify_settled_grab.py](../../scripts/diagnostics/_verify_settled_grab.py) |
| `ARPES_3dMAP.spec` | [packaging/pyinstaller/ARPES_3dMAP.spec](../../packaging/pyinstaller/ARPES_3dMAP.spec) |
| `ARPES_3dMAP_gpu.spec` | [packaging/pyinstaller/ARPES_3dMAP_gpu.spec](../../packaging/pyinstaller/ARPES_3dMAP_gpu.spec) |
| `installer/BandScope.iss` | [packaging/windows/BandScope.iss](../../packaging/windows/BandScope.iss) |

## 文档与资源

| 旧路径 | 新路径 |
|---|---|
| `app.ico` | [assets/app.ico](../../assets/app.ico) |
| `CROP_HANDOFF_2026-09-24.md` | [docs/archive/crop/CROP_HANDOFF_2026-09-24.md](../archive/crop/CROP_HANDOFF_2026-09-24.md) |
| `design/flat_band_opacity_plugin_plan_2026-09-27.md` | [docs/plans/flat_band_opacity_plugin_plan_2026-09-27.md](../plans/flat_band_opacity_plugin_plan_2026-09-27.md) |
| `design/mockup_full.png` | [docs/archive/ui/mockup_full.png](../archive/ui/mockup_full.png) |
| `design/publication_export_acceptance` | [docs/archive/publication-export/publication_export_acceptance](../archive/publication-export/publication_export_acceptance) |
| `design/publication_export_research_plan_2026-09-21.md` | [docs/archive/publication-export/publication_export_research_plan_2026-09-21.md](../archive/publication-export/publication_export_research_plan_2026-09-21.md) |
| `design/ui_redesign_mockup.html` | [docs/archive/ui/ui_redesign_mockup.html](../archive/ui/ui_redesign_mockup.html) |
| `design/ui_review_2026-09-08.md` | [docs/archive/ui/ui_review_2026-09-08.md](../archive/ui/ui_review_2026-09-08.md) |
| `design/ui_review_actual.jpg` | [docs/archive/ui/ui_review_actual.jpg](../archive/ui/ui_review_actual.jpg) |

## 本机材料

`smoke_data/` → `.local/data/`；`smoke_output/` → `.local/outputs/`；根目录日志 → `.local/logs/`；本地发布维护说明 → `.local/maintainer/`。内容保留，继续忽略。旧测试目录仅剩的缓存移入 `.local/outputs/legacy-caches/`，未删除。
