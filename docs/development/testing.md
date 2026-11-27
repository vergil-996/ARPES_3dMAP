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
python -m unittest tests.processing.test_momentum_calibration tests.core.test_momentum_calibration tests.ui.test_momentum_calibration_dialog tests.exporting.test_calibrated_source tests.integration.test_momentum_calibration tests.integration.test_axis_assignment -v
python -m unittest tests.processing.test_energy_calibration tests.ui.test_energy_calibration_dialog tests.integration.test_energy_calibration -v
python -m unittest tests.integration.test_edc_integration tests.integration.test_multi_region_interaction tests.processing.test_edc -v
python -m compileall -q bandscope plugins scripts tests start.py
```

测试全部纳入 Git；默认 Qt 离屏运行、临时扩展目录和临时 INI 设置，不依赖 `.local/data/`。时间控件测试在临时目录生成含非均匀时间坐标的 NPZ；轴分配测试另用小型合成 NPZ/MAT 覆盖时间、角度、温度、裸 `T`、缺失坐标、等长轴、降序非均匀坐标、静态／扫描三维、重设事务和两种分配的复现往返。共享导出快照构造器位于 `tests/support/publication.py`。

结果创建集成测试若不以菜单委托为验收目标，应直接调用主窗口 `_on_page_create_requested(parent_page_id, action_key)` 并检查 `ResultWorkspace` 中的新页、父子关系和冻结参数，不再构造已删除的「处理分析」页或旧下拉框。页面树已移除创建入口，动作过滤与视图右键菜单委托由 `tests/integration/test_integration_context_menu.py` 覆盖，`tests/ui/test_page_tree.py` 只断言页面树不再出现创建子菜单。入口迁移的聚焦回归可运行：

```powershell
python -m unittest tests.ui.test_page_tree tests.ui.test_control_page_state tests.integration.test_integration_context_menu tests.integration.test_plugin_derived_flow tests.integration.test_plugin_analysis_flow tests.extensions.test_plugin_analysis tests.extensions.test_derived_host tests.integration.test_crop_integration tests.integration.test_axis_interval_pages tests.integration.test_analysis_control_refresh tests.integration.test_second_derivative tests.integration.test_view_recipe_roundtrip -v
```

测试需要 NumPy、Qt、VTK 等运行依赖，但不要求 CUDA。加载 NPZ 时会在后台线程计算文件摘要，Windows 上紧接着删除临时目录可能因文件仍被打开而报 WinError 32；`tests/__init__.py` 对**清理动作**做 5 秒有界重试，断言本身不变。Windows 沙箱如果导致 `TemporaryDirectory` 新建后不可读写，应在具备正常临时目录权限的环境复核，并保留错误日志；不要通过删除断言绕过。

### 实时旋转与空间工具共存定向回归

```powershell
python -m unittest tests.ui.test_rotation_dial tests.ui.test_canvas_overlay tests.integration.test_rotation_preview tests.integration.test_canvas_axis_slices tests.integration.test_multi_region_interaction tests.integration.test_local_enhancement_controls tests.integration.test_path_slice tests.core.test_rotation_crop tests.integration.test_rotated_crop tests.integration.test_rotated_edc tests.integration.test_rotated_derived_pages tests.integration.test_view_recipe_roundtrip -v
```

合成数据必须分别核对 XY／XZ／YZ 的多个当前角度与独立参考数组，覆盖 `axis_integral_crop`、切页恢复和导出一致性；法向区间和坐标不变，预览实际更新且不污染最终精确结果。交互回归覆盖两种面板打开顺序、普通外部点击不误关、Z／`Esc`／切页关闭和面板避让，并确认 2D 框、3D 盒、局部增强框与路径节点在旋转前后保持原几何和活动状态。二阶导、EDC、路径结果及插件派生页仍冻结来源；已提交裁剪／裁空历史的跨角度重放测试不得改成实时显示语义。当前实施状态与实窗验收清单见 [2D 切片实时旋转与空间交互工具共存](../plans/live-slice-rotation-interactions.md)。

## 视觉与真实数据验证

这些脚本读取显式输入文件，默认输出至 `.local/outputs/<检查名>/`。真实数据无需加入 Git。默认隔离用户设置和扩展目录；完整窗口验收默认使用本机图形平台，需要桌面环境。Windows 上 VTK 嵌入窗口不能使用 Qt 的 `offscreen` 平台；若此前设置过该变量，运行前设为 `windows`。纯自动化回归仍默认离屏。

```powershell
python scripts/validation/smoke_test.py .local/data/scan07_dynamic.npz --dynamic
python scripts/validation/verify_color_lock.py .local/data/scan07_dynamic.npz
python scripts/validation/verify_2d_aspect.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_camera_view_panel.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_time_axis_visibility.py .local/data/scan07_dynamic.npz .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_axis_assignment.py .local/data/WSe2_step.npz
python scripts/validation/verify_momentum_calibration.py .local/data/WSe2_step.npz
python scripts/validation/verify_momentum_calibration.py --synthetic-only
python scripts/validation/verify_energy_calibration.py .local/data/WSe2_step.npz
python scripts/validation/verify_energy_calibration.py --synthetic-only
python scripts/validation/verify_multi_region_edc.py .local/data/WSe2_step.npz
python scripts/validation/verify_rotated_crop_analysis.py
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

