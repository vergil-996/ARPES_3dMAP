# 1D / 2D 活动裁剪框

## 状态与交接

- 状态：用户已确认方案，待其他 AI 接手实施；本轮只保存计划，没有修改功能代码。
- 负责范围：接手任务兼任集成负责人，负责 `bandscope/ui/` 的活动框、裁剪接入、主窗口生命周期，以及对应自动化测试和视觉验收。
- 共享文件：`bandscope/app/crop_integration.py`、`bandscope/app/refactored_app.py`；必要时调整 `bandscope/ui/crop_controls.py` 的同步接入。
- 相关工作：[轴标题编辑与页面重命名](../archive/axis-titles/axis-title-editing.md)（2026-09-28 已实施并归档）。该功能已改过主窗口、裁剪接入与 2D 画布鼠标事件：标题命中会先拿画布 widget lock 让裁剪选区让路。实施前先看那份验收记录，再做本功能。
- 验收结果：已完成代码调查和既有测试基线检查；本功能尚未实施，未进行本功能视觉验收。
- 下一步：阅读各级 `AGENTS.md` 和根目录约定的架构、测试、插件及当前交接文档，检查 `git status --short`，声明负责范围后按本计划实施。
- 交付物：功能代码、自动化测试、真实窗口验收结果及未执行事项。本文件是本功能唯一当前计划，实施期间更新状态，完成后连同验收记录归档。

## 已确认目标与交互

- 2D、1D、曲线对比和瀑布图统一使用活动矩形框，适用于“裁剪”和“裁空”。用户明确选择两者统一。
- 开启模式即显示选框：优先恢复当前页保存的范围，首次使用覆盖完整数据范围。
- 四角及四边中点提供控制点；拖控制点调整范围，拖框内部整体移动，无需按辅助键。
- 1D 保留横轴和强度上下限；用户明确选择横纵轴矩形框。
- 框外拖动不重新框选，矩形不支持旋转。
- 拖动实时更新框的位置；松手后同步数值浮窗。编辑数值也同步选框，点击浮窗按钮才执行操作。
- 保留右键打开范围浮窗、Esc 收起浮窗及退出模式的现有行为。

## 实现方式

- 在 `bandscope/ui/` 封装活动矩形选择器，复用 Matplotlib `RectangleSelector` 的交互控制点、内部拖动和局部重绘能力，无需新增依赖。
- 在裁剪接入中用活动框替换当前静态虚线框与一次性框选的组合；继续由 `CropController` 保存各页选区。
- 数值修改和鼠标释放后原位同步范围，避免在鼠标回调中销毁、重建选择器。零宽或零高操作恢复上次有效范围。
- 鼠标移动限制在绘图区内，整体移动到边界时保持框的大小。数值输入沿用现有校验规则，显示裁切不得反写修改输入范围。
- 调整主窗口的清理与刷新接入：完整重绘后恢复活动框，快速刷新保留控制点；关闭模式、切页和清空数据时移除图形及事件连接，避免残影和重复响应。
- 保留坐标提示与选框共享局部重绘的机制。与轴标题编辑任务协调事件优先级，命中轴标题时由标题交互接管。
- 裁剪数据模型、保存格式、插件公开 API 和 3D 交互保持兼容。

### 调查所得的接入注意事项

- 当前 `_refresh_axis_crop_interaction` 每次清理、重建非交互 `RectangleSelector`，并叠加独立的静态 `Rectangle`；改造后只保留一份可见选框。
- `_on_crop_selection_changed` 当前会触发选择器重建，需区分范围原位同步与页面/画布重建。
- 主窗口的 `_clear_axis_crop_selector` 当前只停用、隐藏并断开事件；接手时补齐所属 artists 的移除，检查重复开关不会累计隐藏对象。
- `_active_2d_blit_artists` 与 `_sync_active_2d_blit_background` 已负责选框及坐标提示的快速刷新接入，新增控制点也应纳入。
- 本机 Matplotlib 为 3.10.8，项目声明最低版本为 3.8。当前选择器提供 `interactive`、`drag_from_anywhere`、`ignore_event_outside` 等参数；实施需保持声明的版本兼容性。
- 不能只开启 `interactive=True`：还需初始化可见范围、确保首次框外拖动不会创建新框、禁用旋转/清空快捷键对活动框的破坏，并处理退化范围恢复。
- Matplotlib 默认会逐边裁切越界矩形；整体平移需另行限制位移，确保触边不会缩小选框。
- `scripts/diagnostics/repro_crop_erase.py` 的 2D 路径使用旧拖动框选流程；若用于本功能验收，须同步改成拖动已有活动框的控制点。

## 验证计划

- 自动化覆盖初始显示、各控制点缩放、内部平移、边界限制、框外操作、无效范围恢复，以及浮窗与选框双向同步。
- 覆盖各视图的裁剪和裁空、反向坐标与能量翻转、切页恢复、连续开关及刷新后无重复图形或事件。
- 保留拖动期间局部重绘与坐标提示共享绘制的测试，并验证拖动只调整草稿、执行按钮生成结果页。
- 更新现有 `tests/integration/test_axis_crop_blit.py` 的替身和手势：测试应从已显示的活动框开始拖动，而不是从空白处创建矩形。
- 新测试使用合成数据和隔离设置；需要共享构造器时放入 `tests/support/`，不得依赖本机实验文件或从其他测试文件导入辅助函数。
- 运行受影响模块测试后，执行全量回归：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.integration.test_axis_crop_blit tests.integration.test_crop_controls tests.integration.test_crop_integration tests.core.test_crop_model -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

- 真实窗口验收控制点可见性、拖动手感、边缘命中、连续裁剪后裁空及 3D 回归。窗口验证遵循开发文档的图形平台要求，明确记录数据来源、环境和检查范围；未执行项不得写为通过。
- 提交前检查 `git status --short`，只包含本任务代码、测试及说明，不提交 `.local/`、环境或构建产物。

## 已完成的基线检查

2026-09-28 在 Windows 本机 `.venv` 中执行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.integration.test_axis_crop_blit tests.integration.test_crop_controls tests.core.test_crop_model -v
```

- 23 项既有测试通过，进程退出码为 0。
- 退出时 `TemporaryDirectory.cleanup` 出现 `PermissionError: [WinError 5]`，涉及测试临时目录清理。实施验收时应在正常临时目录权限的环境复核并记录，不能把该环境问题隐去。
- 本轮未运行全量回归，未验证新增活动框行为，也未执行真实窗口视觉验收。
