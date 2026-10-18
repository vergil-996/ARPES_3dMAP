# ARPES 弱信号色阶映射修复

日期：2026-10-17。状态：源码修复完成；自动化及真实窗口脚本通过，待用户弱带视觉确认与发行包验收。

## 目标与责任范围

修复全局灰场拉到最高仍无法显示弱信号、局部归一化却可见的问题，覆盖 2D、3D、局部区域与出版导出。
处理与显示通道负责变换，原始谱强度、科学数组和导出快照中的科学数据保留原值。
主窗口、插件公开接口、依赖及打包配置未增加新接口。

## 当前实现

- `bandscope/processing/display_transfer.py`：黑白场选择输入强度窗口，窗口映射到完整色带；
  Gamma 在查色带和基础透明度曲线之前求值。移除 2D 的前置 256 档量化。
- `bandscope/rendering/color_mapping.py`：解析计算 Gamma 的 Matplotlib colormap，主图、预览、区域及导出共用。
- `bandscope/rendering/render_core.py`、`local_enhancement.py`：非线性 3D 色阶将最终颜色坐标和 alpha
  写入独立显示通道，避免 GPU 在原强度轴重采样 Gamma 曲线。线性色阶保留原标量路径；
  插件能量倍率乘在最终基础 alpha 上。区域同样直接使用最终颜色坐标，不再编码区域偏移。
- 2D／出版色条使用 Gamma norm；3D 色条按颜色坐标展开，并标注对应的黑、灰、白物理强度，
  避免极端 Gamma 时中灰刻度挤在端点或显示成错误颜色。
- `bandscope/exporting/publication_renderers.py`：全局非线性体渲染导出复用显示通道。
  保留工作区已有的出版图区域标注移除改动。
- `bandscope/ui/page_render_control.py`：补充黑白场与灰场 Gamma 的悬停解释。

## 验证依据

- 合成动态范围 `0..1e6`、灰场 100：原实现将强度 `1..1000` 的 2D 颜色全部量化为零；
  修复后颜色坐标分别遵循 `(I/1e6)^0.1`，例如 `I=1` 对应约 `0.25119`。
  3D 的线性基础 alpha 同样正确提升，不再由原强度轴上的 11 节点折线抑制。
- 新增处理、解析 colormap、主图及导出测试；覆盖黑白窗口、弱谱、NaN、Gamma 色条、
  帧更新、强制上传、插件倍率、线性／非线性／区域路径切换及科学数组不变。
- 全量命令：`.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`。
  1290 项全部通过（Windows、仓库 `.venv` Python 3.12、Matplotlib 3.10.8）；
  日志：`.local/logs/weak-transfer-full-regression.log`。
- 真实数据：`.local/data/WSe2_step.npz`；原生 Windows Qt 与可用 OpenGL。
  `scripts/validation/verify_local_enhancement.py --output-dir .local/outputs/weak-transfer-native`
  覆盖全局 2D／3D 灰场 100、弱样本颜色／透明度、区域控件、删除恢复、锁定及出版导出，共 156 个检查。
  验收清单及截图：`.local/outputs/weak-transfer-native/acceptance.json` 和同目录 PNG；
  日志：`.local/logs/weak-transfer-native.log`。
- 合成谱前后对比图已查看：`.local/outputs/weak-transfer/synthetic-comparison.png`。
  图中明确标注 Synthetic，不作为真实实验谱证据。
- 编译与 `git diff --check` 通过。临时输出均在 `.local/`，不提交。

## 下一步

- 用户针对关心的真实弱带确认可见性、全局／局部对比与拖动手感。
- 对非线性体渲染首次调整的额外显示缓冲与计算开销做发行环境确认。
- CPU／NVIDIA 冻结包和安装器验收尚未执行；本次未提交、打标签或发布。
- 黑白场交叉仍沿用原来的正向窗口保护；本次未将交叉定义为反色功能。

此记录是本事项状态主记录，后续结果在这里更新。
