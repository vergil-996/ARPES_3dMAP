# 局部增强控件简化与锁定色带整合

日期：2026-10-16　状态：已实施并随 v1.14.3 发布（2026-10-17）；自动化与真实窗口脚本通过，仍有人工视觉及发行包验收

## 当前修复（2026-10-17）：色阶与强度映射跟随调整对象

用户反馈二次调整误把黑白灰场滑条与强度映射全局化。当前约定：选中某区域时只调整该区域，
选「全局（背景）」时只调整背景；用户确认「全部显示」时同时调整背景与全部增强区域。
锁定色带仍统一锁定各对象自己的强度范围。

- 负责范围：`bandscope/app/enhancement_integration.py` 状态提交与回显，`refactored_app.py` 控件刷新接入，
  `bandscope/ui/` 提示与注释，增强控件集成测试及现有真实窗口验收脚本。
- 区域与背景分别保存 levels / opacity_mode；切换对象前保存当前设置，切换后阻断信号回显新对象。
  新增区域继承当前参数；删除选中区域后回显接替区域，删完后回显背景；页面切换与导出保留独立设置。
- 「全部显示」以背景参数作为批量调整起点；实际拖动滑条或应用映射时统一写入。
  仅查看全部色带、切换页面或保存状态不会自动抹平不同对象的设置。
- 已验证（Windows、仓库 `.venv` Python 3.12.10、原生 Qt 窗口、可用 OpenGL）：
  增强控件集成测试 30 项，相关模型/处理/渲染测试 26 项，全量
  `python -m unittest discover -s tests -t . -v` 1266 项全部通过；编译、差异与文档链接检查通过。
- 真实数据 `.local/data/WSe2_step.npz`：`scripts/validation/verify_local_enhancement.py`
  146 个检查通过，覆盖 2D/3D 独立调节与其他对象显示通道不变、切换回显、全部显示批量调节、
  锁定、区域交互与删除、出版导出及科学数据不变。证据位于
  `.local/outputs/enhancement-target-controls/acceptance.json` 与同目录截图；日志位于
  `.local/logs/enhancement-target-native.log`、`.local/logs/enhancement-target-full-regression.log`。
  已查看 2D 窗口与单独的 3D scene 截图；Qt 窗口 grab 不包含原生 VTK 场景，3D 画面以 scene 图为准。
  验收脚本改用含中间强度的 R1 数据范围，避免原全零/二值区域无法验证色阶变化；区域标签按实际状态核对。
- 发布（2026-10-17，v1.14.3）：提交 `28928d2`，标签 `v1.14.3` 的 CI 测试、插件打包与签名、CPU / NVIDIA 安装包全部通过，
  GitHub Release 已发布；CPU `256f1f29…`、NVIDIA `26f24ac0…`（sha256 边车一致）。MatElab 更新源同版发布并通过只读校验，
  见 [MatElab 更新源接入](matelab-update-source.md)。
- 未执行：用户人工拖拽手感与完整视觉复核、实窗 ROI 去噪、其余四个既有增强 validation 脚本、
  冻结包（CPU / NVIDIA）与安装器端到端验收。下一步仍按这些范围补验，本记录保留开放。

## 三次调整（2026-10-19）：删除入口移至视图右键 + 下拉框改用 SiliconUI

状态：自动化、2D/3D 真实窗口命中与下拉弹层检查已通过；真实鼠标点击手感待人工确认；已随 v1.14.5 发布（2026-10-19）。

- 增强区域删除入口从下拉框条目右侧的 × 移到左侧视图右键：2D 在当前显示轮廓的区域上命中，3D 用
  `vtkPropPicker` 命中区域框；菜单新增「删除增强区域「R1」」，未命中区域时不显示该条目。
  3D 的 `vtkPropPicker` 只命中轮廓线，因此框内部（空白体素）再按八个角点的投影凸包做一次
  命中测试，保证右键区域内部也能出删除项。删除仍走 `_delete_enhancement_region`，保持原有
  选中接替、背景重新归一化与全局解锁行为。
