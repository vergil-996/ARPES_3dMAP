# 分级设置窗口

日期：2026-11-21。状态：已实现并随 v1.22.1 发布到 GitHub 与 MatElab（2026-11-21）；维护者人工视觉复核与冻结包验收未执行。本文是该事项唯一状态主记录。

## 目标与范围

把顶部齿轮从弹出菜单改为非模态「设置」窗口，用左侧分级导航集中原本分散的入口：计算后端与 3D 渲染质量（原右侧外观控制）、去噪参数（原独立小窗）、插件管理与更新设置（原齿轮菜单）。

## 实现

- 新增 `bandscope/ui/settings_dialog.py`：`QTreeWidget` 分级导航 + `QStackedWidget` 页面。「计算与显示」下含「计算后端」（Auto／CPU／NVIDIA GPU，GPU 不可用时禁用并说明原因）与「3D渲染质量」（质量模式、体插值、静止质量；兼容质量时后两项禁用）；「数据处理 → 去噪参数」内嵌 `DenoiseSettingsPanel`；「应用管理 → 插件管理／更新设置」按钮分别发出 `plugins_requested`／`updates_requested` 信号。
- 主窗口 `refactored_app` 打开非模态窗口并复用同一实例：`backend_changed` → `set_backend`；`quality_changed` → `set_render_quality`，写入本机设置 `rendering/quality` 并即时作用于当前 3D 视图，跨数据源与重启共用；插件管理与更新设置接到原入口。重复点击齿轮复用窗口。
- 顶部工具栏齿轮菜单与画布右键菜单中的三个入口移除，版本号仍在状态栏。

## 验证

- 全量 `python -m unittest discover -s tests -t .`：1941 项通过（2026-11-21，155.846 秒，日志 `.local/logs/release-1.22.1-full.log`）；`compileall` 通过。
- 集成覆盖：`tests/integration/test_plugin_management_entry.py` 与 `tests/integration/test_update_context_menu.py` 通过 `window.settings_dialog` 驱动入口、页面切换与信号接线。
- 真实窗口：2026-11-21 18:55 用真实构建的插件包运行过 `scripts/validation/verify_plugin_management.py`，生成了 `settings_light.png`、`settings_dark.png`、`denoise_settings.png`、`update_settings.png` 等截图（`.local/outputs/plugin_management/`）；该次运行没有留下 `report.json` 计数记录，且未在发布前重跑，**不作为**发布门禁或视觉验收。

## 未执行 / 下一步

- 维护者人工视觉复核：分级导航、三个分类页的布局、窄窗滚动、深浅主题。
- 发布后重跑 `scripts/validation/verify_plugin_management.py`；`scripts/validation/verify_ui_themes.py` 需按新入口更新脚本中的齿轮步骤后再跑。
- 冻结安装包与安装器的人工安装验收。
