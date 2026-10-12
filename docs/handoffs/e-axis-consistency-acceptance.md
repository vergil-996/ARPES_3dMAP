# E 翻转一致性验收

2026-10-12。状态：验收完成，裁剪／裁空坐标修复通过；四项扩展路径问题待修复。

## 范围与环境

Windows 原生 Qt/VTK 窗口，仓库 `.venv`，隔离设置和扩展目录，使用
`.local/data/WSe2_step.npz`（150×150×200×25，E 为索引坐标）。
验收脚本为 `scripts/validation/verify_e_axis_consistency.py`，从仓库根运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.validation.verify_e_axis_consistency
```

本次只增加验收脚本与交接记录，保留其他任务已有的应用代码改动。

## 实测结果

共 12 项数值检查：8 项通过、4 项失败，无执行异常。检查了真实场景、导出图片、
弹窗及二维／一维页面截图。Qt 主窗口抓图中 VTK 子窗口为黑色，采用
`plotter.screenshot()` 单独捕获 3D 场景；工具弹窗也单独抓图。

通过：翻转前 3D 快照坐标；翻转后弹窗输入对应绝对 E 下标 40–66；裁剪和
裁空作用体素；生成的真实 ROI 下标；ROI 弹窗 E 范围 133–159；二维图片快照
与画面数组；一维画面与渲染上下文。

未通过：

1. **3D 图片导出**：实时 E 轴为 199→0，图片快照仍为 0→199，导出图中
   E 刻度与实际画面方向相反。位置：`bandscope/exporting/publication_export.py`
   的 `capture_snapshot` / `_coord_meta`。
2. **插件 ROI**：实时与弹窗范围为 133–159，插件范围为 40–66。
   位置：`bandscope/extensions/plugin_host.py` 的 `_energy_axis`。
3. **二维分析插件**：插件数据仍按原始 E 方向，图片快照及实时画面已经翻转，
   数组最大绝对差为 8。位置：同文件的 `capture_analysis_input`。
4. **普通数值导出**：Energy DOS 导出强度与相同 E 坐标下的实时曲线不一致，
   最大绝对差为 92。ROI 导出为 27 点、画面为完整 200 点，比较时已用导出 E
   坐标插值取画面强度，排除了零填充长度差异。位置：
   `bandscope/app/refactored_app.py` 的 `_build_export_payload` 及普通导出构造器。

## 证据与回归

- `.local/outputs/verify_e_axis_consistency/results.json`：逐项结果、形状与差值。
- 同目录 `3d_crop_fields_scene.png`、`3d_crop_fields_popup.png`、
  `3d_erase_fields_popup.png`、`roi_flipped_scene.png`：原修复的视觉证据。
- 同目录 `3d_flipped_export.png`、`2d_flipped.png`、`energy_dos_flipped.png`、
  `curve_arrays.npz`：新增问题的图像及数组证据。
- E 翻转、导出与插件相关回归共 60 项通过，日志
  `.local/outputs/e-axis-acceptance-regression.log`。
- 完整回归 1069 项通过，日志 `.local/outputs/e-axis-acceptance-full-tests.log`。
  当前自动化测试通过不代表上述四项验收通过。

所有本机数据、截图和日志均不提交。未执行冻结安装包或安装器验收。

## 下一步

统一显示与导出、插件输入的 E 坐标对应规则，补充失败场景的自动化回归，
然后重跑本验收脚本。处理插件倍率时还需确认倍率与显示坐标的体素对应，
避免只修 ROI 文案而留下倍率方向不一致。
