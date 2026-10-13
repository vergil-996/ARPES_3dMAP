# 1D / 2D 活动裁剪框

## 实施与验收（2026-10-13）

- 状态：功能已实现，原计划由本次集成任务接续完成并归档；工作区未提交。
- 原因：此前只有已确认计划，功能代码仍使用 `interactive=False` 的一次性框选器，并未实施活动框。
- 负责范围：`bandscope/ui/` 活动框、`bandscope/app/` 裁剪接入与刷新生命周期、对应 UI/集成测试、真实窗口验收脚本。本任务保留开始时已有的 3D 超界草稿、时间同步及切片强度修复。
- 共享文件：`bandscope/app/crop_integration.py`、`bandscope/app/refactored_app.py`。未改插件 API、数据模型、依赖和打包配置。
- 交付：新增 `InteractiveCropBox`，2D、1D、曲线对比、瀑布图的裁剪与裁空共用活动框。2D 首次使用居中的 80% 数据范围，为九个控制点留边距；恢复逐页草稿，四角/四边控制点缩放、内部平移、边界保持大小；框外手势不重新框选，禁用旋转/清空快捷键。零宽/零高恢复有效范围，数值超界草稿保留原值。
- 同步与清理：释放鼠标和编辑数值原位同步，不销毁选择器；完整重绘后重建，预览保留并共享坐标提示 blit；停用与切页移除 artists 和事件，避免重复响应。

### 自动化验证

Windows 本机 `.venv`（Python 3.12、Matplotlib 3.10.8）。新增 17 项 UI/同步测试、1 项持续显示像素测试，更新 3 项 blit 手势测试。最终验证：

```powershell
.venv/Scripts/python.exe -m unittest tests.ui.test_interactive_crop_box tests.integration.test_interactive_crop_sync tests.integration.test_axis_crop_blit -v
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v
.venv/Scripts/python.exe -m compileall -q bandscope/ui/interactive_crop_box.py bandscope/app/crop_integration.py bandscope/app/refactored_app.py tests/ui/test_interactive_crop_box.py tests/integration/test_interactive_crop_sync.py scripts/validation/verify_interactive_crop_box.py
```

- 定向交互回归：21 项通过；连同坐标提示和裁剪控制回归共 38 项通过。覆盖所有角点/边中点、内部移动与边界、边缘命中、反向坐标、跨越对边、无效范围恢复、标题 widget lock、数值同步、逐页恢复、裁剪/裁空各视图以及重复开关/清空 axes 不泄漏。
- 全量回归：1166 项通过。日志：`.local/outputs/interactive-crop-tests.log`（不提交）。
- 首次沙箱内运行出现临时目录清理 `PermissionError`；改在正常权限环境重跑，最终全量回归通过且没有该清理错误。

### 真实窗口与截图

```powershell
.venv/Scripts/python.exe scripts/validation/verify_interactive_crop_box.py
```

默认数据 `.local/data/WSe2_step.npz`，Windows 原生 Qt/VTK 窗口，隔离 INI 设置和插件目录。脚本驱动真实 Qt 鼠标事件，23 项通过：3D 交互盒保留、3D 单层转 2D、初始内缩框与九个控制点的像素、拖动松手/悬停/移出画布后九个点的持续显示、角点拖动、内部平移、框外忽略、数值同步、完整刷新、E 开关前后 kx–ky 选框保持、创建裁剪页、连续裁空及停用清理。截图在 `.local/outputs/interactive_crop_box/`；补修后已查看 `initial_canvas.png`、`resized_canvas.png`，确认实心高亮控制点清晰可见。

### 持续高亮补修（同日）

用户实际使用发现拖动后鼠标悬停会让选框消失。原因是坐标提示恢复了不包含 animated artists 的背景，只重画提示框，把裁剪框和手柄擦掉；此前仅检查 `get_visible()`，截图前强制 draw 也掩盖了该问题。

修复：坐标提示的独立 blit 同时重画宿主活动框；松手、悬停、提示隐藏和移出画布均保留选框。边框改为高亮实线及深色描边，九个手柄改为实心品牌粉。2D 初始范围内缩各轴两侧 10%，已有草稿和手动数值原样恢复。新增实际像素回归；真实窗口截图不再强制重绘，直接验收当前画面。

### 未执行与后续

- 真实窗口验证为脚本驱动，人工鼠标手感与 1D/对比/瀑布页逐张视觉复核未执行；这些页的活动框接入由合成数据自动化覆盖。
- 未重建冻结包/安装器，也未启动已安装旧版本；本次修改需从当前源码重启程序才生效，发行包随之后版本纳入。
- 未单独安装 Matplotlib 3.8 运行兼容矩阵；本次没有新增依赖。
- 下一步：按后续发布流程打包，实际使用时复核边缘手柄手感。

---

## 原始计划（历史记录）

## 状态与交接

- 状态：用户已确认方案，待其他 AI 接手实施；本轮只保存计划，没有修改功能代码。
- 负责范围：接手任务兼任集成负责人，负责 `bandscope/ui/` 的活动框、裁剪接入、主窗口生命周期，以及对应自动化测试和视觉验收。
- 共享文件：`bandscope/app/crop_integration.py`、`bandscope/app/refactored_app.py`；必要时调整 `bandscope/ui/crop_controls.py` 的同步接入。
- 相关工作：[轴标题编辑与页面重命名](../axis-titles/axis-title-editing.md)（2026-09-28 已实施并归档）。该功能已改过主窗口、裁剪接入与 2D 画布鼠标事件：标题命中会先拿画布 widget lock 让裁剪选区让路。实施前先看那份验收记录，再做本功能。
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

## Matplotlib 3.11 兼容修复（2026-10-13）

- `update`（`b601ea0`）已推送成功；GitHub Actions 回归失败，是因为 Matplotlib 3.11.2 已移除 `AxesWidget._get_data_coords`，内部平移回调报 `AttributeError`，四个平移用例失败。
- `bandscope/ui/interactive_crop_box.py` 的平移回调改用已由 `onmove` / `_clean_event` 转换到本坐标轴的 `event.xdata`、`event.ydata`，不再调用已删除的私有方法；边界限制与缩放逻辑保持原样。
- 验证环境：Windows、Python 3.12.10、Matplotlib 3.10.8。`python -m unittest tests.ui.test_interactive_crop_box -v` 的 11 项交互测试通过；`python -m unittest discover -s tests -t . -v` 的 1213 项全量测试通过；编译与 diff 空白检查通过。完整日志在 `.local/logs/crop_mpl_compat_full.log`。
- 尝试在 `.local/outputs/` 隔离安装 Matplotlib 3.11.2，但下载未完成，已停止安装，现有虚拟环境未修改。未执行：3.11.2 本机运行、修复后的 GitHub CI、真实窗口视觉验收及发行包重建。
