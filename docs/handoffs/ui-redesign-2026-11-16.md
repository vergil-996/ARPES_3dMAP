# 界面重做：深浅双主题与布局

日期：2026-11-16。状态：源码、自动化与真实窗口主题脚本已通过，随 **v1.21.0** 发布到 GitHub 与 MatElab（2026-11-16）；v1.21.1 的入口与主题修正随 **v1.21.1** 发布到 GitHub 与 MatElab（2026-11-17）；维护者人工视觉复核与冻结包验收未执行。本文是本事项唯一状态主记录。

## 目标与约束

- 界面只用中性黑白灰：新增 `dark` / `light` 两套色板，新环境默认浅色；颜色仍只从 `bandscope/ui/theme.py` 取，模块继续通过 `BG_*`、`TEXT_*`、`ACCENT*` 读取。
- 切换主题不重建页面、不改变数据、参数与相机；已创建控件通过 `apply_widget_theme` 重写颜色令牌，SiUI 色板、Qt 调色板、图标、2D／3D 画布背景同步刷新；选择写入 QSettings `ui/theme`。
- 数据色带与出版输出不属于界面主题：出版图继续使用 `PUB_CURVE_PALETTE` 白底身份色，本版未改变导出配色。
- 不改数据格式、Blender 工程 schema、视图复现文件格式、插件 API 与运行依赖；旧兼容导入 `theme`／`ui_controls`／`plugin_api` 仍指向同一模块。
- 新增模块：`bandscope/ui/canvas_overlay.py`（画布顶部视角预设与常用操作）、`bandscope/ui/numeric_wheel.py`（按光标数位滚轮步进）。

## 相关文件

- `bandscope/ui/theme.py`：两套色板、`set_theme`／`initialize_theme`、控件与调色板重着色、`assets/ui/check-*.svg` 勾选图标。
- `bandscope/app/refactored_app.py`：顶部 `DayNightSwitch` 昼夜动画开关、画布工具条、专注画布、单数据源徽标、结果页标题与状态栏。
- `bandscope/ui/control_page_base.py`：可折叠扁平参数分组与折叠后的滚动区重排；同时修复折叠标题 QSS 多一个右括号（非 f-string 的 `}}`）导致 Qt 拒绝整条样式的问题。
- `bandscope/ui/page_tree.py`、`bandscope/ui/result_workspace.py`：工作区标题、数据源摘要卡片、视图类型标签与面包屑。
- `bandscope/ui/ui_controls.py`：开关按主题重着色。
- `bandscope/rendering/render_core.py`：2D 预览背景、坐标轴、刻度与标题跟随主题。
- `bandscope/exporting/publication_dialog.py`、`blender_project_dialog.py`、`bandscope/extensions/plugin_dialog.py`：次级窗口布局与样式。
- `packaging/pyinstaller/ARPES_3dMAP.spec`、`ARPES_3dMAP_gpu.spec`：收集 `assets/ui`。
- `tests/ui/test_theme.py`、`tests/ui/test_day_night_switch.py`、`tests/ui/test_canvas_overlay.py`、`tests/ui/test_numeric_wheel.py`、`tests/integration/test_3d_background.py`；`scripts/validation/verify_ui_themes.py` 为真实窗口验收脚本。

## 验证

- 全量 `python -m unittest discover -s tests -t .`：**1703 项通过**（2026-11-16，555.205 秒），日志 `.local/logs/release-1.21.0-full.log`；`compileall` 通过。
- 真实窗口 `scripts/validation/verify_ui_themes.py`：PASS（0），覆盖新环境默认浅色、深浅切换、参数保持、相机预设与主窗口／出版导出／扩展／Blender／更新设置的实际控件状态；截图与报告在 `.local/outputs/ui-themes-1.21.0/`。脚本用屏幕抓取取图，本次运行时桌面上另有打开的 BandScope 实例，截图混入了其他窗口，只作参考，不能当作视觉验收。
- `check_release_version.py v1.21.0`、`scripts/validation/verify_plugin_release_flow.py` 与 `git diff --check` 通过；CI 产物验收见发布记录。

## v1.21.1 修正（2026-11-17）

v1.21.0 之后按实际使用反馈修正入口与主题细节，不改数据格式、视图复现文件格式、Blender 工程 schema、插件 API 与运行依赖。

