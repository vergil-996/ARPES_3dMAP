# 开发与测试

所有示例从仓库根目录执行。Windows 可用 `.venv\Scripts\python.exe` 替代 `python`。

## 环境与启动

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe start.py
```

主程序位于 `bandscope/`，无需 `pip install -e .`。直接启动入口负责 Qt 初始化顺序；应用图标依据源码或冻结目录解析。

## 自动化回归

```powershell
python -m unittest discover -s tests -t . -v
python -m unittest discover -s tests/extensions -t . -v
python -m unittest tests.core.test_axis_mapping -v
python -m unittest tests.core.test_axis_assignment tests.ui.test_axis_assignment_dialog tests.integration.test_axis_assignment -v
python -m compileall -q bandscope plugins scripts tests start.py
```

测试全部纳入 Git；默认 Qt 离屏运行、临时扩展目录和临时 INI 设置，不依赖 `.local/data/`。时间控件测试在临时目录生成含非均匀时间坐标的 NPZ；轴分配测试另用小型合成 NPZ/MAT 覆盖时间、角度、温度、裸 `T`、缺失坐标、等长轴、降序非均匀坐标、静态／扫描三维、重设事务和两种分配的复现往返。共享导出快照构造器位于 `tests/support/publication.py`。

结果创建集成测试若不以菜单委托为验收目标，应直接调用主窗口 `_on_page_create_requested(parent_page_id, action_key)` 并检查 `ResultWorkspace` 中的新页、父子关系和冻结参数，不再构造已删除的「处理分析」页或旧下拉框。页面树已移除创建入口，动作过滤与视图右键菜单委托由 `tests/integration/test_integration_context_menu.py` 覆盖，`tests/ui/test_page_tree.py` 只断言页面树不再出现创建子菜单。入口迁移的聚焦回归可运行：

```powershell
python -m unittest tests.ui.test_page_tree tests.ui.test_control_page_state tests.integration.test_integration_context_menu tests.integration.test_plugin_derived_flow tests.integration.test_plugin_analysis_flow tests.extensions.test_plugin_analysis tests.extensions.test_derived_host tests.integration.test_crop_integration tests.integration.test_axis_interval_pages tests.integration.test_analysis_control_refresh tests.integration.test_second_derivative tests.integration.test_view_recipe_roundtrip -v
```

测试需要 NumPy、Qt、VTK 等运行依赖，但不要求 CUDA。加载 NPZ 时会在后台线程计算文件摘要，Windows 上紧接着删除临时目录可能因文件仍被打开而报 WinError 32；`tests/__init__.py` 对**清理动作**做 5 秒有界重试，断言本身不变。Windows 沙箱如果导致 `TemporaryDirectory` 新建后不可读写，应在具备正常临时目录权限的环境复核，并保留错误日志；不要通过删除断言绕过。

## 视觉与真实数据验证

这些脚本读取显式输入文件，默认输出至 `.local/outputs/<检查名>/`。真实数据无需加入 Git。默认隔离用户设置和扩展目录；完整窗口验收默认使用本机图形平台，需要桌面环境。Windows 上 VTK 嵌入窗口不能使用 Qt 的 `offscreen` 平台；若此前设置过该变量，运行前设为 `windows`。纯自动化回归仍默认离屏。

```powershell
python scripts/validation/smoke_test.py .local/data/scan07_dynamic.npz --dynamic
python scripts/validation/verify_color_lock.py .local/data/scan07_dynamic.npz
python scripts/validation/verify_2d_aspect.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_camera_view_panel.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_time_axis_visibility.py .local/data/scan07_dynamic.npz .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_axis_assignment.py .local/data/WSe2_step.npz
python scripts/validation/export_acceptance.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_flat_band_alpha.py --bands 5
python scripts/validation/volume_display_quality.py .local/data/WSe2_step.npz D:/ARPES数据/npz_files/西湖/Au_cut.npz D:/ARPES数据/npz_files/西湖/NiHITP_cut.npz --blender D:/blender/blender.exe
python scripts/validation/verify_view_recipe.py
python scripts/validation/verify_ui_themes.py
python scripts/validation/verify_fluent_icon_dpi.py
python scripts/validation/verify_fluent_icon_windows.py
python scripts/validation/verify_levels_and_colormap_range.py .local/data/WSe2_step.npz
python scripts/validation/verify_independent_opacity_curve.py .local/data/WSe2_step.npz
python scripts/validation/verify_multi_source_workspaces.py
python scripts/validation/verify_plugin_management.py
python scripts/validation/verify_plugin_release_flow.py
python scripts/validation/verify_integration_context_menu.py .local/data/WSe2_step.npz
python scripts/validation/verify_derived_waterfall.py .local/data/<数据>.npz
python scripts/validation/verify_path_slice.py .local/data/WSe2_step.npz
python scripts/validation/verify_canvas_axis_slices.py .local/data/WSe2_step.npz
python scripts/validation/verify_canvas_rotation_dial.py .local/data/WSe2_step.npz
python scripts/validation/blender_export_acceptance.py --blender D:/blender/blender.exe
python scripts/validation/blender_export_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe
python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
python scripts/validation/blender_project_roundtrip.py --blender D:/blender/blender.exe
python scripts/validation/blender_volume_lighting.py --blender D:/blender/blender.exe
python scripts/validation/blender_dialog_acceptance.py .local/data/WSe2_step.npz --blender D:/blender/blender.exe --volume-mode emission
python scripts/validation/blender_dialog_acceptance.py .local/data/WSe2_step.npz --blender D:/blender/blender.exe --volume-mode hybrid_lit
python scripts/validation/blender_recipe_acceptance.py --blender D:/blender/blender.exe
python scripts/validation/verify_frozen_blender_flow.py <BandScope.exe> --blender D:/blender/blender.exe
```

`verify_ui_themes.py` 使用隔离设置检查新环境默认浅色、深浅主题切换、参数保持，以及主窗口、出版导出、Blender 和设置窗口的真实截图；插件管理与更新设置截图均通过 `window.open_plugin_manager()` 或 `SettingsDialog.select_page('updates')` 选中「设置 → 应用管理」的内嵌页后截取整个主设置窗口，不再创建或关闭独立功能窗口。

`verify_fluent_icon_dpi.py` 在 Windows 上以独立进程覆盖 DPR 1、1.25、1.5、1.5625、1.75、2，用真实 `ActionButton` 对照 24 个 Fluent 图标的旧版位图、新引擎和目标物理尺寸 SVG，并覆盖禁用与勾选状态；输出逐像素报告和分倍率对照图。`verify_fluent_icon_windows.py` 使用默认 WSe₂ 数据和隔离设置截取深浅主题下的主窗口、专注模式、出版导出、插件管理及 Blender 标题，报告实际屏幕 DPR 和未执行的跨屏操作。两者默认输出至 `.local/outputs/icon-dpi-audit/`。

`verify_levels_and_colormap_range.py` 默认使用 WSe₂ 数据，驱动累计直方图、黑灰白输入色阶与双端输出色带，检查连续拖动缓存复用、切帧、局部区域、全部显示、二维切片、窄面板和深浅主题；色带切换统一走「输出色带」标题行的新菜单，并核对默认 `magma`、菜单首两项、`coolwarm` 唯一、换色保留输出端点、每次只完成一次精确刷新、重选或取消不刷新、菜单滚动与色样／勾选、标题行色名与渐变条同宽。输出窗口／三维帧缓冲截图、深浅主题色阶卡片、标题行、展开菜单与两组控件截图及 `report.json`。`verify_independent_opacity_curve.py` 在同一真实数据上检查默认三点曲线与旧兼容线性一致、颜色控件不改变 α、下降段与平台的逐体素曲线、拖动曲线点不带动底轨、底轨水平定位与向上加点、黑点脱离外轮廓并连续穿过底轨与横轴间距、拖回底轨重新挂回、碰线前保持空心不回填、原位回填动画、碰线前松手、命中已有点、α＝0 加点、零线编辑与重新打开后保留、右键与 Delete 删除、端点保护、焦点离开后 Delete 无效、深浅主题与窄面板，并核对 CPU／可用 CUDA、出版导出与 Blender 密度的参考距离换算及 schema 4→5 迁移误差；输出曲线控件、窗口、三维帧缓冲和出版图截图。自动化颜色与透明度契约由合成数据测试覆盖，不能用截图替代这些数值断言。2026-11-22 起，该脚本输出至 `.local/outputs/opacity-curve-interpolation/`，并增加标题行「平滑／直线」选择器、PCHIP 精确拆分与保存重开、模式往返、同强度垂直阶跃拖动、schema 6 与 Blender schema 13／编辑格式 4 检查；窄面板截图包含完整标题行。

`verify_path_slice.py` 在真实窗口中用鼠标事件完成等能面选点、双击改名、右键建页和 Shift 跳选，检查分段厚度、标注开关、时间切帧、窄窗口滚动与深浅主题，并输出主窗口截图、完整路径出版图、数值 NPZ 和 `report.json`。

`verify_axis_assignment.py` 默认用 WSe₂ 复核时间轴兼容性，再生成降序非均匀温度扫描，通过数据源卡片交换等长 Kx/Ky、修改扫描名称，实际生成强度曲线、出版 PNG 和数值 NPZ；输出窗口／对话框截图及 `report.json`。缺少 WSe₂ 时可显式使用 `--synthetic-only` 只验收温度流程，报告会记录 `wse2_executed: false`，不能据此宣称时间真实数据验收已通过。

`verify_canvas_axis_slices.py` 从 3D 画布新建 XY 切片，核对四分位端点、零长度锁定、开锁动画、重复建页与 3D 返回，截取深浅主题、窄窗口、悬浮坐标框和动画帧，并输出 `report.json`。

`verify_canvas_rotation_dial.py` 在真实窗口中驱动画布图标开关、负小数输入、跨零拖动、单次精确提交和归零，检查宽窗等分、窄窗两行、低高度工具条、系统缩放及路径选段条独立显隐；输出主窗口、原生旋钮弹层和桌面区域截图及 `report.json`。默认数据为 WSe₂，也可用其他含时间轴的 NPZ 做布局与交互验收。

`verify_integration_context_menu.py` 使用真实 `QMenu` 验证「视图右键 → 处理分析」，覆盖四个内置动作与 EDC 插件动作、非法父页和深浅主题，并检查页面树入口与 `Ctrl+2` 已消失。脚本在右键前激活目标父页，再操作 2D／3D 视图的真实菜单；真实桌面验收必须实际从菜单创建结果，核对父页、数值／冻结参数、创建后选中和插件面板，并另用静态数据确认切片内强度积分项不显示；不能用直接调用 `_on_page_create_requested()` 的集成测试代替这项菜单与视觉验收。

`verify_plugin_management.py` 用真实构建的插件包驱动「设置 → 应用管理 → 插件管理／更新设置」内嵌页，检查选中二级页直接显示、三页签与原有操作、更新设置保存／取消／检查更新控件及滚动可达性，以及深浅主题和窄窗布局；截图对象为主设置窗口，不再按独立插件管理或更新设置窗口验收。另加 `--volume` 单独用默认 WSe₂ 验证真实三维标准／精细采样距离（均需要 OpenGL 与桌面环境）；`verify_plugin_release_flow.py` 不需要窗口，用临时测试密钥把「打包 → 签名 → 生成目录 → 验签 → 官方来源安装」整条链路跑一遍，不联网也不发布任何内容。冻结包链路另用 `scripts/validation/verify_frozen_plugin_flow.py <可执行文件>`；Blender 冻结验收覆盖导出、回读、显示编辑与原位更新，并检查五种 Matplotlib／DejaVu 字体资源随包可用、十种文本已打包且保持独立。`blender_dialog_acceptance.py --multi` 重复加载同一文件并跨两个数据源导出，同时检查统一薄层预设、默认开启的十种文本预设、材质与布局设置区移除及八块默认橙色薄板的实际产物。`blender_recipe_acceptance.py` 使用默认 WSe₂ 数据和真实 Blender 进程检查同源两页配套导出、显示覆写原位更新、独立配置加载、强制选源次数、逐页数值与导出草稿恢复；报告和截图输出到 `.local/outputs/blender-recipe-acceptance/`。`blender_project_roundtrip.py` 检查多视图只生成一组共享预设与文本、后续纯数据视图沿 +X 无重叠排列、关闭开关后的文本／字体／灯组缺席、目录搬移、Append 及原位更新后的文本保护。`blender_volume_lighting.py` 对比自发光与柔和受光，检查实际体积系数连接、三灯链接、开关灯、移动灯、多页、Append、手调后原位更新及 WSe₂ 128／256 采样图像和耗时。`blender_material_acceptance.py` 逐个重开并渲染八种薄板材质，另覆盖统一预设、混合材质、两种内部验收布局、数据漫透射采样与纯数据照明、透射滤色、多页独立灯光和 Append 后灯光链接；它和冻结包、人工视觉复核分开记录。`verify_derived_waterfall.py` 会把真实构建的 `edc_waterfall` 插件装进隔离扩展根，再用真实窗口验证视图右键的创建动作、冻结空间来源、逐页时间区间、采样复算、E 翻转前后屏幕/出版图/数值导出一致以及「插件设置」的两个操作。

使用 `--output-dir <目录>` 指定输出。冒烟脚本另支持 `--size 1280x800`、`--hold`。数据探查和指定控件截图分别使用 `scripts/diagnostics/probe_frame_ranges.py` 与 `scripts/diagnostics/_verify_settled_grab.py`，同样传入 NPZ 路径。

自动化测试验证契约与行为；截图是否适合科研出图仍需人工复核。正式验收结论要写明数据来源、运行环境、检查范围与未执行项目。
