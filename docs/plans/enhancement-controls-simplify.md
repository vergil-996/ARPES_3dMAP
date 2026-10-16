# 局部增强控件简化与锁定色带整合

日期：2026-10-16　状态：已实施（2026-10-16），待真实窗口视觉验收

## 实施结果（2026-10-16）

开放问题确认结论：1 保留「全局（背景）」；2 保留下拉框 × 删除；3 只显示选中区域的色带，「显示全部增强区域」勾选时全部显示；4 按钮文案沿用「锁定色带」；5 摘要标签直接移除。

已完成的改动：

- `bandscope/ui/enhancement_controls.py`：重写为 `RegionComboBox`（可编辑，NoInsert，编辑提交即 `rename_committed`；条目右侧 × 删除保留）；删除 `EnhancementControls`、`EnhancementBoundsDialog` 及重命名/范围/锁定复选框相关信号。
- `bandscope/ui/page_render_control.py`：区域下拉框 + 「锁定色带」按钮（`region_combo` / `btn_lock`）组成一行放入「色阶调整」组内、「白场」滑条以上；下拉框固定显示；删除显隐动画相关方法（`set_enhancement_controls_visible` 等）。
- `bandscope/app/refactored_app.py`：顶部栏移除 `btn_tb_lock`；`on_toggle_color_lock` 按下拉框当前对象分发——选中区域走 `_set_enhancement_locked`，选中「全局（背景）」走原 `VisualEngine.lock_data_range` 全局锁定。
- `bandscope/app/enhancement_integration.py`：删除 `_edit_enhancement_bounds` 与 `QInputDialog` 重命名；`_rename_enhancement_region(name)` 同步 `name`/`label`；新建区域默认名 `R1`、`R2`……；`_set_enhancement_locked` 返回是否生效；新增 `_sync_color_lock_button` 在每次控件同步时按当前对象刷新锁定按钮状态；`_reset_enhancement_for_new_data` 改为重置下拉框。
- 区域色带：`bandscope/rendering/local_enhancement.py` 新增 `draw_region_colorbars_2d`（2D 视图左上角叠放选中/全部可见区域的小色带，刻度用区域自身 level_info）与 `add_region_scalar_bars_3d`（3D 视图左侧每条区域一个 scalar bar）；清理挂进 `_clear_enhancement_interaction` 与 `_draw_enhancement_boxes`。
- 测试与脚本：`tests/integration/test_local_enhancement_controls.py` 适配新控件并新增重命名用例；`scripts/validation/` 下 5 个相关脚本改走 `region_combo` / `btn_lock`。

已验证：`python -m unittest discover -s tests -t .` 1214 项全部通过（含 20 项局部增强控件集成测试）。

未执行：真实窗口视觉验收（本 shell 无 OpenGL，无法启动 GUI）——需人工确认：下拉框+锁定按钮在「色阶调整」组内的布局与自适应宽度；可编辑重命名的交互手感；2D 左上角区域色带与 3D 左侧 scalar bar 的位置、配色、多条堆叠效果；`verify_color_lock.py`、`verify_local_enhancement.py`、`verify_enhancement_crop_parity.py`、`verify_enhancement_projection.py`、`verify_enhancement_outline_visibility.py` 五个脚本需在真实窗口下重跑。

## 目标

精简局部增强的控制面板，并把「锁定色带」从顶部工具栏移入「色阶调整」模块，锁定目标由区域下拉框决定；新增增强区域时在左侧视图同步显示该区域自己的色带。

## 现状

- `bandscope/ui/enhancement_controls.py` 的 `EnhancementControls` 包含：区域下拉框（`RegionComboBox`，条目右侧 × 删除）、「重命名」「范围」两个按钮、「锁定区域归一化范围」复选框、摘要标签。整体初始隐藏，添加首个增强区域后才显示（`page_render_control.py` 中 `set_enhancement_controls_visible`）。
- 「重命名」走 `QInputDialog`（`_rename_enhancement_region`）；「范围」打开 `EnhancementBoundsDialog` 输入各轴上下限（`_edit_enhancement_bounds`）；区域也可以在 2D 视图拖框、3D 视图拖 box widget 调整范围。
- 锁定有两套：顶部栏 `btn_tb_lock`（`on_toggle_color_lock` → `VisualEngine.lock_data_range`，锁定全局背景归一化范围）；增强面板复选框（`_set_enhancement_locked` → 区域的 `locked_range` / 统一模式的 `common_locked_range`）。
- 区域默认名：`name` 为「区域 N」，`label` 为「RN」（`EnhancementRegion` 创建处，`_add_enhancement_region`）。视图上的标注用 `label`。
- 左侧视图目前只有一条全局色带（2D：`_add_2d_colorbar` inset；3D：scalar bar），增强区域只通过 `enhancement_legend` 文字和虚线框标注，没有自己的色带。

