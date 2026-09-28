# 坐标轴积分交互重构（2026-09-28）

> 已归档。原文是实施前的计划（`docs/plans/axis-integration-refactor.md`），下面是
> 实施后的验收记录。本文件不是当前执行指令；后续工作见 [当前计划](../../plans/README.md)。

## 验收结果（2026-09-28）

- 状态：已实施，自动化回归与真实窗口视觉验收均已执行；发布在 v1.11.0。
- 负责范围：本任务兼任集成负责人，覆盖 `bandscope/core/` 的区间模型、`bandscope/ui/`
  的控件与同步、主窗口与裁剪接入，以及对应自动化测试与视觉验收脚本。
- 交付物：功能代码、自动化测试、真实窗口验收脚本与截图（同目录）。

## 改了什么

| 位置 | 内容 |
|---|---|
| `bandscope/core/axis_interval.py`（新增） | `AxisInterval` 物理区间模型（端点、长度、中心、锁定、平移触边、`to_indices` 采样映射）与 `AxisSpace`（范围、采样坐标、单位标签）。纯数值，不依赖 Qt |
| `bandscope/ui/axis_interval_controller.py`（新增） | `AxisIntervalController`：控件只提交意图，模型负责约束，一次阻断信号同步全部控件；`IntervalEditMode` 区分完整编辑 / 只移动位置 / 禁用 |
| `bandscope/ui/page_data_process_v2.py` | 「对坐标轴积分」卡片删除中心位置控件，新增可输入「积分长度」与「锁定区间」按钮；页面不再自己维护中心与锁定半宽 |
| `bandscope/ui/timeline_bar.py` | 底栏左侧新增积分位置组（滑条 + 物理值输入框）；位置组与时间轴同排分享宽度，窄窗下视图控件整组下移第二行并同步加高底栏 |
| `bandscope/ui/control_layout_utils.py` | 新增 `animate_widget_width`（与显隐动画同一套 220 ms / InOutCubic 参数，作用于宽度） |
| `bandscope/app/refactored_app.py` | 区间真值接入：`_axis_interval_context` / `_bind_axis_interval` / `_restore_axis_interval` / `_persist_axis_interval_state`；刷新链路改为 `intervalChanged` → 预览、`intervalCommitted` → 精确；状态栏新增区间锁定指示灯；底栏排版变化合并画布刷新 |
| `bandscope/app/crop_integration.py` | 新增 `_update_slice_source_range`：单层切片页移动位置只改产生该切面的那一层源范围 |

## 关键设计

- **一份真值**：页面参数里保存 `axis_interval`（物理上下限 + 锁定），整数 `low` / `up` / `mid`
  由它派生。积分、裁剪、二阶导、瀑布图和导出继续读原来的整数键，接入点不变。
- **物理端点**：模型保留落在采样点之间的精确端点；计算时才由 `to_indices` 映射到最近采样点，
  按原始下标排序后包含两端求和，等距取较小原始下标。数值框按坐标步长设精度，不再固定两位小数。
- **滑条映射**：位置类滑条以 `SLIDER_STEPS = 1000` 等分整段物理跨度，拖动连续；精确值仍由模型
  与输入框持有。
- **刷新时机**：拖动只发预览，释放与输入提交才发精确刷新；同步其它联动控件时阻断业务信号，
  一次操作只落定一次。
- **整段区间不能平移**：完整轴范围时长度等于跨度，平移到不了任何地方（两端一起停在边界），
  这与「平移触边时限制位移、长度不变」是同一条规则的结果。

## 与计划的差异

- 计划写「长度最大为当前轴完整跨度」，实现相同；但**完整跨度时位置滑条不动**是这条规则的
  直接推论，计划没有明说，验收脚本据实记录。
- 计划未点名「页面参数里旧整数键的写入位置」：本实现由 `_persist_axis_interval_state` 统一
  按来源页类型选择键名（`low/up/mid`、`slice_index`、`integral_*`），不再散落在各持久化函数里。
- 底栏换行阈值按实际控件最小宽度计算（约 1250 px 需要单行容纳位置组 + 时间轴 + 视图控件）。
  实测默认 1550×950 窗口下画布区约 824 px，因此**默认窗口即处于两行布局**，这是预期结果。
- `verify_time_axis_visibility.py` 原断言「动态数据加载后底条保持固定高度 56」仍然通过
  （该流程下未触发换行），未做修改。

## 验证

自动化（`.venv/Scripts/python.exe`，Windows 11 / Python 3.12.10）：