- `RegionComboBox` 改为继承 `siui.components.combobox_.SiCapsuleComboBox`：保留可编辑重命名、
  空名/固定条目回退与 `target_changed`/`rename_committed` 信号；移除 `delete_requested`、
  `QListView` 委托和条目 × 点击拦截；同时移除下拉框及其编辑框的 `setToolTip`，悬停不再弹出气泡。
- 相关文件：`bandscope/ui/enhancement_controls.py`、`bandscope/app/enhancement_integration.py`、
  `bandscope/app/refactored_app.py`、`tests/integration/test_local_enhancement_controls.py`、
  `scripts/validation/verify_local_enhancement.py`。
- 已验证（2026-10-19，仓库 `.venv` Python 3.12.10）：
  - `python -m unittest tests.integration.test_local_enhancement_controls -v`：40 项通过；
    其中断言下拉框与编辑框 `toolTip()` 为空，悬停不再出现气泡；
  - `python -m unittest discover -s tests -t .`：1306 项全部通过；
  - 真实数据 `.local/data/WSe2_step.npz`：`scripts/validation/verify_local_enhancement.py
    --output-dir .local/outputs/enhancement-3d-context` 160 项通过，覆盖 2D 右键命中与菜单删除、
    3D 区域框内部右键命中与删除项、删除后的色带/轮廓与科学数据不变；证据为同目录
    `acceptance.json`、`2d_show_all_window.png`、`region_delete_menu.png`、
    `3d_region_delete_menu.png`；
  - `scripts/validation/verify_enhancement_outline_visibility.py` 24 项通过，覆盖 3D/2D
    下拉切换、全部显示与轮廓/色带联动；证据在 `.local/outputs/enhancement-outline-visibility/`；
  - SiliconUI 下拉框实际弹层：菜单列出「全局（背景）/全部显示/R1/R2」四项，触发 R2 后
    `currentIndex=3`、框内文字 `R2`、`target_changed="2"`；自动化
    `test_siliconui_popup_lists_regions_and_selects_target` 已覆盖，截图
    `.local/outputs/region_dropdown_popup.png`；窗口内布局见 `2d_show_all_window.png`。
- 发布（2026-10-19，v1.14.5）：提交 `fe914ff`，标签 `v1.14.5` 的 CI 全部成功；GitHub Release
  与 MatElab 均已发布并完成校验。CPU `3d6e2c60…`、NVIDIA `5945b041…`（sha256 边车一致），
  插件与目录资产摘要与 CI 一致；MatElab 记录 `bandscope-release-1.14.5`。
- 未执行：真实鼠标点击到 Qt `customContextMenuRequested` 的端到端手测（当前用真实 VTK
  事件位置、真实相机投影与菜单构建验证）、人工拖动/视觉手感复核。
- 下一步：人工在 3D 视图右键区域框内部确认菜单与删除手感；其余视觉项随本记录保留开放。

## 数字键输入与切页快捷键冲突修复（2026-10-19）

状态：已修复并验证。

- 现象：可编辑区域下拉框只能输入 5 及以后的数字，1-4 输入不进去。
- 原因：SiliconUI 可编辑下拉框的焦点留在 `RegionComboBox` 本身，文字由内部
  `SiCapsuleLineEdit` 接收，`QApplication.focusWidget()` 返回的是组合框。
  `_focus_accepts_number_input()` 只按 `LineEdit`/`EditBox`/`SpinBox` 等类名判断，
  没识别可编辑 `ComboBox`，于是 `_handle_number_page_shortcut` 把 1-4 当成数字切页消费。
- 修复：`bandscope/app/refactored_app.py` 的 `_focus_accepts_number_input()` 对可编辑
  ComboBox 返回 `True`；非可编辑 ComboBox 仍返回 `False`，保留左侧结果页数字切页。
  同时移除 `_select_page_under_cursor` 的右侧控件页分支：数字键 1-4 不再切换
  「渲染控制 / 处理分析」，右侧仍可用 `Ctrl+1` / `Ctrl+2`。
