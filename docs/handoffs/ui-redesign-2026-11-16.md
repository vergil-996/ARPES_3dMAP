# 界面重做：深浅双主题与布局

日期：2026-11-16。状态：源码、自动化与真实窗口主题脚本已通过，随 **v1.21.0** 发布到 GitHub 与 MatElab（2026-11-16）；维护者人工视觉复核与冻结包验收未执行。本文是本事项唯一状态主记录。

## 目标与约束

- 界面只用中性黑白灰：新增 `dark` / `light` 两套色板，新环境默认浅色；颜色仍只从 `bandscope/ui/theme.py` 取，模块继续通过 `BG_*`、`TEXT_*`、`ACCENT*` 读取。
- 切换主题不重建页面、不改变数据、参数与相机；已创建控件通过 `apply_widget_theme` 重写颜色令牌，SiUI 色板、Qt 调色板、图标、2D／3D 画布背景同步刷新；选择写入 QSettings `ui/theme`。
- 数据色带与出版输出不属于界面主题：出版图继续使用 `PUB_CURVE_PALETTE` 白底身份色，本版未改变导出配色。
- 不改数据格式、Blender 工程 schema、视图复现文件格式、插件 API 与运行依赖；旧兼容导入 `theme`／`ui_controls`／`plugin_api` 仍指向同一模块。
- 新增模块：`bandscope/ui/canvas_overlay.py`（画布顶部视角预设与常用操作）、`bandscope/ui/numeric_wheel.py`（按光标数位滚轮步进）。

## 相关文件

- `bandscope/ui/theme.py`：两套色板、`set_theme`／`initialize_theme`、控件与调色板重着色、`assets/ui/check-*.svg` 勾选图标。
- `bandscope/app/refactored_app.py`：顶部深色／浅色按钮、画布工具条、专注画布、单数据源徽标、结果页标题与状态栏。
- `bandscope/ui/control_page_base.py`：可折叠扁平参数分组与折叠后的滚动区重排；同时修复折叠标题 QSS 多一个右括号（非 f-string 的 `}}`）导致 Qt 拒绝整条样式的问题。
- `bandscope/ui/page_tree.py`、`bandscope/ui/result_workspace.py`：工作区标题、数据源摘要卡片、视图类型标签与面包屑。
- `bandscope/ui/ui_controls.py`：开关按主题重着色。
- `bandscope/rendering/render_core.py`：2D 预览背景、坐标轴、刻度与标题跟随主题。
- `bandscope/exporting/publication_dialog.py`、`blender_project_dialog.py`、`bandscope/extensions/plugin_dialog.py`：次级窗口布局与样式。
- `packaging/pyinstaller/ARPES_3dMAP.spec`、`ARPES_3dMAP_gpu.spec`：收集 `assets/ui`。
- `tests/ui/test_theme.py`、`tests/ui/test_canvas_overlay.py`、`tests/ui/test_numeric_wheel.py`、`tests/integration/test_3d_background.py`；`scripts/validation/verify_ui_themes.py` 为真实窗口验收脚本。

## 验证

- 全量 `python -m unittest discover -s tests -t .`：**1703 项通过**（2026-11-16，555.205 秒），日志 `.local/logs/release-1.21.0-full.log`；`compileall` 通过。
- 真实窗口 `scripts/validation/verify_ui_themes.py`：PASS（0），覆盖新环境默认浅色、深浅切换、参数保持、相机预设与主窗口／出版导出／扩展／Blender／更新设置的实际控件状态；截图与报告在 `.local/outputs/ui-themes-1.21.0/`。脚本用屏幕抓取取图，本次运行时桌面上另有打开的 BandScope 实例，截图混入了其他窗口，只作参考，不能当作视觉验收。
- `check_release_version.py v1.21.0`、`scripts/validation/verify_plugin_release_flow.py` 与 `git diff --check` 通过；CI 产物验收见发布记录。

## 已知边界与下一步

- 主题切换在满载页面实测约 3 秒（Windows 实窗、约 2.7k 控件；应用级样式表重设与逐控件重着色各约一半），不是即时反馈；后续如需更快，可按可见窗口分批重着色，本次未改。
- 维护者人工视觉未执行：两主题的对比度与整体观感、折叠分组与窄栏／多分组布局、画布工具条与专注模式、滚轮微调手感、出版／扩展／Blender／更新设置窗口的实际观感。
- 高 DPI 与多显示器、深色系统主题下的原生菜单栏与文件对话框未专门验收。
- 冻结安装包与安装器的人工安装验收未执行，以 CI 产物为准（见 [v1.21.0 发布说明](../archive/releases/v1.21.0.md)）。
- 插件面板若在自身代码里硬编码颜色，不会随主题变化；本版未修改插件 API，后续按需在插件内改用 `theme` 令牌。