`verify_levels_and_colormap_range.py` 默认使用 WSe₂ 数据，驱动累计直方图、黑灰白输入色阶与双端输出色带，检查连续拖动缓存复用、切帧、局部区域、全部显示、二维切片、窄面板和深浅主题；色带切换统一走「输出色带」标题行的新菜单，并核对默认 `magma`、菜单首两项、`coolwarm` 唯一、换色保留输出端点、每次只完成一次精确刷新、重选或取消不刷新、菜单滚动与色样／勾选、标题行色名与渐变条同宽。输出窗口／三维帧缓冲截图、深浅主题色阶卡片、标题行、展开菜单与两组控件截图及 `report.json`。`verify_independent_opacity_curve.py` 在同一真实数据上检查默认三点曲线与旧兼容线性一致、颜色控件不改变 α、下降段与平台的逐体素曲线、拖动曲线点不带动底轨、底轨水平定位与向上加点、黑点脱离外轮廓并连续穿过底轨与横轴间距、拖回底轨重新挂回、碰线前保持空心不回填、原位回填动画、碰线前松手、命中已有点、α＝0 加点、零线编辑与重新打开后保留、右键与 Delete 删除、端点保护、焦点离开后 Delete 无效、深浅主题与窄面板，并核对 CPU／可用 CUDA、出版导出与 Blender 密度的参考距离换算及 schema 4→5 迁移误差；输出曲线控件、窗口、三维帧缓冲和出版图截图。自动化颜色与透明度契约由合成数据测试覆盖，不能用截图替代这些数值断言。2026-11-22 起，该脚本输出至 `.local/outputs/opacity-curve-interpolation/`，并增加标题行扭曲三角函数曲线／三段折线图标动画开关、PCHIP 精确拆分与保存重开、模式往返、同强度垂直阶跃拖动、schema 6 与 Blender schema 13／编辑格式 4 检查；窄面板截图包含完整标题行。

`verify_path_slice.py` 在真实窗口中用鼠标事件完成等能面选点、双击改名、右键建页和 Shift 跳选，检查分段厚度、标注开关、时间切帧、窄窗口滚动与深浅主题，并输出主窗口截图、完整路径出版图、数值 NPZ 和 `report.json`。

`verify_axis_assignment.py` 默认用 WSe₂ 复核时间轴兼容性，再生成降序非均匀温度扫描，通过数据源卡片交换等长 Kx/Ky、修改扫描名称，并检查「重新分配轴」窗口内的扫描轴滑块停靠／整段平移设置随重设生效，实际生成强度曲线、出版 PNG 和数值 NPZ；输出窗口／对话框截图及 `report.json`。缺少 WSe₂ 时可显式使用 `--synthetic-only` 只验收温度流程，报告会记录 `wse2_executed: false`，不能据此宣称时间真实数据验收已通过。

`verify_momentum_calibration.py` 默认用 WSe₂，从数据体卡片进入真实轴分配与两点定标窗口，在 XY 切片上鼠标选点、调整能量切片、输入实际间距并应用；检查能量与扫描控件均为共享 `DualRangeSlider`／`CalibrationSliceSlider` 双端样式、无长度输入框，figure 只有一个 axes 且没有 colorbar；随后通过同一个独立保存按钮依次导出完整 NPZ 与 MAT，NPZ 直接回读，MAT 经 `data_trans.convert` 转换后回读，分别用 `AnalyzerCore` 核对强度与坐标，严格比较轴分配的非坐标字段和扫描信息，并逐项检查 Kx/Ky 定标后坐标键、来源与单位的预期规范化；若有不符，报告列出具体字段的宿主值、加载值或预期值。脚本截取包含切片、选点、距离及两个固定动作按钮的定标窗口与主窗口深浅主题图。输出至 `.local/outputs/momentum-calibration/`，`report.json` 分项记录实际执行与未执行内容；`--synthetic-only` 使用小型非均匀、降序坐标合成数据，不代表真实 WSe₂ 已验收，也不把自动截图记为人工视觉复核通过。