- 顶部工具栏的「扩展」按钮与右侧版本按钮合并为齿轮设置菜单（更新设置／插件管理／去噪参数设置），版本号移到状态栏；画布右键移除重复入口。
- 更新设置窗口新增「检查更新」，先应用设置再检查；新版本提示把更新说明与插件影响收进默认折叠的「详细信息」。
- 画布工具条改到右上角竖排，3D 预设按画布高度收起；圆角用原生窗口遮罩绘制，避免 VTK 画布直角黑边。
- 色带锁定按钮改为只显示图标的动画开关（180 ms），主题切换与状态恢复保持图标一致。
- 主题刷新改用 `palette(...)` 角色与逐控件调色板：浅色主题下列表/菜单文字保持黑色；2D 标题、坐标轴、刻度、图例、色条与裁剪选择框，3D 标量条与坐标轴随主题更新；切换主题不触发数据重渲染，参数与相机保持不变。
- 验证：全量 1728 项通过（2026-11-17，218.983 秒，日志 `.local/logs/release-1.21.1-full.log`）；`verify_ui_themes.py` PASS（0）；`verify_plugin_management.py` 全部通过；`compileall`、`check_release_version.py v1.21.1`、`verify_plugin_release_flow.py` 与 `git diff --check` 通过。`verify_color_lock.py` 的默认百分位归一化会让各帧 P99.5 端点相同，已在脚本内切到完整范围后复验，3D／2D 锁定与解锁全部通过。
- 发布：GitHub Release CI（run 44009625220，26m59s）成功，共 12 个资产；`check_for_update` 从 1.21.0 发现 1.21.1，CPU／NVIDIA 完整下载并按 SHA-256 校验，插件包与目录 Ed25519 验签通过。MatElab 发布记录「BandScope v1.21.1」（uid `bandscope-release-1.21.1`）建于「BandScope发布」记录本；只读元数据核对两个 flavor 均发现 1.21.1、文件名与大小一致，按约定未做回传下载复验。

## 昼夜动画开关（2026-11-23，未发布）

- 顶栏原「深色／浅色」双按钮替换为 `bandscope/ui/day_night_switch.py` 的 96×38 自绘开关：浅色显示太阳与云层，深色显示月亮与星空；主题过渡为 560 ms，另含悬停回弹、焦点与禁用状态。鼠标、空格与外部主题变更共用原有主题入口，仍保存 `ui/theme`，不重建页面或请求数据重渲染。
- 自动化：`tests.ui.test_day_night_switch` 13 项、`tests.integration.test_3d_background` 7 项通过，覆盖鼠标／键盘、动画中点与终点、双层光晕、星场、云层、状态恢复、参数与主题持久化。
- 集成回归：`PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -t . -v` 共 2071 项通过（184.466 秒，日志 `.local/logs/day-night-switch-full-utf8.log`）；`python -m compileall -q bandscope plugins scripts tests start.py` 通过。
- 真实窗口：`QT_QPA_PLATFORM=windows python scripts/validation/verify_fluent_icon_windows.py` 使用 WSe₂ 在 Windows、DPR 1.25 下通过，深浅主题、专注模式和次级窗口截图位于 `.local/outputs/icon-dpi-audit/windows/`；开关最终独立的浅色／中间帧／深色渲染位于 `.local/outputs/day-night-switch-final/`。跨屏移动未执行。
- `verify_ui_themes.py` 的新开关与主题断言已通过并生成深色主窗口截图，但完整脚本仍在既有相机预设步骤失败：有效 3D 场景下调用 `_set_camera_view_preset("xy")` 后所有预设按钮均未保持勾选。本轮未修改该无关相机逻辑，因此不能把本次完整主题脚本记为通过。

## 已知边界与下一步

- 主题切换在满载页面实测约 3 秒（Windows 实窗、约 2.7k 控件；应用级样式表重设与逐控件重着色各约一半），不是即时反馈；后续如需更快，可按可见窗口分批重着色，本次未改。
- 维护者人工视觉未执行：两主题的对比度与整体观感、折叠分组与窄栏／多分组布局、画布工具条与专注模式、滚轮微调手感、出版／扩展／Blender／更新设置窗口的实际观感。
- 高 DPI 与多显示器、深色系统主题下的原生菜单栏与文件对话框未专门验收。
- 冻结安装包与安装器的人工安装验收未执行，以 CI 产物为准（见 [v1.21.0 发布说明](../archive/releases/v1.21.0.md)）。
- 插件面板若在自身代码里硬编码颜色，不会随主题变化；本版未修改插件 API，后续按需在插件内改用 `theme` 令牌。
