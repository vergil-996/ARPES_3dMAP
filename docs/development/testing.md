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
python -m compileall -q bandscope plugins scripts tests start.py
```

测试全部纳入 Git；默认 Qt 离屏运行、临时扩展目录和临时 INI 设置，不依赖 `.local/data/`。时间控件测试在临时目录生成含非均匀时间坐标的 NPZ。共享导出快照构造器位于 `tests/support/publication.py`。

测试需要 NumPy、Qt、VTK 等运行依赖，但不要求 CUDA。加载 NPZ 时会在后台线程计算文件摘要，Windows 上紧接着删除临时目录可能因文件仍被打开而报 WinError 32；`tests/__init__.py` 对**清理动作**做 5 秒有界重试，断言本身不变。Windows 沙箱如果导致 `TemporaryDirectory` 新建后不可读写，应在具备正常临时目录权限的环境复核，并保留错误日志；不要通过删除断言绕过。

## 视觉与真实数据验证

这些脚本读取显式输入文件，默认输出至 `.local/outputs/<检查名>/`。真实数据无需加入 Git。默认隔离用户设置和扩展目录；完整窗口验收默认使用本机图形平台，需要桌面环境。Windows 上 VTK 嵌入窗口不能使用 Qt 的 `offscreen` 平台；若此前设置过该变量，运行前设为 `windows`。纯自动化回归仍默认离屏。

```powershell
python scripts/validation/smoke_test.py .local/data/scan07_dynamic.npz --dynamic
python scripts/validation/verify_color_lock.py .local/data/scan07_dynamic.npz
python scripts/validation/verify_2d_aspect.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_camera_view_panel.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_time_axis_visibility.py .local/data/scan07_dynamic.npz .local/data/NiHITP_calibrated_2.npz
python scripts/validation/export_acceptance.py .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_flat_band_alpha.py --bands 5
python scripts/validation/volume_display_quality.py .local/data/WSe2_step.npz D:/ARPES数据/npz_files/西湖/Au_cut.npz D:/ARPES数据/npz_files/西湖/NiHITP_cut.npz --blender D:/blender/blender.exe
python scripts/validation/verify_view_recipe.py
python scripts/validation/verify_multi_source_workspaces.py
python scripts/validation/verify_plugin_management.py
python scripts/validation/verify_plugin_release_flow.py
python scripts/validation/verify_derived_waterfall.py .local/data/<数据>.npz
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

`verify_plugin_management.py` 用真实构建的插件包驱动完整窗口（需要 OpenGL 与桌面环境）；`verify_plugin_release_flow.py` 不需要窗口，用临时测试密钥把「打包 → 签名 → 生成目录 → 验签 → 官方来源安装」整条链路跑一遍，不联网也不发布任何内容。冻结包链路另用 `scripts/validation/verify_frozen_plugin_flow.py <可执行文件>`；Blender 冻结验收覆盖导出、回读、显示编辑与原位更新，并检查五种 Matplotlib／DejaVu 字体资源随包可用、十种文本已打包且保持独立。`blender_dialog_acceptance.py --multi` 重复加载同一文件并跨两个数据源导出，同时检查统一薄层预设、默认开启的十种文本预设、材质与布局设置区移除及八块默认橙色薄板的实际产物。`blender_recipe_acceptance.py` 使用默认 WSe₂ 数据和真实 Blender 进程检查同源两页配套导出、显示覆写原位更新、独立配置加载、强制选源次数、逐页数值与导出草稿恢复；报告和截图输出到 `.local/outputs/blender-recipe-acceptance/`。`blender_project_roundtrip.py` 检查两组独立文本、关闭开关后的文本／字体／灯组缺席、目录搬移、Append 及原位更新后的文本保护。`blender_volume_lighting.py` 对比自发光与柔和受光，检查实际体积系数连接、三灯链接、开关灯、移动灯、多页、Append、手调后原位更新及 WSe₂ 128／256 采样图像和耗时。`blender_material_acceptance.py` 逐个重开并渲染八种薄板材质，另覆盖统一预设、混合材质、两种内部验收布局、数据漫透射采样与纯数据照明、透射滤色、多页独立灯光和 Append 后灯光链接；它和冻结包、人工视觉复核分开记录。`verify_derived_waterfall.py` 会把真实构建的 `edc_waterfall` 插件装进隔离扩展根，再用真实窗口验证下拉动作、冻结来源、采样复算、E 翻转前后屏幕/出版图/数值导出一致以及「插件设置」的两个操作。

使用 `--output-dir <目录>` 指定输出。冒烟脚本另支持 `--size 1280x800`、`--hold`。数据探查和指定控件截图分别使用 `scripts/diagnostics/probe_frame_ranges.py` 与 `scripts/diagnostics/_verify_settled_grab.py`，同样传入 NPZ 路径。

自动化测试验证契约与行为；截图是否适合科研出图仍需人工复核。正式验收结论要写明数据来源、运行环境、检查范围与未执行项目。
