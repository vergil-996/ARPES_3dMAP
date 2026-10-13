# EDC 瀑布图插件化

日期：2026-10-12（2026-10-13 随 v1.13.0 发布）。状态：**已实施并发布**；自动化回归、
插件构建与真实数据验收已完成，冻结包/安装器端到端验收待发布流程执行。本轮负责
范围：宿主 API 3、主窗口、插件管理、刷新与发布链路（集成任务），以及 EDC 业务与
插件测试（插件任务）。

## 目标与行为

首轮迁移 EDC 瀑布图及其步长设置，插件标识为 `edc_waterfall`。

- 保留“其他积分”中的原入口，由已加载插件动态注册；插件未加载时隐藏。
- 支持 X/Y 积分页及其矩形裁剪结果，创建后进入瀑布页。
- 固定创建时的时间帧或时间积分区间、积分范围、裁剪范围和数据域；全局去噪、
  当前页旋转及本页步长变化仍触发重算。
- 插件管理提供“保存为新建默认值”和“应用当前瀑布图”两个独立操作，其他已有页保留各自参数。

## 宿主接口与实现边界

新增 API 3，继续支持 API 1/2，新增插件钩子提供空默认实现。

| 接口 | 职责 |
|---|---|
| `integration_actions()` / `invoke_action()` | 注册和执行下拉动作，以插件 ID、动作 ID 分派 |
| `DerivedSource2D` / `DerivedInput2D` | 宿主保存冻结来源，并提供只读二维数组、坐标、单位、完整轴信息及来源元数据 |
| `compute_derived_result()` / `DerivedWaterfallResult` | 插件计算并返回原始曲线、归一化曲线、坐标、采样身份、偏移及标题 |
| `create_settings_panel()`、默认值读写、当前结果查询与更新 | 插件提供设置界面，宿主保存默认值并校验结果页归属 |

- 新结果使用通用 `plugin_derived` 页面类型。宿主按注册能力处理，不识别 EDC 插件 ID 或中文标题。
- 插件负责采样、归一化、参数校验及自动标题；宿主负责数据准备、页面状态、绘图、裁剪、出版和数值导出。
- 沿用现有刷新流程：体数据准备仍异步执行，二维采样及归一化在数据就绪后同步计算。缓存按来源、数据修订、旋转和页面参数失效；其他页激活时重算。
- V3 输入和结果采用原始坐标排列，显示、出版及数值导出由宿主统一处理 E 翻转一次。同时修复 API 2 二维快照与画面不一致的问题。
- 创建和参数更新先验证计算结果，再提交页面状态。失败时保留上次成功结果并明确标注；用户改名不被自动标题覆盖。

## 实施记录（2026-10-12）

代码落点：

- 协议与兼容：`bandscope/extensions/api.py`（API 3 类型、`PluginHostV3`、
  `Plugin.integration_actions/compute_derived_result/create_settings_panel/
  export_settings/restore_settings`、`validate_derived_waterfall`）、
  `bandscope/extensions/compat.py`（支持集合补 `3` 与三项新能力）。
- 宿主协调：新增 `bandscope/extensions/derived_host.py`（冻结来源、只读输入、
  缓存与失败回退、建页、参数提交、按插件 ID 的默认值存储）；`plugin_host.py`
  增加 `PluginSession` 的派生结果与插件设置入口、`_HostBridge` v3 方法，并修复
  `capture_analysis_input` 的二维快照方向（与画面一致）。
- 主窗口：`plugin_derived` 页面类型接入刷新、时间锁定、导出与页面树图标；
  下拉按稳定动作键分派（`builtin:*` 与 `插件ID:动作ID`）并支持缺失动作回退；
  移除 EDC 专属计算、创建分支、全局步长状态、参数组与步长弹窗（“复用中的裁剪
  标题与轴间距辅助”整理为通用方法）。
- 插件：`plugins/edc_waterfall/`（`edc.py` 纯数值 + `entry.py` + `settings_panel.py`），
  `plugin.json` 声明 API 3 与 `integration_action` / `derived_result` / `plugin_settings`。
- 插件管理：`plugin_dialog.py` 新增通用「插件设置」页，两个操作由宿主实现，
  面板由插件提供。
- 构建与发布：`.github/workflows/release.yml` 增加 `edc_waterfall` 构建；基础安装包
  的 `plugins` 源目录排除规则本就按整棵目录生效，无需改动。
- 验收脚本：`scripts/validation/verify_derived_waterfall.py`（新）、
  `export_acceptance.py` 与 `verify_1d_export_style.py` 改为经插件路径创建瀑布页。

E 翻转口径（本轮的明确决定）：瀑布页的 E 翻转是**定向翻转**——能量轴与采样一起
反序，同一个 E 坐标在翻转前后对应同一强度；屏幕与出版图仍按升序绘制，数值导出
保持原始坐标排列，因此“屏幕 / 出版图 / 数值导出在相同 E 坐标下一致”。1D（能量
DOS / 单条 EDC 曲线）与 3D 图片导出的同类问题不在本轮范围，仍按
[E 翻转一致性验收](../handoffs/e-axis-consistency-acceptance.md) 记录待修复。

## 验证结果

自动化（本机 `.venv`，Python 3.14）：

- `python -m unittest discover -s tests -t .`：**1123 项，1120 通过 + 2 失败 +
  1 跳过**；2 项失败为 `tests/integration/test_crop_controls.py` 的裁剪窗口几何
  断言，在未改动的干净工作区同样失败（本机离屏/DPR 环境问题），与本迁移无关。
- 新增测试：`tests/plugins/edc_waterfall/`（16 项）、`tests/extensions/test_derived_host.py`
  （17 项）、`tests/extensions/test_plugin_settings_dialog.py`（3 项）、
  `tests/integration/test_plugin_derived_flow.py`（11 项，含真实构建/安装事务）。
- 插件构建：`python scripts/release/build_plugin.py edc_waterfall --output-dir
  .local/outputs/v1.13.0` → `BandScope-edc_waterfall-1.0.0.bsplugin`
  （sha256 `367dd96a…`，CRLF 工作区与 CI 的 checkout 一致；`requires_app` 随 1.13.0
  定为 `>=1.13.0,<2.0.0`）。

真实数据（`D:\ARPES_3Dmap\npzfiles\mose2.npz`，200×200×800 静态谱；本机默认的
`.local/data/WSe2_step.npz` 缺失，验收按用户指定的数据执行）：

- `scripts/validation/verify_derived_waterfall.py`：**23/23 项通过**，含下拉注册、
  越界步长拒绝、冻结来源字段、采样独立复算、屏幕/出版图/数值导出在相同 E 坐标
  一致（最大差 2.97e-08）、应用当前页与默认值隔离，以及真实窗口里「插件设置」
  面板、两个按钮与“新建页立即使用保存的默认值”。截图与数值证据在
  `.local/outputs/derived-waterfall/`。
- `scripts/validation/export_acceptance.py`：全视图族导出验收通过，其中
  `plugin_derived 瀑布页渲染/导出` 通过（`page_waterfall.png`）。

未执行：冻结包（CPU/NVIDIA）加载与安装器端到端验收、官方目录在线安装链路
（需要发布密钥与 Release）；这两项按既有交接在发布流程里补做。

## 下一步

1. 发布流程里执行插件签名、目录生成与冻结包加载验证；
2. 需要时把 1D/3D 的 E 翻转一致性问题按既有验收记录单独修复；
3. 完成后连同材料归档到 `docs/archive/`。
