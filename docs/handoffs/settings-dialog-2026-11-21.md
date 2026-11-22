# 分级设置窗口

日期：2026-11-21。状态：原分级设置窗口已实现并随 v1.22.1 发布到 GitHub 与 MatElab（2026-11-21）；插件管理／更新设置的内嵌改造随 v1.22.3 发布到 GitHub 与 MatElab（2026-11-22），全量回归 1964 项通过，聚焦自动化、真实窗口与发布验证均通过。完整 WSe₂／主题流程、维护者人工视觉复核与冻结包验收未完成。本文是该事项唯一状态主记录。

## 目标与范围

把顶部齿轮从弹出菜单改为非模态「设置」窗口，用左侧分级导航集中原本分散的入口：计算后端与 3D 渲染质量（原右侧外观控制）、去噪参数（原独立小窗）、插件管理与更新设置（原齿轮菜单）。本轮进一步把插件管理与更新设置从分类页按钮和独立功能窗口改为左侧二级内嵌页。

## v1.22.1 发布时实现

- 新增 `bandscope/ui/settings_dialog.py`：`QTreeWidget` 分级导航 + `QStackedWidget` 页面。「计算与显示」下含「计算后端」（Auto／CPU／NVIDIA GPU，GPU 不可用时禁用并说明原因）与「3D渲染质量」（质量模式、体插值、静止质量；兼容质量时后两项禁用）；「数据处理 → 去噪参数」内嵌 `DenoiseSettingsPanel`；当时「应用管理 → 插件管理／更新设置」按钮分别发出 `plugins_requested`／`updates_requested` 信号。
- 主窗口 `refactored_app` 打开非模态窗口并复用同一实例：`backend_changed` → `set_backend`；`quality_changed` → `set_render_quality`，写入本机设置 `rendering/quality` 并即时作用于当前 3D 视图，跨数据源与重启共用；当时插件管理与更新设置接到原入口。重复点击齿轮复用窗口。
- 顶部工具栏齿轮菜单与画布右键菜单中的三个入口移除，版本号仍在状态栏。

## 当前实现：应用管理内嵌页

- `SettingsDialog` 默认尺寸为 1040×680。「应用管理」左侧二级项直接内嵌 `PluginManagerPanel` 与 `UpdateSettingsPanel`，选中「插件管理」或「更新设置」即显示对应面板；分类页不再有「打开…」按钮，也不存在独立的 `PluginManagerDialog` 或 `UpdateSettingsDialog`。
- 主窗口 `window.open_plugin_manager()` 复用 `window.settings_dialog`，调用 `select_page('plugins')` 并返回 `PluginManagerPanel`；`SettingsDialog.select_page('updates')` 选中并显示 `UpdateSettingsPanel`。`settings.plugins`／`settings.updates` 是内嵌面板，`panel.window()` 是所属 `SettingsDialog`，不是独立功能窗口。
- 插件管理保留「已安装」「官方插件」「插件设置」三页签及原有安装、启用／停用、卸载和设置操作。关闭内嵌功能时关闭 `window.settings_dialog`，不得调用 `panel.close()`；`SettingsDialog` 的关闭、隐藏与 Escape／`reject()` 统一清理插件资源并丢弃未保存的更新草稿，重开仍复用同一面板。主窗口关闭时清空设置窗口引用并 `deleteLater()`，避免内嵌原生子控件延迟析构造成退出竞争。
- 更新设置「保存」后留在当前页，「取消」恢复已保存值；「检查更新」先保存再启动手动检查，主设置窗口不关闭。检查或下载期间禁止编辑；MatElab 登录等必要弹窗仍保留。

## 验证

### v1.22.1 发布验证

- 全量 `python -m unittest discover -s tests -t .`：1941 项通过（2026-11-21，155.846 秒，日志 `.local/logs/release-1.22.1-full.log`）；`compileall` 通过。
- 当时的集成覆盖：`tests/integration/test_plugin_management_entry.py` 与 `tests/integration/test_update_context_menu.py` 通过 `window.settings_dialog` 驱动入口、页面切换与信号接线。
- 真实窗口：2026-11-21 18:55 用真实构建的插件包运行过 `scripts/validation/verify_plugin_management.py`，生成了 `settings_light.png`、`settings_dark.png`、`denoise_settings.png`、`update_settings.png` 等截图（`.local/outputs/plugin_management/`）；该次运行没有留下 `report.json` 计数记录，且未在发布前重跑，**不作为**发布门禁或视觉验收。