## 改动方案

### 1. 控件删减（`enhancement_controls.py` + `enhancement_integration.py`）

- 删除「重命名」「范围」按钮及 `rename_requested` / `bounds_requested` 信号、`_rename_enhancement_region`、`_edit_enhancement_bounds`、`EnhancementBoundsDialog`。
- 删除「锁定区域归一化范围」复选框及 `locked_changed` 信号（功能由移入的锁定色带按钮接替）。
- `EnhancementControls` 只保留下拉框和摘要标签，改为固定显示，不再随区域有无呼出/隐藏（去掉初始 `hide()` 与 `set_enhancement_controls_visible` 的显隐逻辑，或让该方法成为无操作以兼容调用方）。

### 2. 下拉框改为可编辑

- `RegionComboBox.setEditable(True)`，编辑提交（editingFinished）即重命名选中区域，替代原「重命名」按钮；名称同步到区域的 `name` 与 `label`（视图标注随刷新更新）。
- 新建区域默认名改为 `R1`、`R2`……（沿用 `next_label` 计数；`name` 与 `label` 统一为 `RN`，不再使用「区域 N」）。
- 待确认：下拉框条目右侧的 × 删除是否保留（见开放问题）。

### 3. 锁定色带按钮移入「色阶调整」模块

- 顶部工具栏移除 `btn_tb_lock`；在 `page_render_control.py` 的「色阶调整」组内新建锁定按钮，放在区域下拉框右侧（同一行），下拉框+锁定按钮这一行整体位于「白场」滑条以上。
- `EnhancementControls` 的区域下拉框也移入「色阶调整」组内该行（从原来的独立挂载点移除）。
- 锁定按钮行为按下拉框当前选项分发：
  - 选中「全局（背景）」→ 现有 `VisualEngine.lock_data_range` 全局锁定逻辑；
  - 选中某区域 → 现有 `locked_range` / `common_locked_range` 区域锁定逻辑（`_set_enhancement_locked` 的核心部分保留，改为由按钮 toggled 驱动）。
- 按钮文案/配色随锁定状态与目标变化（如「锁定色带 / 色带已锁定」「锁定区域 / 区域已锁定」），状态同步集中在 `_sync_enhancement_controls` 与 `_update_color_lock_button_style` 的合并实现里。

### 4. 新增区域时在左侧视图显示其色带

- 添加增强区域后，左侧视图为该区域绘制一条属于它的色带（强度范围用该区域的 `data_range` / `locked_range`，样式用区域的 levels/映射设置）。
- 2D：在现有 inset 色带机制基础上支持多条（每条标记所属区域，随 `_draw_enhancement_regions` 一并刷新与清理）。
- 3D：pyvista 每条区域一个 scalar bar（title 用区域名），注意与全局 scalar bar 的位置避让。
- 待确认：是所有启用区域都显示色带，还是只显示当前选中区域的（见开放问题）。

### 5. 测试与清理

- 更新 `tests/` 中引用被删控件/信号的用例（`tests/processing`、`tests/rendering`、`tests/exporting` 下的 local_enhancement 相关测试大多测核心逻辑，应不受影响；UI 层同步逻辑如有测试需同步改）。
- `scripts/validation/verify_color_lock.py` 按新 UI 路径更新。
- 运行 `python -m unittest discover -s tests -t . -v` 回归。

## 验收标准

- 无增强区域时下拉框固定可见，只有「全局（背景）」一项；锁定按钮锁定/解锁全局色带，行为与改造前一致。
- 添加区域后下拉框出现 `R1`、`R2`……，可直接在框内改名，视图标注同步；左侧视图出现对应区域的色带。
- 选中某区域时锁定按钮只锁定该区域；切回「全局（背景）」时锁定全局；两者互不干扰。
- 「重命名」「范围」「锁定区域归一化范围」控件及其代码路径完全移除；区域范围仍可通过视图拖动调整。

## 开放问题（已确认，2026-10-16）

1. 下拉框保留「全局（背景）」作为第一项吗？——保留。
2. 下拉框条目右侧的 × 删除入口保留吗？——保留。
3. 区域色带的显示范围？——只显示当前选中区域的一条；勾选「显示全部增强区域」时全部显示。
4. 锁定按钮文案？——沿用「锁定色带 / 色带已锁定」。
5. 摘要标签？——直接移除。

## 下一步

真实窗口下完成视觉验收（范围见「实施结果」末节）；验收闭环后本记录连同材料归档到 `docs/archive/`。
