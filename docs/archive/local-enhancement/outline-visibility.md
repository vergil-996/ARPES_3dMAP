# 增强框显示选择

2026-10-13，状态：源码及验收完成。负责范围：`bandscope/app/enhancement_integration.py` 的 2D/3D 辅助框绘制、拾取和右键菜单；集成测试、窗口验收及记录。保留工作区现有裁剪和跨视图增强修改。

## 行为与状态

- 左侧默认只显示下拉框选中区域的框线、标签和编辑手柄；选择背景时隐藏全部增强框。
- 左侧右键菜单增加可勾选的「显示全部增强区域」。勾选后显示当前视图中所有增强框，仍仅选中区域可编辑；取消后恢复仅显示当前框。
- `show_all_regions` 保存在页面自身增强状态中，切页保持各自选择。没有可显示区域时菜单动作停用。
- 隐藏的框不参与鼠标拾取。框线显示与色阶/不透明度渠道独立，开关直接重绘辅助框并复用缓存的增强渠道，无需重新计算区域统计。
- 本次调整左侧交互辅助框；科学数组、区域增强效果和既有出版图标记规则保持。

## 验证

```powershell
.venv/Scripts/python.exe -m unittest tests.integration.test_local_enhancement_controls -v
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v
.venv/Scripts/python.exe scripts/validation/verify_enhancement_outline_visibility.py
```

- 定向集成 **17 项通过**；新增 3 项覆盖真实 Matplotlib 框/拾取、3D actor 与编辑 widget 生命周期、Qt 可勾选菜单、页面隔离及显示渠道缓存复用。
- 最终全量 **1208 项通过**，退出码 0。首次全量发现旧曲线菜单测试替身缺少可勾选 QAction 接口，补齐替身并保留原断言后复跑通过。
- WSe₂ 真实窗口 **23 项检查通过**，退出码 0。数据为 `.local/data/WSe2_step.npz`，Qt 原生 Windows、本机 NVIDIA GPU。检查 3D→2D、下拉框切换、显示全部、选择背景、隐藏所有框、选中编辑手柄及框线变化后颜色/不透明度和科学样本不变。
- 已复核 2D 当前/全部框窗口图和 3D 当前框场景图。完整输出位于 `.local/outputs/enhancement_outline_visibility/`；日志在 `.local/logs/enhancement_outline_regression.log` 与 `enhancement_outline_gui.log`。
- 附件：`outline_acceptance.json`、`outline_2d_selected.png`、`outline_2d_all.png`、`outline_3d_selected.png`。
- 编译和 `git diff --check` 通过。未执行发行包/安装器重建、人工 3D 拖拽手感验收。

下一步：源码功能可直接使用，发行时补做打包及人工手感检查。
