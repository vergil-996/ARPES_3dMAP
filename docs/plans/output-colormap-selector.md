# 将色带选择并入“输出色带”

2026-11-21 · 已实施；自动化、WSe₂ 真实窗口视觉验收与分段全量回归通过，保留维护者人工视觉复核。

删除主界面原先独立的“色带选择”卡片、其中的“确定”按钮及其刷新接线。“色阶调整”卡片内的“输出色带”标题行成为主界面唯一的色带选择入口，默认显示 **`magma ▾`**。

## 控件与交互

- 标题行左侧显示“输出色带”，右侧显示当前色带名和下拉箭头。下方整行留给现有渐变条、黑白手柄和百分比读数。
- 点击色带名展开可滚动的单列菜单，每项显示完整渐变色样和名称，当前项带勾。列表以 `magma`、`coolwarm` 开头，其余色带保持原有相对顺序。Blender 导出窗口复用同一份列表，也采用此顺序。
- 用户选中另一条色带后，菜单收起，渐变条立即更新，当前视图精确刷新一次。悬停、取消或再次选择当前项均不触发渲染。
- 换色带保留当前输出区间。例如从 `magma` 的 15%–80% 切到 `coolwarm`，两个手柄仍停在 15% 和 80%。色带名由当前视图共用，手柄继续调整当前对象的输出区间。

## 状态兼容

沿用现有按名称保存的 `combo_cmap` 字段。新视图及缺少色带值的旧状态默认使用 `magma`。切页或恢复配方时，按已保存的名称同步标题和渐变条，不因列表重排选错色带，也不额外触发用户选色的刷新流程。

## 验收

- 用界面测试核对默认值、列表前两项、`coolwarm` 仅出现一次，以及旧卡片和按钮已移除。
- 用集成测试覆盖选择 `coolwarm`、保留端点、精确刷新一次，以及取消选择和恢复已有配方时的行为。
- 将现有视觉验收脚本改为通过新菜单选色，检查窄侧栏和深浅主题下的色名、色样与手柄布局，再运行全量 `python -m unittest discover -s tests -t . -v` 回归。

## 实施与验收记录

- 2026-11-21 实施：新增 `bandscope/ui/colormap_selector.py`，集中 `DEFAULT_COLORMAP` 与共享 `COLORMAP_OPTIONS`（`magma`、`coolwarm` 置顶且 `coolwarm` 只出现一次，其余保持原相对顺序）；`OutputColormapSelector` 由左侧标题、右侧色带名与下拉箭头组成，点击整条标题行展开带完整渐变色样、名称和当前项勾选的可滚动单列菜单，Esc 或点菜单外取消不产生选择，只有选中另一条色带才发出一次 `colormap_changed`。菜单在当前项处滚动显示，高度按屏幕可用空间上下翻转并限制在 380 px。
- `RenderControlPage` 删除「色带选择」卡片、`combo_cmap` 与「确定」按钮，把 `OutputColormapSelector` 放进「色阶调整」卡片的「输出色带」标题行，与渐变条同宽；`get_selected_cmap()` 改读选择器，`export_state()`／`restore_state()` 继续按名称保存和恢复 `combo_cmap`，恢复时静默同步标题与渐变条。主窗口只接 `page_render.colormap_changed` 一次 `TRANSFER_FUNCTION` 精确刷新；同色带重选、悬停和取消都不再触发渲染。Blender 工程编辑窗口改用同一份 `COLORMAP_OPTIONS`。
- 自动化：新增 `tests/ui/test_colormap_selector.py`（10 项：共享顺序、默认值、菜单各项与完整色样、滚动显示当前项、选中一次生效、重选／取消、程序化同步静默、点击标题行展开）和 `tests/integration/test_colormap_selection.py`（4 项：选择 `coolwarm` 保留 15%–80% 端点并只请求一次精确刷新、重选与取消不刷新、恢复配方静默同步、缺少色带值的旧状态回落 `magma`）；`tests/ui/test_control_page_state.py` 增加默认值、旧卡片／按钮移除与窄栏最长色名布局断言，并在状态往返中核对色带名。
- 2026-11-21 真实窗口：`python scripts/validation/verify_levels_and_colormap_range.py .local/data/WSe2_step.npz` 的 9 项检查通过（默认 `magma`、首两项、`coolwarm` 唯一、旧控件移除、每次选色只完成一次精确刷新、重选／取消不刷新、窄侧栏与深浅主题下标题行色名、菜单色样与勾选、渐变条与手柄布局）。15 张截图与 `report.json` 在 `.local/outputs/levels-and-colormap-range/`，其中 `colormap-header-*.png`、`colormap-menu-*.png` 为本轮新增。
- 2026-11-21 回归：单进程 `python -m unittest discover -s tests -t . -v` 在既有问题模块 `tests.integration.test_axis_interval_pages` 处再次触发 Windows 原生崩溃（`QPaintDevice: Cannot destroy paint device that is being painted`，退出码 0xC0000005，日志 `.local/logs/full-regression-colormap-selector.log`）。分段复核：排除该模块后 1904 项全部通过（543.9 s，日志 `.local/logs/colormap-selector-full-chunked-utf8.log`）；该模块单独运行 33 项，在未修改 HEAD 的 detached worktree 中 10 次运行复现 1 次同一 `QDoubleSpinBox ... has been deleted` 原生中断（0xC0000409），当前工作区（含上一任务未提交的控件改动）中断更频繁，最终重试取得一次 33 项全通过（日志 `.local/logs/colormap-selector-axis-interval-pages-retry.log`）。该中断的调用链在 `_AxisHarness` + `TimelineBar`／`AxisIntervalController`，不经过本任务新增的输出色带控件；该模块结果依赖重试，不写成稳定全绿。合计 1937 项；`python -m compileall -q bandscope plugins scripts tests start.py` 与 `git diff --check` 通过。
- 未执行：维护者人工视觉复核（窄侧栏、深浅主题下的色名／色样／手柄观感与菜单滚动手感）以及冻结包／安装器验收；本轮无发布动作。
