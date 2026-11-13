# 更新结果复现配置，并补齐 Blender 配套配置

- 日期：2026-11-12。
- 状态：源码、自动化与真实窗口／Blender 往返验收已完成；维护者原生文件对话框人工验收待执行，本轮不涉及发布。
- 负责范围：`bandscope/core/` 的复现数据契约，`bandscope/app/` 的捕获与多源恢复，`bandscope/exporting/` 的 Blender 配对导出、原位更新和界面，以及对应测试与说明。
- 共享接入点：主窗口、数据源事务和页面状态由集成任务统一协调；保留工作区已有显示与 Blender 改动，不覆盖其他任务成果。
- 状态主记录：本文件维护本次配置补齐工作的状态；既有图片复现行为与验收背景见 [视图复现文件](view-reproducibility.md)，不复制其历史验收流水。
- 交付物：图片配置缺项修复、Blender 配套复现配置、主动选源的多页恢复、事务与兼容性测试、更新后的用户说明；不涉及发布。

## 目标与实施结果

已补齐图片 schema 2 的派生页显示与质量捕获，并新增 Blender 配套复现文件。图片 schema 1 迁移和 Blender 场景 schema 11 均保持兼容。

按已确认的选择实施：默认勾选、可关闭；恢复视图与 Blender 导出设置；同一原数据源的多个页面恢复到同一个新数据源下。每次加载配置仍由用户主动选择原始 NPZ。

## 配置与状态捕获

- 保留图片 `ViewRecipeDocument` schema 2，继续兼容 schema 1。修复插件派生页捕获：保留成功结果的科学参数，同时保存当前控件、局部增强、独立显示和渲染质量。
- 修正共同祖先的旋转记录：默认环境中的旋转角取自正在冻结的页面，不取当前活动页面；显式保存的插件计算环境保持原样。
- 新增纯数据契约 `BlenderRecipeDocument`，外层格式为 `bandscope.blender-recipe`、版本 1，文件名为 `名称.blend.bsview.json`。内部复用现有单页配方，保存来源会话分组、条目顺序、逐页状态和 Blender 导出设置。
- 保存五项预设、数据体外观、材质与布局等实际导出设置；不保存原始数据路径、Blender 安装路径或原始数据数组。现有 Blender 场景 schema 11 保持不变。

## 导出与工程更新

- Blender 面板增加“同时保存复现文件”，默认开启，在当前窗口运行期间独立记忆，不写入全局偏好。任务开始时冻结开关、页面配方和导出设置。
- 开启时，任何页面缺少有效配方都在启动 Blender 前报错并指出页面；工程、资源目录和配置统一暂存、校验、提交，失败或取消整体回滚。
- 覆盖检查包含同名配置。关闭配置导出后覆盖旧工程时，在覆盖提示中明确旧配置也会移除，并纳入同一回滚事务，避免留下过期配置。
- 原位更新已有配套配置时，保留原科学配方，另存逐条目的显示覆写；同步记录被移除的局部增强和插件透明度倍率，恢复时不得重新启用。配置纳入并发变化检查，与资源更新一起提交；保持现有 `.blend` 不被原位更新改写的约定。
- 无复现配置的旧工程继续支持现有编辑功能；提示需从原始数据重新导出才能获得完整复现配置。

## 加载与主动选源

- 沿用“加载数据”的 `.bsview.json` 入口，按文件内 `format` 分派图片或 Blender 复现。Blender 配置可独立于 `.blend` 和资源目录加载。
- 每个原来源会话组主动选择一次 NPZ，同组多页共用该次选择；同一文件曾被加载成两个独立会话时仍分别选择。禁止利用旧路径、已打开数据或历史选择跳过选源，继续验证 SHA-256 和数据布局。
- 全部来源完成选择、校验和预计算后再整体新增工作区。任一来源取消、失败或提交失败，撤销本次全部新增内容，保留原有数据与页面。
- 同组共同祖先合并一次，逐页恢复相机、显示、质量和效果，重新分配运行时页面 ID。源级去噪与数据域必须一致，旋转按页保留；重复页面记录出现真实冲突时明确拒绝，要求重新冻结导出。
- 成功后选中首个导出页面，打开已填入恢复设置的 Blender 导出面板，并勾选原导出页面。恢复设置仅属于本次草稿，不自动导出或覆盖全局偏好。

