# EDC 瀑布图插件化迁移交接（2026-10-12）

任务目标：按[当前计划](../plans/edc-waterfall-plugin.md)把 EDC 瀑布图及步长设置迁入
`edc_waterfall` 插件，新增 API 3，并把默认值与“应用当前页”移入插件管理。

## 当前状态：创建入口再次迁移（2026-11-20）

- 原「处理分析」页及「其他积分」下拉已移除。插件就绪时，`edc_waterfall:create` 以「EDC瀑布图」显示在无擦除区域的 X/Y 轴积分或其矩形裁剪页的「右键 → 创建结果页」子菜单中；主页、E 积分、其他页型和多选不显示，未安装、未启用或加载失败时也不显示。
- 来源树与「我的分组」中的页面副本使用同一动作过滤和创建链路。点击后先激活目标父页并重新校验，再冻结空间来源、继承时间区间并调用插件；打开菜单只做轻量检查，取消或失败不建页。
- API 2 分析插件卡片同时迁到保留的「外观」页，仍只在二维结果页显示。新状态不再保存旧下拉选择，恢复旧复现文件时忽略 `combo_other`，不会自动创建结果；已有瀑布页、逐页参数和插件设置继续保留。

### 新入口实测结果（2026-11-20）

- `verify_integration_context_menu.py` 使用合成 **6×7×8×3 动态 NPZ** 在真实 Windows 窗口运行，**22/22 通过**；报告与 14 张截图位于 `.local/outputs/integration-context-menu/`。
- `verify_derived_waterfall.py` **23/23 通过**，输出位于 `.local/outputs/derived-waterfall/`。
- 动态与静态数据的 `verify_time_axis_visibility.py` 均全部通过，输出位于 `.local/outputs/time_axis/`。退出后出现临时扩展 lease 锁文件清理的 **WinError 32** 警告；该清理警告仍需后续排查，不能写成无警告通过。
- 默认 WSe₂ 数据的两个约定路径 `.local/data/WSe2_step.npz` 与 `D:\ARPES数据\npz_files\WSe2_step.npz` 均不存在。因此上述结果不是 WSe₂ 真实实验数据验收，不能替代默认真实数据检查。
- 通用 `smoke_test.py` 在合成数据上完成外观截图后遇到既有 WSe₂ 专用尺寸断言，**不计为通过**。
- 视觉检查仅完成脚本截图抽查；维护者人工视觉验收仍未执行，不能记为通过。
- 全量回归原始结果为 `Ran 1827 tests in 892.646s`，**FAILED (failures=5, errors=3, skipped=4)**；日志为 `.local/logs/integration-context-menu-full-tests.log`（另有 UTF-8 转换副本），独立复跑不改写该原始结果。
  - `tests.core.test_data_trans` 的 3 个 error 均为 GBK 控制台 `UnicodeEncodeError`；设置 `PYTHONIOENCODING=utf-8` 后单独复跑 **3/3 OK**。
  - 全量中的 `tests.rendering.test_refresh_pipeline` `interactive_pair` 与 `tests.ui.test_page_tree` `delete_directly` 两项失败独立复跑均 OK，两个模块合计 **95/95 OK**，属于全量状态／定时波动。
  - 持续剩余 3 项为 `tests.exporting.test_orientation_preview.test_hover_cursor_and_disabled_interaction`，以及 `tests.integration.test_crop_controls` 的 `range_window` / `toggle_buttons` 两个几何断言；涉及文件均不在本改造范围。裁剪两项在本机 `QT_SCALE_FACTOR=2.0` 下已有 [v1.13.0 既有失败记录](v1.13.0-release.md)；方向标一项仅确认独立复跑仍失败，原因尚未完全查明，不归入既有已证实问题。
- `compileall`、`git diff --check`，以及 `edc_waterfall` / `integral_demo` 两个插件的 **1.0.1** 构建均通过。

## 2026-10-12 原始迁移记录

宿主与协议：

- `bandscope/extensions/compat.py`：支持接口集合 `{1, 2, 3}`；新增能力
  `integration_action` / `derived_result` / `plugin_settings`。
