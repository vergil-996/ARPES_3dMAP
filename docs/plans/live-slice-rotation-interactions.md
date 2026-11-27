# 2D 切片实时旋转与空间交互工具共存

2026-11-27 · **源码与集成验证已执行；定向及合成数据实窗通过，全量保留4项顺序相关既有UI失败，WSe₂与人工验收待办**。本文件是该事项的状态主记录；事项保持开放，完整验证证据和未执行项见下文。

## 目标

- 主页以及 `axis_integral`／`axis_integral_crop` 直接切片使用页面恢复后的当前旋转角度。先按当前角度重建完整来源体，再从同一法向范围提取或积分；法向 `axis_interval`、上下限、坐标上下限和页面区间保持固定。
- 拖动旋钮期间持续提供合并、过期任务丢弃和自适应节流的预览，松手后得到精确结果。预览缓存不得替代最终精确数组，精确结果完成前继续保护确认裁剪／裁空和需要精确来源的局部增强操作。
- 旋转与框选、裁剪、裁空、局部增强框及折线路径选点共存。打开任一空间工具不再关闭旋钮，打开旋钮也不关闭空间工具；不同空间编辑器之间已有的冲突保护保持不变。
- 可见交互几何不跟随 Z 旋转：2D 矩形保持原轴对齐边界，3D 裁剪／增强盒保持原逻辑和渲染边界，局部增强区域与路径节点保持原范围或坐标；变化的是框内数据。
- 旋钮改为不独占鼠标的模式less浮窗，普通外部点击不再自动关闭。再次点击 Z、按 `Esc`、切页／切源或工具条隐藏时仍关闭；同时可见的旋钮、次级栏和范围浮窗需要屏幕边界与矩形避让。
- 二阶导、EDC、路径结果及插件派生页仍冻结各自创建来源和有效角度；从直接切片创建子页时记录创建当时的有效角度。已提交裁剪／裁空记录继续按既有创建角度重放，不改变跨角度选择同一数据区域的科学数据契约。

## 范围与接入边界

- 直接切片角度解析和预览分发接入 `bandscope/app/time_interval_integration.py`、`bandscope/app/refactored_app.py`；导出、视图复现、切页恢复和新建子页使用同一有效角度。
- 模式less浮窗与工具共存接入 `bandscope/ui/rotation_dial.py`、`bandscope/ui/canvas_overlay.py`。2D 继续保留选区身份、活动状态、矩形 extents、数值浮窗、增强区域 bounds 和路径草稿；3D 只更新体积 Actor，不给交互 widget 增加 Z Orientation。
- 回归沿用现有 UI／集成测试，真实窗口验收扩展 `scripts/validation/verify_canvas_rotation_dial.py` 与 `scripts/validation/verify_canvas_axis_slices.py`，不新建平行测试或验收框架。
- 用户说明、开发测试说明和原旋钮计划同步更新；历史 `docs/archive/` 不改写。原旋钮计划的既有验收仅描述其最初范围，本事项以本文件记录后续状态。

## 验证要求

### 定向与全量回归

```powershell
.venv\Scripts\python.exe -m unittest tests.ui.test_rotation_dial tests.ui.test_canvas_overlay tests.integration.test_rotation_preview tests.integration.test_canvas_axis_slices tests.integration.test_multi_region_interaction tests.integration.test_local_enhancement_controls tests.integration.test_path_slice tests.core.test_rotation_crop tests.integration.test_rotated_crop tests.integration.test_rotated_edc tests.integration.test_rotated_derived_pages tests.integration.test_view_recipe_roundtrip -v
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.venv\Scripts\python.exe -m compileall -q bandscope plugins scripts tests start.py
git diff --check
```

定向回归必须使用非对称合成体分别验证 XY／XZ／YZ 在多个当前角度下的独立参考数组，覆盖 `axis_integral_crop`、切页恢复和导出一致性；核对法向区间与坐标不变、直接切片中间预览实际更新、最终精确结果不被预览缓存替代，以及冻结派生页不进入实时预览路径。UI 与交互测试覆盖两种打开顺序、普通外部点击不误关、显式关闭路径、面板避让，以及 2D 框、3D 盒、增强框和路径节点的几何与活动状态保持。