```powershell
python -m unittest discover -s tests -t . -v     # 773 条通过（基线 678 条）
python -m compileall -q bandscope plugins scripts tests start.py
python scripts/release/check_release_version.py v1.11.0
python scripts/release/build_plugin.py flat_band_opacity --output-dir .local/outputs/release-check
```

新增 95 条：`tests/core/test_axis_interval.py`（50，区间规则、数值映射、序列化）、
`tests/ui/test_axis_interval_controller.py`（19，真实控件同步、锁定、模式）、
`tests/integration/test_axis_interval_pages.py`（26，页面恢复与状态隔离、切片源范围、底栏布局）。
既有 `test_second_derivative` / `test_time_integral_derivation` / `test_analysis_control_refresh`
按新契约改写（不再依赖已删除的中心控件与 `locked_half_width`）。

真实窗口（本机 NVIDIA 桌面，`windows` 平台，数据 `.local/data/scan07_dynamic.npz`）：

```powershell
python scripts/validation/verify_axis_interval.py .local/data/scan07_dynamic.npz        # 48 项全通过
python scripts/validation/verify_axis_interval.py .local/data/NiHITP_calibrated_2.npz   # 52 项全通过（无时间轴）
python scripts/validation/verify_time_axis_visibility.py .local/data/scan07_dynamic.npz .local/data/NiHITP_calibrated_2.npz
python scripts/validation/verify_crop_window.py .local/data/scan07_dynamic.npz
python scripts/validation/verify_axis_titles.py .local/data/scan07_dynamic.npz
python scripts/validation/verify_page_tree.py .local/data/scan07_dynamic.npz
python scripts/validation/smoke_test.py .local/data/scan07_dynamic.npz --dynamic
python scripts/validation/export_acceptance.py .local/data/NiHITP_calibrated_2.npz
```

`verify_axis_interval.py` 覆盖：位置组呼出的宽度过渡中间帧、完整范围平移触边不产生刷新、
拖动位置保持长度、输入长度以中心扩缩、锁定后整体平移与长度框只读、状态灯文案与配色、
结果页之间的区间与锁定隔离与切回恢复、换行后底栏加高、单层切片页只移动位置且源范围跟随、
切回 3D 主页后位置组收起。

无时间轴数据（`NiHITP_calibrated_2.npz`）额外断言：时间轴组收起、位置组从底栏左侧内边距处
开始（实测 left=15，即 14 px 边距 + 1 px 边框，左侧无占位空隙）、位置滑条拿到整行可用宽度
（实测 737 px，最低可拖宽度为 120 px）。输入框显示按坐标分辨率存在一个显示单位内的舍入，
模型仍是精确值——这正是「显示舍入不再影响数据」要保证的，验收脚本按显示单位而非固定
容差判断。

## 验收中发现并已修复的缺陷

1. **「应用」生成的新页面丢失采样点之间的端点**：新页面的区间原先只从整数 `low`/`up` 反推，
   精确物理端点被吸附到采样点（例如 `998.75~1496.25` 变成相邻采样值）。修复：
   `_build_axis_request_params` 把物理区间原样带给新页面；同时明确新页面不继承锁定状态
   （锁定是编辑方式，与「新数据默认未锁定」一致）。回归测试：
   `test_new_page_keeps_sub_sample_endpoints_but_starts_unlocked`。
2. **底栏最小宽度把换行阈值顶死**：`QFrame` 的默认布局约束会把「视图控件与两个分组同排」
   写进控件最小宽度，`_should_wrap` 永远不成立。修复：外层布局设 `SetNoConstraint`，
   用 `minimumSizeHint` 表达换行后的真实最小宽度（两个分组同排 vs 视图控件，取较大者）。
   回归测试：`test_narrow_window_wraps_the_view_controls_to_a_second_row`。
3. **单层切片页保存的位置与切片下标可能不一致**：模型保留精确物理值，切片取最近采样点，
   切页回来时位置会停在未吸附的值上。修复：`_update_slice_source_range` 把保存的区间对齐到
   该采样点。回归测试：`test_saved_interval_is_snapped_to_the_slice_sample`。

## 未执行 / 已知问题

- **`verify_page_shortcuts.py` 有既存偶发失败**：约 1/5~1/8 次运行会报 3 项失败，首个失败是
  「数字键 2 仍切换到左侧第 2 页」。**已在本改动之前（`git stash` 到 HEAD）复现同样比例，
  与本任务无关**，本轮未修复。
- 真实窗口验收覆盖 1550×950 与 1280×800；未逐一覆盖更窄的窗口尺寸。
- 视觉验收仅在本机 NVIDIA 桌面完成，未在无 GPU / 其他分辨率缩放环境复核。
- 打包版（PyInstaller 安装器）未重建，发布走 CI 的标签工作流。