- `bandscope/extensions/api.py`：API 3 类型（`IntegrationAction`、`DerivedAxis`、
  `DerivedSource2D`、`DerivedInput2D`、`DerivedWaterfallResult`、`DerivedPage`）、
  `validate_derived_waterfall`、`PluginHostV3` 与 `Plugin` 的新钩子（默认空实现）。
- 新增 `bandscope/extensions/derived_host.py`：冻结来源、只读输入、缓存 + 失败
  回退、建页、参数提交、按插件 ID 的默认值存储（QSettings）。
- `bandscope/extensions/plugin_host.py`：会话接入派生结果与插件设置；
  `capture_analysis_input` 的二维快照改为与画面一致（E 翻转和降序轴一起翻数组
  与坐标）。
- `bandscope/extensions/plugin_dialog.py`：新增「插件设置」页（宿主两个操作 +
  插件面板）；`plugin_host.py` 的 `mount_cards` 跳过派生插件。

主窗口（`bandscope/app/refactored_app.py` 等）：

- 新增 `plugin_derived` 页面类型：刷新接入、时间锁定、数值导出、页面树图标。
- 移除 EDC 专属计算、创建分支、全局步长状态、`瀑布图参数` 组、步长弹窗与右键
  入口；`_waterfall_crop_suffix`/`_resolve_waterfall_axis_info` 整理为通用方法。
- 「其他积分」下拉按稳定动作键分派，插件动作以 `插件ID:动作ID` 并入并按权重排序；
  旧状态按文本恢复，缺失动作回退到可用内置项（不再用旧下标）。
- E 翻转口径：瀑布页为定向翻转（能量轴与采样一起反序），屏幕/出版图/数值导出
  在同一 E 坐标下一致；`crop_model._orient` 同步。

插件与发布：

- `plugins/edc_waterfall/`（API 3）：采样、归一化、自动步长、自动标题、设置面板。
- `.github/workflows/release.yml`：新增 `edc_waterfall` 构建步骤。
- 验收脚本：新增 `scripts/validation/verify_derived_waterfall.py`；
  `export_acceptance.py`、`verify_1d_export_style.py` 改走插件创建路径，并修掉
  `setCurrentText` 在非可编辑下拉上改写当前项文本的坑（改为按下标选择）。
- 文档：`docs/development/plugins.md`（API 3 与能力表）、README 的插件与瀑布图
  章节、`docs/architecture/repository-map.md`。

## 验证了什么

- 全量回归：`python -m unittest discover -s tests -t .` → 1123 项，1120 通过、
  1 跳过、2 失败；两项失败在干净工作区同样失败（本机裁剪窗口几何断言），与本次
  改动无关。
- 新增覆盖：插件业务 16 项、宿主协调 17 项、插件设置对话框 3 项、端到端 11 项
  （真实构建+安装事务、X/Y 来源、裁剪来源、时间积分来源、越界拒绝、E 翻转一致
  性、用户改名、默认值隔离、下拉回退）。
- 插件构建：`build_plugin.py edc_waterfall` 通过（1.0.0，`sha256 70a4477e…`）。
- 真实数据（本机 `D:\ARPES_3Dmap\npzfiles\mose2.npz`，默认 `.local/data/WSe2_step.npz`
  缺失）：`verify_derived_waterfall.py` 23/23 通过（含真实窗口里的「插件设置」面板、
  两个按钮与默认值生效）；`export_acceptance.py` 全项通过（瀑布页渲染与导出含在内）。

## 未执行

- 冻结包（CPU/NVIDIA）加载与安装器端到端、官方目录在线安装链路（需要发布密钥
  与 Release），按既有交接在发布流程补做；
- 1D（能量 DOS / 单条 EDC 曲线）与 3D 图片导出的 E 翻转一致性：仍按
  [E 翻转一致性验收](e-axis-consistency-acceptance.md) 记录为待修复，不在本轮范围；
- 截图人工复核：本轮只做了脚本级的屏幕/出版图数值一致性检查与样例出图，
  未做逐张人工视觉复核（建议发布前对
  `.local/outputs/derived-waterfall/` 与 `.local/outputs/publication_export/` 的
  瀑布样例各看一遍）。
