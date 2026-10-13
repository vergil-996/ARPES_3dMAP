# 3D 局部增强在 2D 结果中的继承

2026-10-13，状态：源码修复及验收完成。负责范围：增强区域几何模型、主窗口结果页接入、对应测试与真实窗口验收。

## 原因与修复

2D 积分页没有复制来源的增强状态；裁剪降维页面虽复制状态，仍被同维度匹配过滤。此前 27 项真实窗口检查在删除全部 3D 区域之后才创建 2D，未覆盖这条跨视图路径。

- 新建 2D/3D 科学结果页深复制来源显示设置，名称、色阶、不透明度模式、启用状态及共用模式保持。1D 结果不继承增强框。
- 与切片或积分区间相交的 3D 盒子投影到剩余两轴。保留完整盒子范围以支持切片/轴改变；不相交的盒子暂时不显示，相交后可恢复。
- 2D 归一化始终使用原始 2D 结果。积分仍对科学数组求和；色阶决定色带取值，强度映射只决定不透明度。
- 继承的锁定设置在新结果首次精确计算时重新建立范围，不使用 3D 原始强度范围或预览统计。
- 2D 编辑更新投影坐标并保留盒子的深度。编辑/删除只影响当前页；每次删除的样本并入背景重新归一化，最后一个删除后下拉框消失。

接口与状态归属：`core/local_enhancement.py` 管理投影与绝对坐标；`app/enhancement_integration.py` 管理页面状态复制、锁定及交互；共享入口 `app/refactored_app.py` 在结果页初始化继承状态，并在精确渲染时建立锁定范围。保留工作区其他裁剪与增强成果。

## 验收

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v
.venv/Scripts/python.exe scripts/validation/verify_enhancement_projection.py
```

- 最终全量 **1205 项通过**，退出码 0；本修复新增 9 项测试。覆盖三个轴、区间/单层相交与恢复、绝对裁剪坐标、投影编辑后换轴/返回 3D、真实结果构造入口、来源隔离、独立/共用锁定范围及预览不锁定。
- 真实窗口 **22 项通过**，退出码 0。数据为 `.local/data/WSe2_step.npz`（150×150×200×25，E 索引坐标），Qt 原生 Windows、本机 NVIDIA GPU。
- 窗口覆盖：在 3D 创建两个区域后直接应用 Y/X/E 方向 2D 积分；下拉框与可编辑矩形、独立设置、原始二维统计、E 翻转、出版图、编辑不回写来源、删除后背景归一化、最后删除隐藏面板、返回 3D 恢复区域及科学数据不变。
- 窗口及出版 PNG 已复核。完整输出：`.local/outputs/enhancement_projection/`；日志：`.local/logs/enhancement_projection_regression.log` 与 `enhancement_projection_gui.log`。
- 本目录附件：`projection_acceptance.json`、`projection_2d_window.png`、`projection_2d_publication.png`。
- 编译检查与 `git diff --check` 通过。初次用了缺少依赖的系统 Python，改用项目 `.venv`；测试在正常临时目录权限和隔离配置下复核。
- 未执行：发行包/安装器重建及人工 3D 拖拽手感；单层/裁剪降维由合成测试覆盖，本次真实窗口验证的是三个方向的积分。

下一步：源码功能已可使用，发行时补做打包与人工手感验收。