## 验证与交付

- 图片回归：插件计算后修改显示与质量再往返；旧格式迁移；冻结预览；每次强制选源。
- Blender 回归：单页、同源多页、不同旋转角、多源、同文件独立会话；逐页参数、数值和导出设置往返一致。
- 事务回归：第二来源取消或摘要不符、配置写入失败、覆盖失败、更新失败及并发修改，均不留下部分结果；关闭保存和旧工程兼容行为符合上述约定。
- 现有复现相关 **26 项测试已通过**；实施后运行受影响模块测试及 `python -m unittest discover -s tests -t . -v`。真实窗口验收使用默认 WSe₂ 数据，另行记录选源交互与 Blender 往返结果。
- 保留工作区现有改动，更新本状态主记录、[复现用户说明](../development/view-recipes.md) 及 [Blender 说明](../development/blender-export.md)；既有复现主记录仅补充本计划的路由与必要行为说明，不重复维护本事项状态。本轮不涉及发布。

### 已执行的规划基线检查

```powershell
.\.venv\Scripts\python.exe -m unittest tests.core.test_view_recipe tests.core.test_view_recipe_display tests.exporting.test_recipe_export tests.exporting.test_recipe_export_ui tests.integration.test_view_recipe_roundtrip -q
```

2026-11-12：26 项通过，耗时 20.427 秒，日志为 `.local/logs/view-recipe-plan-baseline.log`。首次沙箱内运行因临时目录不可访问而出现 `PermissionError`，在正常权限环境重跑后通过；该结果仅为现有功能基线，不代表本计划已实施或新增验收通过。

### 实施与验收记录

- 实现完成：`core/blender_recipe.py` 提供外层版本 1 契约；`app/blender_recipe_integration.py` 负责共同祖先合并、逐组选源预检和整体事务；导出面板、配对提交、工程回读与原位更新均已接入。图片派生页保留成功科学结果并补齐当前显示/质量，祖先与目标页分别记录旋转。
- 新增 21 项契约、恢复与文件事务测试，并扩充面板开关/覆盖提示及插件派生页回归。覆盖单页、同源多页、不同旋转、多源、同文件独立会话、显示覆写不重启已移除效果，以及第二来源取消/摘要不符、提交失败、配置写入/覆盖/更新失败和并发修改回滚。
- 全量回归：`./.venv/Scripts/python.exe -X faulthandler -m unittest discover -s tests -t . -v`，**1668 项全部通过**，耗时 111.827 秒；日志 `.local/logs/export-recipe-blender-sidecar-full.log`（UTF-8 复核副本为 `export-recipe-blender-sidecar-full-utf8.log`）。`python -m compileall -q bandscope plugins scripts tests start.py` 与 `git diff --check` 通过。
- 真实验收：`./.venv/Scripts/python.exe -X faulthandler scripts/validation/blender_recipe_acceptance.py --blender /d/blender/blender.exe`，使用默认 `.local/data/WSe2_step.npz`（SHA-256 `ade124107d04aff247d9db3fc1dbf6936b99625115246f5e9ac042f355baa3e3`）和 Blender 5.2.3 LTS，29 项检查全部通过。覆盖真实同源两页导出、配套文件、真实工程回读、显示覆写原位更新、`.blend` 摘要保持不变、无工程/资产目录的独立配置恢复、同组仅一次选源、逐页数值与相机一致及导出草稿恢复。报告和截图位于 `.local/outputs/blender-recipe-acceptance/`，日志为 `.local/logs/blender-recipe-acceptance.log`。
- 图像复核：已查看导出面板、恢复后面板与原生 VTK 场景，确认两页选择、预设开关和可见体数据。Qt 主窗口抓图仍可能显示黑色 VTK 区域，不能代替原生场景图；验收脚本另存 `restored-3d-scene.png` 并执行非空像素检查。
- 未执行：维护者对原生文件对话框、主动选源、覆盖确认和日常鼠标交互的人工验收；冻结包与安装器验收。本轮选源由真实窗口中的受控回调提供，不能写成原生对话框人工通过；不涉及发布。
- 下一步：维护者完成上述人工验收。当前计划索引已移出本实施事项，剩余事项由[交接索引](../handoffs/README.md)路由到本记录。
