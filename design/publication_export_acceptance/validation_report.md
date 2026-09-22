# 图片导出 · 验收报告

- 日期：2026-09-22 16:18
- 数据：`smoke_data\NiHITP_calibrated_2.npz`（shape=(200, 200, 500, 1)）
- 环境：Python 3.12.10 · matplotlib 3.10.8 · pyvista 0.47.1 · vtk 9.6.0 · Qt 5.15.2

## 检查项

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 3D 样式 3d_minimal | PASS | 1.18s · 395 KB |
| 3D 样式 3d_boxed | PASS | 1.14s · 398 KB |
| 3D 样式 3d_horizontal | PASS | 1.17s · 400 KB |
| 状态隔离 · 3D 三样式导出 | PASS |  |
| 2D 样式 2d_boxed | PASS | 0.53s · 1351 KB |
| 2D 样式 2d_topbar | PASS | 0.50s · 1206 KB |
| 2D 样式 2d_open | PASS | 0.53s · 1345 KB |
| 2D PDF | PASS | 0.63s · 640 KB |
| 状态隔离 · 2D 三样式 + PDF 导出 | PASS |  |
| axis_integral 页导出 | PASS | 0.57s · 1071 KB |
| 1D 样式 1d_open | PASS | 0.08s · 116 KB |
| 1D 样式 1d_boxed | PASS | 0.08s · 116 KB |
| 1D 样式 1d_compact | PASS | 0.09s · 117 KB |
| 1D PDF | PASS | 0.07s · 12 KB |
| 状态隔离 · 1D 三样式 + PDF 导出 | PASS |  |
| waterfall_edc 页导出 | PASS | 0.16s · 239 KB |
| edc_curve 页导出 | PASS | 0.12s · 104 KB |
| 比较页导出 | PASS | 0.08s · 152 KB |
| log_curve 页导出 | PASS | 0.08s · 129 KB |
| 样式面板打开 | PASS | 快照族=3d |
| 样式卡片生成 | PASS | 卡片数=3 大图就绪=True |
| 草稿语义 · 切换不提交 | PASS | draft=3d_minimal committed=3d_boxed |
| 取消不提交 | PASS |  |
| 使用此样式提交 | PASS | committed=3d_minimal |
| 截图按钮 tooltip 刷新 | PASS | 截图：3D · 极简 (Ctrl+S) |
| PNG 元数据 3d_3d_minimal.png | PASS | 2102x2007px dpi=600 期望 2102x2008 |
| PNG 元数据 3d_3d_boxed.png | PASS | 2102x2007px dpi=600 期望 2102x2008 |
| PNG 元数据 3d_3d_horizontal.png | PASS | 2102x2007px dpi=600 期望 2102x2008 |
| PNG 元数据 2d_2d_boxed.png | PASS | 2102x1771px dpi=600 期望 2102x1772 |
| PNG 元数据 2d_2d_topbar.png | PASS | 2102x1771px dpi=600 期望 2102x1772 |
| PNG 元数据 2d_2d_open.png | PASS | 2102x1771px dpi=600 期望 2102x1772 |
| PNG 元数据 1d_1d_open.png | PASS | 2102x1535px dpi=600 期望 2102x1535 |
| PNG 元数据 1d_1d_boxed.png | PASS | 2102x1535px dpi=600 期望 2102x1535 |
| PNG 元数据 1d_1d_compact.png | PASS | 2102x1535px dpi=600 期望 2102x1535 |
| PDF 校验 2d_2d_boxed.pdf | PASS | MediaBox 前缀 0 0 252.2 |
| PDF 校验 1d_1d_open.pdf | PASS | MediaBox 前缀 0 0 252.2 |
| 同数据对照拼图 3d | PASS | design\publication_export_acceptance\mosaic_3d.png |
| 同数据对照拼图 2d | PASS | design\publication_export_acceptance\mosaic_2d.png |
| 同数据对照拼图 1d | PASS | design\publication_export_acceptance\mosaic_1d.png |

## 导出耗时

| 产物 | 秒 |
| --- | --- |
| 3d_3d_minimal.png | 1.18 |
| 3d_3d_boxed.png | 1.14 |
| 3d_3d_horizontal.png | 1.17 |
| 2d_2d_boxed.png | 0.53 |
| 2d_2d_topbar.png | 0.50 |
| 2d_2d_open.png | 0.53 |
| 2d_2d_boxed.pdf | 0.63 |
| page_axis_integral.png | 0.57 |
| 1d_1d_open.png | 0.08 |
| 1d_1d_boxed.png | 0.08 |
| 1d_1d_compact.png | 0.09 |
| 1d_1d_open.pdf | 0.07 |
| page_waterfall.png | 0.16 |
| page_edc.png | 0.12 |
| page_comparison.png | 0.08 |
| page_log.png | 0.08 |

**汇总：39 通过 / 0 失败 / 0 未测**

## 已知限制

- 非均匀坐标网格：2D/3D 明确报不支持（1D 曲线按真实值绘制不受影响）。
- 2D 导出继承当前正式显示的 spline16 插值。
- 3D 首版仅 PNG；PDF 对 3D 禁用（TIFF/混合 PDF 属后续增强）。
- 轴标签单位仅在数据文件显式提供时标注，否则只写物理量名（不编造单位）。
- 瀑布图动量标签带逐曲线标注（与主视图一致）：k 步长过密时标签会互相压盖，请调大步长（本验收样例用 0.1）。