`verify_energy_calibration.py` 默认用 WSe₂，从数据体卡片经轴分配的「能量定标」按钮进入真实窗口，核对默认 XZ 切片数组、法向与扫描共享双端滑条、无长度输入框和无色带，再切换 YZ、垂直翻转并用鼠标拖动两个图外 E1/E2 手柄。输入目标间距与单位后应用，核对 E1 为零、E2 为目标间距、能量坐标均匀、强度不变及宿主 core 同步，随后截取定标窗口和主窗口深浅主题图并关闭嵌套窗口。输出至 `.local/outputs/energy-calibration/`，`report.json` 区分自动执行、未执行的保存回读和截图人工视觉复核；`--synthetic-only` 使用小型非均匀、降序坐标合成数据，不代表真实 WSe₂ 已验收。

`verify_multi_region_edc.py` 使用真实窗口验收画布工具条：「裁剪」「裁空」位于画布初级工具栏，「新增框选范围」「生成 EDC」「切片内强度积分」位于「框选范围」次级栏。脚本点击这些真实入口，检查重复展开后草稿保留、裁剪／裁空只作用活动选区，以及每个 2D 选框与 3D 交互盒都有稳定的 `Z{id}` 标号；随后分别在 2D 手柄和 3D 手柄上右键，核对弹窗对应该盒的各轴上下限，点击移除并确认其他选区不受影响。

EDC 数值验收覆盖 3D 主页、E 轴积分的 kx–ky 页及 XZ／YZ 页：kx–ky 的框选只限定 Kx／Ky，EDC 必须保留完整能量曲线；XZ／YZ 的范围是框选两轴加该页法向双端滑条区间，脚本按完整 X／Y／E 索引区间独立复算曲线。3D 多盒仍核对各自不同的 E 范围与多选区同页比较。切片内强度积分必须通过次级工具按钮创建，范围为**活动框选 + 当前页法向双端滑条区间**，脚本逐扫描帧复算强度。若法向区间初始锁定，先真实点击锁徽章并确认解锁，再分别提交双端，避免锁定状态下的整体平移。视图右键「处理分析」不再含内置 EDC 或切片内强度积分。脚本输出只代表自动操作、数值断言和截图完成；Z 标号可读性、手柄命中、弹窗布局及科研出图质量仍需人工视觉复核。

`verify_rotated_crop_analysis.py` 使用本机窗口、VTK、原生旋钮、范围浮窗、工具按钮和右键菜单，验收 30° 旋转后的裁剪／裁空及冻结来源分析页，覆盖 EDC、切片内强度积分、XY／XZ／YZ、Energy DOS、二阶导、折线路径、回 0° 切帧和 `log10`。这些派生页继续冻结创建来源；主页与 `axis_integral`／`axis_integral_crop` 直接切片按当前角度实时重建是另一套显示契约，已提交裁剪／裁空记录仍按既有创建角度重放。默认读取 `.local/data/WSe2_step.npz`，默认输出 `.local/outputs/rotated-crop-analysis/`，包含 `report.json` 和窗口截图；真实文件缺失时直接失败，不用合成数据替代。严格合成数值回归仍由 `tests/integration/test_rotated_*.py` 负责，实窗脚本不能替代这些回归。自动操作、数值断言和截图完成不等于维护者人工视觉复核，也不代表鼠标手感或科研出图质量已获认可。

`verify_crop_window.py` 真实点击画布初级「裁剪」「裁空」与框选主按钮，检查初级操作不联动勾选框选或展开次级栏、初级／次级父控件和布局归属、浮窗位置记忆与重绘恢复；范围标题应以「裁剪范围」或「裁空范围」开头并包含对应 `Z{id}`，不能再与无标号旧标题做全等比较。`verify_time_axis_visibility.py` 在启动、动态 3D、动态含能量轴 2D、静态及重新加载页面均确认右键「处理分析」无内置切片积分，同时检查合法动态 2D 页的 `slice_dos_button` 可用、静态 2D 页禁用。

`verify_canvas_axis_slices.py` 从 3D 画布新建 XY／XZ／YZ 切片，核对四分位端点、零长度锁定、开锁动画、重复建页与 3D 返回；在多个当前角度下驱动真实旋钮，核对中间预览和最终精确数组相对独立参考正确、法向区间与坐标不变、`axis_integral_crop` 同样实时重建，以及切页恢复、导出和画面一致。截取深浅主题、窄窗口、悬浮坐标框、动画帧和旋转前后对照，并输出 `report.json`。