### 真实窗口验收

使用默认 `.local/data/WSe2_step.npz` 运行更新后的旋钮和轴切片脚本，至少检查：

1. 在 30° 创建的切片上转到 0°、负角度和新正角度，中间预览与最终精确画面都变化，法向区间不变，数值与独立参考一致。
2. 先开选框再开旋转、先开旋转再开选框，两者都保持可用；裁剪／裁空浮窗、局部增强和路径选点也不关闭旋钮。
3. 连续角度序列中，2D 框和路径节点不动；3D 裁剪盒与增强盒不动，框内体积转动；精确刷新后无回弹、旧框残影或草稿重置。
4. 保存并恢复视图复现，核对当前角度、切片数组、固定区间和导出一致；已有冻结派生页行为不回归。

自动截图和数值断言不记为维护者人工视觉、鼠标流畅度或科研出图质量认可。

## 集成验证记录

### 自动化回归与静态检查

- **定向回归**：执行上述 12 个模块的定向命令，结果为 `Ran 188 tests in 83.220s`，`OK`。
- **全量回归**：执行 `PYTHONUTF8=1 .venv\Scripts\python.exe -m unittest discover -s tests -t . -v`，结果为 `Ran 2400 tests in 271.533s`，`FAILED (failures=4, skipped=5)`，无 errors。**全量未通过**，失败为：
  1. `OrientationPreviewTests.test_hover_cursor_and_disabled_interaction`
  2. `DualIntervalMouseTests.test_locked_center_drag_keeps_popup_on_the_merged_handle`
  3. `MomentumCalibrationDialogTests.test_real_mouse_selection_replaces_p2_and_undo_clear_redraw`
  4. `MomentumCalibrationDialogTests.test_slice_uses_canonical_xy_transpose_and_physical_slice_controls`
- 上述失败对应的三个模块单独运行共 **52 项，OK**。旧 `MultiRegion3DTests.test_multiple_boxes_bind_ids_and_only_active_box_syncs_controls` 作为前置也能触发轴浮框失败，旧 `RotationDialTests` 前置也能触发动量定标像素失败，表明存在 Qt 全局状态／测试顺序隔离问题。本任务未修改这些失败对应的无关模块；单模块通过不能抵消或改写上述全量失败结果。
- `.venv\Scripts\python.exe -m compileall -q bandscope plugins scripts tests start.py` 通过；`git diff --check` 通过，仅有行尾提示。

### 合成数据真实窗口验收

默认 `.local/data/WSe2_step.npz` 不存在，本轮显式改用现有 `.local/data/integration_context_menu_synthetic.npz`。以下结果仅属于该合成数据实窗验收，**不属于 WSe₂ 实窗验收**。

- **轴切片脚本**：28/28 通过，生成 14 张自动截图；独立参考最大误差约 `4.77e-7`。报告为 `.local/outputs/live-rotation-axis-slices-synthetic/report.json`。
- **综合旋钮脚本**：最终 41/41 通过，生成 22 张自动截图；覆盖 2D 多工具共存、固定几何、3D Actor 相对旋转及盒框固定、低高度布局。报告为 `.local/outputs/live-rotation-dial-synthetic/report.json`。
- 自动操作、数值断言和自动截图不记为维护者人工视觉、真实鼠标流畅度或科研出图质量通过。

## 当前状态与下一步

- 当前状态：源码与集成验证已执行；定向及合成数据实窗通过，全量保留 4 项顺序相关既有 UI 失败，WSe₂ 与人工验收待办。本事项保持开放，不记为全量通过或全部验收完成。
- 下一步由对应责任任务继续排查 Qt 全局状态／测试顺序隔离问题，并以 `PYTHONUTF8=1` 重跑全量，如实保留任何剩余失败。
- 待 `.local/data/WSe2_step.npz` 可用后，补做同一套 WSe₂ 真实窗口验收；本轮合成数据结果不能替代该项。
- 维护者人工视觉、真实鼠标流畅度和科研出图质量验收均未执行，仍需单独完成。