### 本轮自动化

- 最终聚焦回归：`tests.extensions.test_plugin_dialog`、`test_catalog_dialog`、`test_plugin_settings_dialog`、`tests.updates.test_update_settings_dialog`、`test_update_sources`、`test_update_auto_check`、`tests.integration.test_update_context_menu`、`test_plugin_management_entry` 共 **96 项通过**。覆盖真实 OpenGL 子进程内嵌入口、更新保存／取消／检查、忙碌与自动检查互斥、真实 Qt 线程关闭引用，以及 Escape／`reject()` 清理和重开。
- 全量 `PYTHONUTF8=1 python -m unittest discover -s tests -t . -v`：共 **1964 项，3 项失败、4 项跳过**；本功能的插件、更新和真实 OpenGL 设置入口测试均通过。失败仅为 `tests.exporting.test_orientation_preview.OrientationPreviewTests.test_hover_cursor_and_disabled_interaction`，以及 `tests.integration.test_crop_controls.CropControlTests` 的两个弹层位置用例；它们独立运行同样失败，本次未修改相关模块。日志 `.local/logs/settings-embedded-full-verified.log`。
- `python -m compileall -q bandscope plugins scripts tests start.py`：通过。

聚焦与全量结果均按实际执行记录，不能把全量状态写成全部通过。

### 本轮真实窗口

- `scripts/validation/verify_plugin_management.py`：最终日志重定向运行正常退出且全部通过，覆盖内嵌三页签、更新控件、单实例复用、裁剪模式，以及 646×400 下深浅主题、插件按钮和更新页滚动可达性；截图在 `.local/outputs/plugin_management/`。更新页已根据截图修正为顶部对齐。脚本显式禁用自动联网检查；主窗口显式释放设置窗口后，原始 OpenGL 入口子进程连续 10 次均以 0 退出，无此前间歇性访问冲突。
- `scripts/validation/verify_derived_waterfall.py .local/data/integration_context_menu_synthetic.npz`：**24/24 通过**，覆盖内嵌「插件设置」的默认值保存、新建页应用与当前页应用。这是真实窗口＋合成动态数据，不能写成 WSe₂ 真实数据验收。
- `scripts/validation/verify_ui_themes.py`：默认 `.local/data/WSe2_step.npz` 及原来源路径不存在；改用合成动态数据后在进入本轮设置页之前，被既有相机预设断言拦住，故该脚本本轮 **未通过／未完成完整主题验收**。两个设置内嵌页的深浅主题由上述插件管理脚本覆盖。

## 未执行 / 下一步

- 上述既有 3 项失败在当前树的全量发布回归中未再出现（`python -m unittest discover -s tests -t .` 1964 项全部通过）；若再次出现，仍按方向预览与裁剪弹层责任范围排查，不以本功能回归掩盖。
- WSe₂ 真实数据与完整 `verify_ui_themes.py` 验收未完成；数据已在 `.local/data/WSe2_step.npz`，仍需先核对既有相机预设断言是否与当前渲染环境有关。
- 维护者人工视觉复核：分级导航、分类页与两个内嵌页布局、窄窗滚动、深浅主题。
- 冻结安装包与安装器的人工安装验收。

## v1.22.3 发布验证（2026-11-22）

- 全量 `python -m unittest discover -s tests -t .`：**1964 项通过**（159.199 秒，单进程，日志 `.local/logs/release-1.22.3-full.log`）；`compileall` 通过。
- `scripts/validation/verify_plugin_management.py` 在最终树上重跑：全部通过，覆盖「设置 → 应用管理」内嵌插件管理与更新设置、三页签、保存／取消／检查更新、646×400 窄窗滚动可达性及深浅主题截图（`.local/outputs/plugin_management/`）。
- 发布：GitHub Release 与 MatElab 记录「BandScope v1.22.3」均于 2026-11-22 发布，验证记录见 [v1.22.3](../archive/releases/v1.22.3.md)。
