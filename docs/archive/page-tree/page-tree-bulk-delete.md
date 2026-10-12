# 页面直接删除与树内批量删除

- 状态：已实施、已验证（2026-10-12），本记录归档。
- 目标：移除删除页面二次确认；左侧树支持框选及 Ctrl / Shift 多选，通过 Delete 或右键批量删除。
- 负责范围：集成任务维护 `bandscope/ui/result_workspace.py`、`bandscope/app/refactored_app.py` 和本说明；页面树任务维护 `bandscope/ui/page_tree.py` 与 UI 测试；验证任务维护页面管理集成测试和真实窗口验收脚本。独立审查任务仅检查删除与宿主清理行为。
- 共享文件：主窗口与工作区由集成任务统一修改；验收共用参数解析增加可选 `default_data` 参数供页面树脚本使用。插件 API、依赖与打包配置未改。工作区起始状态干净；任务期间出现的裁剪 E 轴改动属于其他任务，本轮保留且未修改。

## 接入与行为

- 页面记录、来源关系与删除集合归工作区；树只维护显示与选择，并提交页面 ID 列表。
- `delete_page(page_id)` 与 `delete_pages(page_ids)` 均直接执行。移除宿主确认处理器与弹窗实现。
- 框选及修饰键多选不切换画布；普通点击名称仍切页，名称拖拽仍用于同级排序。
- 删除父页会包含所有派生页，含折叠或搜索隐藏的后代；重叠选择去重，先子后父通知宿主清理。
- 主页、固定工具页和包含受保护后代的分支不能删除；批量请求中的其他可删除分支照常处理。
- 删除其他分支保持当前页面；删除集合包含当前页时只切换一次，回退到最近存活来源页或主页。
- 搜索和折叠会清理不可见行的残余选择；导航刷新与后台新增结果保留原选择、焦点和搜索词，避免后台结果抢走删除目标。

## 验证结果

- 测试环境：`.local/venv-ci-probe/Scripts/python.exe`（Python 3.12，含 Qt / VTK / SiliconUI）；系统默认 Python 缺少 PyQt5。
- 页面树 UI 69 项、页面管理集成 41 项通过；覆盖实际鼠标框选、Ctrl / Shift、多选右键删除、同级排序、保护分支、后代去重、宿主缓存与任务清理、单次刷新/回退、后台新增保留选择。
- 最终完整命令：`.local/venv-ci-probe/Scripts/python.exe -m unittest discover -s tests -t . -v`，运行 1066 项，1065 项通过、1 项 CUDA 环境测试跳过；日志 `.local/outputs/page-bulk-delete-unittest.log`。Windows 沙箱内临时目录不可访问，最终验证在正常临时目录权限下执行，未调整测试隔离或断言。
- 语法检查：`.local/venv-ci-probe/Scripts/python.exe -m compileall -q bandscope plugins scripts tests start.py`；`git diff --check` 通过。
- 真实窗口命令：`QT_QPA_PLATFORM=windows` 下运行 `.local/venv-ci-probe/Scripts/python.exe scripts/validation/verify_page_tree.py --dynamic --output-dir .local/outputs/page-bulk-delete-visual`，59 项检查全部通过。
- 数据：默认 `.local/data/WSe2_step.npz`，WSe₂ 时间分辨 ARPES，150×150×200×25；Windows 原生 Qt 平台，隔离设置与扩展目录。实际验证页眉直接删除、最近来源页回退、树空白处拖框选中多个分支及后代、Delete 批删、保护主页、通知去重和顺序；检查了框选/选中截图。
- 图像与日志只保存至 `.local/outputs/page-bulk-delete-visual/` 和 `.local/outputs/page-bulk-delete-visual.log`，不提交。脚本结束走宿主 `closeEvent`，释放临时插件登记表锁。
- 未执行：1D 页面实际窗口切换、发行包构建及安装检查；本轮未修改构建、插件加载或资源路径。本次页面交互需求无未完成项。