- 验证：实窗脚本输入 `12345` 完整保留；新增单测
  `test_editable_combo_focus_accepts_number_input`；`verify_page_shortcuts.py` 的实窗检查
  全部通过，确认数字键只切左侧、不再切换右侧控件页；增强控件集成测试 40 项、全量
  `python -m unittest discover -s tests -t .` 1308 项全部通过。

## 二次调整记录（2026-10-17）：色带跟随下拉框 + 全局统一调控

当次实现解决新增增强区域后多条色带堆叠的问题：色带跟随下拉框选择显示，新增「全部显示」条目。
同时误将黑白灰场滑条、强度映射改成全局统一调控；这部分已由上面的当前修复纠正。
下面保留当次实现与验证快照，当前行为以上节为准。

已完成的改动：

- `bandscope/ui/enhancement_controls.py`：`RegionComboBox` 新增固定条目「全部显示」
  （`SHOW_ALL_REGIONS_ID`），位于「全局（背景）」之后；该条目不可重命名、无 × 删除；
  `sync(regions, selected_id, show_all)` 按 `show_all` 选中该条目。
- `bandscope/app/enhancement_integration.py`：
  - `_select_enhancement_target`：选「全部显示」置 `show_all_regions=True`（保留 `selected_id`
    供视图内点选回退），选其他项即关闭；下拉框成为全部显示的唯一入口，右键菜单动作保留但与下拉框双向同步。
  - `_commit_enhancement_controls`：色阶滑条与强度映射提交到背景与全部区域（全局同一份样式）；
    `_restore_enhancement_target_style` 更名 `_restore_enhancement_style`，始终从背景（全局）样式恢复，
    切换下拉框目标不再变动滑条。
  - `_set_all_regions_locked(locked)` 取代 `_set_enhancement_locked` / `_set_enhancement_common`：
    每个区域锁定自己实测的强度范围；删除区域走 `_release_global_lock`（背景与全部区域一起解锁）；
    全局锁定开启时新增区域立即锁定自己的当前范围。
  - `_sync_color_lock_button`：背景或任一区域有锁定即显示锁定态。
  - 2D 区域色带改为左下角向上堆叠，摘要文字移到左上角；3D 摘要文字移到 upper_left 避让 scalar bar。
- `bandscope/app/refactored_app.py`：`on_toggle_color_lock` 不再按下拉框目标分发，
  统一执行「背景（`VisualEngine.lock_data_range`）+ 全部区域各自范围」的全局锁定/解锁。
- `bandscope/ui/page_render_control.py`：锁定按钮与下拉框的注释、tooltip 改为全局语义。
- `bandscope/rendering/local_enhancement.py`：`draw_region_colorbars_2d` 槽位移到左下角。
- UI 层不再能进入 common（共享归一化）模式；`resolve_display` 与核心层保留 common 兼容（旧页面状态），
  处理层测试不变。
- 测试：`tests/integration/test_local_enhancement_controls.py` 适配全局样式/全局锁定/「全部显示」条目，
  新增下拉框色带可见性与全局锁定逐区域范围用例（22 项）；`scripts/validation/` 五个相关脚本
  改走新下拉框布局（条目数 +1、索引顺延、「全部显示」步骤替换原 common 模式步骤）。

已验证：`python -m unittest discover -s tests -t .` 1239 项全部通过。

未执行：真实窗口视觉验收（本 shell 无 OpenGL）——除原「实施结果」末节列出的项目外，
需额外确认：下拉框「全部显示」条目与 × 删除的交互；2D 左下角色带堆叠与左上角摘要文字的位置配色；
全局锁定按钮在有/无区域、跨页面切换时的状态显示。

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

> 2026-10-19 已变更：第 2 条的「下拉框 × 删除」改为左侧视图右键删除；下拉框改用 SiliconUI
> 可编辑框。以上旧结论仅保留为当时记录，当前实现见文首「三次调整」。

## 下一步

真实窗口下完成视觉验收（范围见「实施结果」末节）；验收闭环后本记录连同材料归档到 `docs/archive/`。