`verify_canvas_rotation_dial.py` 在真实窗口中驱动画布图标开关、负小数输入、跨零拖动、单次精确提交和归零，检查旋转按钮高亮与弹层显隐同步、宽窗等分、窄窗两行、低高度工具条、系统缩放及路径选段条独立显隐。旋钮按模式less浮窗验收：普通外部点击不关闭，Z、`Esc`、切页／切源和工具条隐藏仍关闭；按两种打开顺序检查它与框选、裁剪／裁空范围浮窗、局部增强和路径选点共存，检查屏幕边界与矩形避让，并核对连续角度序列中 2D 框、3D 裁剪／增强盒和路径节点不动而框内数据变化。输出主窗口、原生旋钮浮窗、空间工具共存截图和桌面区域截图及 `report.json`。默认数据为 WSe₂，也可用其他含时间轴的 NPZ 做布局与交互验收；自动截图和数值断言不记为人工视觉、鼠标流畅度或科研出图质量认可。

`verify_integration_context_menu.py` 使用真实 `QMenu` 验证「视图右键 → 处理分析」，覆盖「能级态密度」「二阶导」两个内置动作与 EDC 插件动作、非法父页和深浅主题，并检查页面树入口与 `Ctrl+2` 已消失。每个菜单都断言不含 `builtin:edc_curve` 和 `builtin:slice_dos`；切片内强度积分另从框选次级工具按钮真实创建，核对活动选区、法向双端区间和逐帧数值，不能再用静态数据检查一个已经移除的菜单项。脚本在右键前激活目标父页，再操作 2D／3D 视图的真实菜单；真实桌面验收必须实际创建结果，核对父页、数值／冻结参数、创建后选中和插件面板，不能用直接调用 `_on_page_create_requested()` 的集成测试代替菜单与视觉验收。

`verify_plugin_management.py` 用真实构建的插件包驱动「设置 → 应用管理 → 插件管理／更新设置」内嵌页，检查选中二级页直接显示、三页签与原有操作、更新设置保存／取消／检查更新控件及滚动可达性，以及深浅主题和窄窗布局；截图对象为主设置窗口，不再按独立插件管理或更新设置窗口验收。另加 `--volume` 单独用默认 WSe₂ 验证真实三维标准／精细采样距离（均需要 OpenGL 与桌面环境）；`verify_plugin_release_flow.py` 不需要窗口，用临时测试密钥把「打包 → 签名 → 生成目录 → 验签 → 官方来源安装」整条链路跑一遍，不联网也不发布任何内容。冻结包链路另用 `scripts/validation/verify_frozen_plugin_flow.py <可执行文件>`；Blender 冻结验收覆盖导出、回读、显示编辑与原位更新，并检查五种 Matplotlib／DejaVu 字体资源随包可用、十种文本已打包且保持独立。`blender_dialog_acceptance.py --multi` 重复加载同一文件并跨两个数据源导出，同时检查统一薄层预设、默认开启的十种文本预设、材质与布局设置区移除及八块默认橙色薄板的实际产物。`blender_recipe_acceptance.py` 使用默认 WSe₂ 数据和真实 Blender 进程检查同源两页配套导出、显示覆写原位更新、独立配置加载、强制选源次数、逐页数值与导出草稿恢复；报告和截图输出到 `.local/outputs/blender-recipe-acceptance/`。`blender_project_roundtrip.py` 检查多视图只生成一组共享预设与文本、后续纯数据视图沿 +X 无重叠排列、关闭开关后的文本／字体／灯组缺席、目录搬移、Append 及原位更新后的文本保护。`blender_volume_lighting.py` 对比自发光与柔和受光，检查实际体积系数连接、三灯链接、开关灯、移动灯、多页、Append、手调后原位更新及 WSe₂ 128／256 采样图像和耗时。`blender_material_acceptance.py` 逐个重开并渲染八种薄板材质，另覆盖统一预设、混合材质、两种内部验收布局、数据漫透射采样与纯数据照明、透射滤色、多页独立灯光和 Append 后灯光链接；它和冻结包、人工视觉复核分开记录。`verify_derived_waterfall.py` 会把真实构建的 `edc_waterfall` 插件装进隔离扩展根，再用真实窗口验证视图右键的创建动作、冻结空间来源、逐页时间区间、采样复算、E 翻转前后屏幕/出版图/数值导出一致以及「插件设置」的两个操作。

使用 `--output-dir <目录>` 指定输出。冒烟脚本另支持 `--size 1280x800`、`--hold`。数据探查和指定控件截图分别使用 `scripts/diagnostics/probe_frame_ranges.py` 与 `scripts/diagnostics/_verify_settled_grab.py`，同样传入 NPZ 路径。

自动化测试验证契约与行为；截图是否适合科研出图仍需人工复核。正式验收结论要写明数据来源、运行环境、检查范围与未执行项目。
