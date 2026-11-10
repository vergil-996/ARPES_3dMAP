# 出版图矢量输出、标题拖动与刻度数量

日期：2026-11-10。状态：源码与自动化通过，已随 **v1.19.0** 发布到 GitHub（2026-11-10）；维护者人工视觉、Illustrator 实机转换与发行安装器验收未执行。

## 目标与范围

- 图片导出在 PNG／PDF 之外新增 SVG 与 Adobe Illustrator（`.ai`）；PDF 从 1D／2D 扩展到 3D。3D 的 PDF／SVG 是混合输出：体渲染主体为栅格，文字、坐标与色条保留矢量。PNG／PDF／SVG 支持透明背景。
- 大图预览可直接拖动图标题、各轴标题，与既有色条、三轴方向标共用同一套拖动命中与图层；松手后重新生成完整预览。手动位置写入当前样式的覆盖参数（`title_cx/cy`、`xlabel_cx/cy`、`ylabel_cx/cy`、`zlabel_cx/cy`），随样式保存并用于正式导出；拖出画布会被收回可见区；修改标题排版会清除标题的手动位置。
- 「微调」新增 2–8 的主刻度数量（`axis_nticks`），2D／3D 的刻度、网格与刻度标签一起调整；1D 在「坐标」页单独设置的轴刻度数量优先。
- `.ai` 由本机 Illustrator 转换 PDF 生成：先用 PowerShell COM 打开 PDF 并另存为原生 `.ai`；未检测到 Illustrator 时该项禁用并回退 PNG。

## 相关文件

- `bandscope/exporting/publication_models.py`：`OUTPUT_FORMATS`、`DRAGGABLE_TEXT_IDS`、文字位置与 `axis_nticks` 的覆盖 schema。
- `bandscope/exporting/publication_renderers.py`：`save_figure` 的 PDF／SVG／AI 分支与原子写入、`apply_manual_annotation_positions`、3D 刻度与网格联动、可拖动文字图层分离。
- `bandscope/exporting/publication_dialog.py`：格式下拉与 AI 可用性、拖动信号与预览图层、刻度控件、导出过滤器。
- `bandscope/exporting/orientation_preview.py`：标题／轴名图层的命中、拖动与独立信号。
- `bandscope/exporting/illustrator_export.py`：Illustrator 检测与 PDF→AI 转换。
- 测试：`tests/exporting/test_illustrator_export.py`、`test_publication_annotation_drag.py`、`test_publication_output_options_ui.py`、`test_publication_ticks.py`、`test_orientation_preview.py`。

## 验证

- 全量 `python -m unittest discover -s tests -t .`：**1598 项通过**（2026-11-10，86.917 秒），日志 `.local/logs/release-1.19.0-full.log`。
- 出版图自动化覆盖：四种格式可选与持久化、AI 缺失时禁用并回退 PNG、3D 的 PDF／SVG、标题与轴名拖动图层及画布边界、2D／1D／3D 刻度数量与 1D 单独设置优先；`export_acceptance.py` 的格式说明同步更新。
- Release CI（run 42771656733）通过；GitHub Release v1.19.0 的 `.sha256` 边车与 Release 元数据摘要一致（CPU／NVIDIA）。安装包完整下载校验未执行，见下节。

## 未执行 / 下一步

- 维护者人工视觉：SVG／PDF 在 Illustrator 中的文字可编辑性、矢量观感，以及拖动位置与导出一致性。
- `.ai` 的真实转换未执行（需要本机安装 Illustrator）；无 Illustrator 时的禁用与回退只由自动化覆盖。
- 冻结安装包与安装器验收。
- 安装包完整下载校验：验证时经本机代理的吞吐降到约 35 KB/s（CPU 仅到 28 MB），未完成下载，不写成已通过。
