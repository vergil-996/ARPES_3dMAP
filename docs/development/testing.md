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

测试需要 NumPy、Qt、VTK 等运行依赖，但不要求 CUDA。Windows 沙箱如果导致 `TemporaryDirectory` 新建后不可读写，应在具备正常临时目录权限的环境复核，并保留错误日志；不要通过删除断言绕过。

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
python scripts/validation/verify_band_overlay.py
python scripts/validation/verify_band_overlay_wse2.py
python scripts/validation/verify_plugin_management.py
python scripts/validation/verify_plugin_release_flow.py
```

`verify_plugin_management.py` 用真实构建的插件包驱动完整窗口（需要 OpenGL 与桌面环境）；`verify_plugin_release_flow.py` 不需要窗口，用临时测试密钥把「打包 → 签名 → 生成目录 → 验签 → 官方来源安装」整条链路跑一遍，不联网也不发布任何内容。冻结包链路另用 `scripts/validation/verify_frozen_plugin_flow.py <可执行文件>`。

`verify_band_overlay.py` 是**离屏**的：合成体数据 → 插件的工作函数重构 → 真实
`VolumeRenderSession` + 叠加层渲染 → 截图，并给出"能带面是否落在体数据亮脊上"的
数值指标（含真值面对照），不弹窗、不抢焦点。`verify_band_overlay_wse2.py` 是同一
流程的**真实数据**版本：读 `.local/data/WSe2_step.npz`（t≈0 三帧平均、E 轴为索引
坐标、能量窗口默认 [90, 199]），无真值面，指标含 ±1/±2 命中率、局部脊距与带间距，
结论需人工核对截图。

使用 `--output-dir <目录>` 指定输出。冒烟脚本另支持 `--size 1280x800`、`--hold`。数据探查和指定控件截图分别使用 `scripts/diagnostics/probe_frame_ranges.py` 与 `scripts/diagnostics/_verify_settled_grab.py`，同样传入 NPZ 路径。

自动化测试验证契约与行为；截图是否适合科研出图仍需人工复核。正式验收结论要写明数据来源、运行环境、检查范围与未执行项目。
