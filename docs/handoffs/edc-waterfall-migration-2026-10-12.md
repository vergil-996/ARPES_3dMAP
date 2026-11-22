# EDC 瀑布图插件化迁移交接（2026-10-12）

任务目标：按[当前计划](../plans/edc-waterfall-plugin.md)把 EDC 瀑布图及步长设置迁入
`edc_waterfall` 插件，新增 API 3，并把默认值与“应用当前页”移入插件管理。

## 当前状态：入口改为视图右键「处理分析」（2026-11-21，已随 v1.22.3 发布 2026-11-22）

- 创建入口从页面树移到视图区域：切到目标父页后，在 2D／3D 画布右键 →「处理分析」；插件就绪时 `edc_waterfall:create` 以「EDC瀑布图」显示在无擦除区域的 X/Y 轴积分或其矩形裁剪页中，主页、E 积分、其他页型不显示，未安装、未启用或加载失败时也不显示。
- 页面树不再提供「创建结果页」子菜单；`PageTreePanel` / `ResultWorkspace` 的 provider 转发与 `page_create_requested` 信号已移除。视图右键只对当前活动页提供动作，打开菜单不切页；点击后由 `_on_page_create_requested()` 复核并冻结来源。
- API 2 分析插件卡片继续挂在外观页，仍只在二维结果页显示。新状态不再保存旧下拉选择，恢复旧复现文件时忽略 `combo_other`，不会自动创建结果；已有瀑布页、逐页参数和插件设置继续保留。

### 2026-11-21 实施与验证

- 主窗口新增 `_append_analysis_actions(menu)`：读取当前页，调用 `_integration_actions_for_parent()` 取过滤排序后的动作，挂载「处理分析」子菜单；每个 `QAction` 带稳定键 `data()` 并直接连到 `_on_page_create_requested()`。2D 右键、3D 右键以及裁剪模式下的紧凑右键菜单（`_show_axis_title_menu`）都接入同一入口。
- `PageTreePanel` / `ResultWorkspace` 删除 `page_create_requested` 信号、provider 字段与 `set_page_create_action_provider()` 转发；来源树与分组副本右键只保留改名、复制、删除等页面操作。
- 更新 `tests/ui/test_page_tree.py`（断言页面树无创建子菜单）与 `tests/integration/test_integration_context_menu.py`（覆盖处理分析子菜单、稳定键分派、非法页隐藏、插件动作排序、2D/3D/裁剪模式接线）。`scripts/validation/verify_integration_context_menu.py` 改为激活父页后右键 2D／3D 视图，并检查页面树入口已移除；`export_acceptance.py`、`verify_2d_aspect.py`、`verify_derived_waterfall.py` 继续复用 `create_from_page_menu()`，无需改调用方。
- 文档同步 README、插件开发指南、测试指南、EDC 插件 README/入口注释与相关计划的状态说明。

验证结果：

- 聚焦回归（`tests.ui.test_page_tree`、`tests.integration.test_integration_context_menu`、`test_curve_log`、`test_plugin_derived_flow`、`test_crop_controls`、`test_plugin_management_entry`、`test_plugin_analysis_flow`、`tests.extensions.test_derived_host`、`test_view_recipe_roundtrip`）：**177 项通过**。
- 全量 `python -m unittest discover -s tests -t .`：**1943 项通过**（2026-11-21，163.854 秒；离屏平台、`PYTHONIOENCODING=utf-8`，`os.mkdir` 临时放宽以避开本机沙箱的 owner-only 临时目录 ACL）。
- 真实窗口 `verify_integration_context_menu.py`（合成 6×7×8×3 动态 NPZ，隔离扩展根中构建并安装 `edc_waterfall`）：**25/25 通过**。2D 主页右键出现「处理分析 → 切片内强度积分／能级态密度／二阶导」，X 积分页出现「处理分析 → 切片内强度积分／能级态密度／EDC瀑布图／二阶导」，E 裁剪页提供「单条 EDC 曲线」，非法结果页与页面树均无入口；所有右键均保持当前页不变，创建后新页选中。报告与截图写在本机临时输出目录，验收后已清理，未提交。
- 默认真实数据 `.local/data/WSe2_step.npz`（150×150×200×25，时间轴 −300–1000 fs）上的同一脚本：**25/25 通过**，覆盖深浅主题、X/E 积分父页、二阶导与 EDC／瀑布图创建；报告与截图写在本机临时输出目录，验收后已清理，未提交。未做人工视觉逐张复核。
- **发布**：入口与 `edc_waterfall` 1.0.2 随 v1.22.3 发布到 GitHub 与 MatElab（2026-11-22），最终树上重跑真实窗口 **25/25 通过**。v1.22.2 的标签因插件 1.0.1 内容变更未升版本、在目录聚合阶段失败而未产出 Release，记录见 [v1.22.3](../archive/releases/v1.22.3.md)。


### 2026-11-20 记录：入口曾迁至页面树

- 原「处理分析」页及「其他积分」下拉已移除。插件动作以「EDC瀑布图」显示在来源树或「我的分组」页面副本的「右键 → 创建结果页」子菜单中；该入口已在 2026-11-21 移除。
- 来源树与「我的分组」中的页面副本使用同一动作过滤和创建链路。点击后先激活目标父页并重新校验，再冻结空间来源、继承时间区间并调用插件；打开菜单只做轻量检查，取消或失败不建页。
- 当日实测与未执行项保留在下一节，作为过渡入口的历史证据。

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
