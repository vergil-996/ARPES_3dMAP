# E 轴翻转验收

本记录负责 E 轴翻转的语义、实现与验收。**2026-10-21 起翻转语义改为「显示镜像」**：
图像与 E 轴刻度一起反向，数据数组、坐标数组、选区、数值导出与插件输入保持原始排列。
屏幕上的像素始终对应它自己的 E 读数，翻转只改变它出现在画面的哪一端。

## 当前语义（2026-10-21）

- 数据不动：不重排图像像素以外的任何数组，不反序 `coords["E"]`、`energy_axis`、
  `curves` 或 `y_data`。
- 映射不动：`data[i, j, k] ↔ (X[i], Y[j], E[k])` 在翻转前后逐位相同；翻转只改变
  显示方向，因此同一 E 坐标始终对应同一份强度。
- E 轴跟着图走：2D 的 E 刻度、1D/比较页的 E 轴、瀑布图的能量纵轴、3D 的坐标轴标注
  都与图像一起镜像。
- 3D 由相机转 180° 完成整场景镜像（体素与坐标都不动）；2D 在渲染出口把像素与
  E 轴一起反向；1D 与瀑布只反向显示轴。翻转只在渲染/出版出口应用一次。

## 改动范围

- `bandscope/rendering/render_core.py`：2D 渲染在翻转时同时反向像素与 E extent。
- `bandscope/rendering/local_enhancement.py`：区域叠加层使用与底图相同的显示方向。
- `bandscope/app/refactored_app.py`：`_render_context_for_visual_flip` 只做标记；
  1D/比较页反向 x 轴、瀑布反向 y 轴；3D 不再反序坐标；区间控件与切片位置按原始
  坐标换算。
- `bandscope/core/crop_model.py`、`bandscope/core/local_enhancement.py`、
  `bandscope/app/crop_integration.py`：删除按显示方向镜像下标/坐标的补偿层。
- `bandscope/exporting/publication_export.py`、`publication_renderers.py`、
  `curve_renderer.py`：出版图与屏幕同向（2D 的 extent、1D/瀑布的轴方向）。
- `bandscope/extensions/plugin_host.py`：插件拿到的能量轴始终是原始排列，倍率不再
  按显示方向倒序。

## 已验证

- 渲染层：翻转前后「像素所在行的数据坐标 == 该行像素值」的最大误差都是 0.04（半格
  的既有采样表示约定），翻转不改变映射，只改变轴方向；`.local/outputs/e_flip_mapping_check.py`。
- 出版图：2D 出版图翻转后 E extent 降序且像素-坐标对应不变；1D 出版图 x 轴反向、
  峰位 E 不变；图片见 `.local/outputs/pub_2d_flip_*.png`、`pub_1d_flip_*.png`。
  检查脚本 `.local/outputs/e_flip_publication_check.py`。
- 真实窗口（`.local/data/WSe2_step.npz`，隔离设置，Windows 原生 Qt/VTK）：
  `scripts/validation/verify_e_axis_consistency.py` **12 项全通过**（3D 快照坐标、
  翻转后的裁剪/裁空体素、真实 ROI 范围、插件 ROI、2D 图片快照与插件方向、
  Energy DOS 导出与画面）。截图在 `.local/outputs/verify_e_axis_consistency/`，
  已人工查看 3D 场景、2D 翻转页与 Energy DOS 页：E 轴与图像同向镜像。
- 全量回归：1448 项通过（`.local/logs/full_suite2.log`）。

## 尚未执行 / 下一步

- 维护者人工确认各视图翻转观感（尤其 3D 场景与原坐标轴同开、瀑布页、比较页），
  以及出版图逐张查看。
- 冻结包/安装器下的同一验收。
- 若确认无误，本记录可归档；若有观感偏差，记在此处再改。

## 迁移提示

翻转前保存的页面/复现文件里，`axis_interval` 记录的是当时的显示空间端点
（`_coords_for_axis` 曾按翻转倒序坐标）。现在区间一律按原始坐标解释：旧记录若在
翻转打开时保存，恢复后 E 区间可能落在另一侧，需要重新设置一次；新保存的状态不受
影响。`display_e_flip` 字段已不再写入也不再读取。

## 历史快照（2026-10-12，语义变更前）

共 12 项数值检查：8 项通过、4 项失败。未通过项为：3D 图片导出 E 方向相反、
插件 ROI 范围错位、二维分析插件数据方向不一致、Energy DOS 数值导出与画面不一致。
这些问题的根因是当时把翻转实现成“数据换向”，本次语义变更后不再适用；证据保留在
`.local/outputs/verify_e_axis_consistency/` 与旧日志
`.local/outputs/e-axis-acceptance-regression.log`、`e-axis-acceptance-full-tests.log`。
