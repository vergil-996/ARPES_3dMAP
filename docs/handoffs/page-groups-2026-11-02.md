# 我的分组与页面副本

日期：2026-11-02。状态：源码、自动化与真实窗口脚本通过，已随 **v1.18.1** 发布到 GitHub 与 MatElab（2026-11-02）；维护者人工交互与冻结包验收未执行。

## 目标与约束

- 分组是应用级区域，不是数据源会话状态的一部分：切源时分组与副本保持显示，`export_source_state` / `install_source_state` / `detach_source_state` 只换来源树。
- 拖放与 `Ctrl+C` / `Ctrl+V` 都只复制、不移动来源页面；副本是深拷贝，带新 `page_id`，标题追加「 副本」并按已有标题去重。来源树内的拖动仍是同级排序。
- 副本记录所属数据源。激活跨源副本先切回所属数据源，再把目标页写进待安装状态并在同一次安装中选中，避免多刷新一次或在不匹配的数据上刷新。
- 关闭数据源只删除它自己的副本；删除分组删除组内副本、不删除原页面；删除原页面不影响副本。
- 未修改插件 API、依赖与打包配置；主窗口只新增 `group_page_activation_requested` 的一处连接。

## 相关文件

- `bandscope/ui/result_workspace.py`：`PageGroup`、`AnalysisPageSpec.group_id`、分组新建 / 改名 / 删除、`copy_page_to_group` 深拷贝、来源状态只装来源树、跨源副本激活信号。
- `bandscope/ui/page_tree.py`：`PageGroupView` 分组区域与投放、复制粘贴剪贴板、分组树重建与选中同步、来源树过滤分组副本、`InternalMove` 后延迟重发排序请求。
- `bandscope/app/source_workspace.py`：页面归属注册排除分组副本、关闭源时清理该源副本、跨源副本激活（先切源再选中）。
- `bandscope/app/refactored_app.py`：连接 `group_page_activation_requested`。
- `tests/ui/test_page_tree.py`、`tests/integration/test_source_workspaces.py`：合成数据覆盖分组、复制粘贴、拖放、排序时序与跨源归属。
- `scripts/validation/verify_page_groups.py`：真实窗口脚本，默认 `.local/data/WSe2_step.npz`。

## 验证

- 全量 `python -m unittest discover -s tests -t . -v`：1470 项通过（2026-11-02，82.741 秒），日志 `.local/logs/release-1.18.1-full.log`。
- 真实窗口（Windows、NVIDIA OpenGL）：`scripts/validation/verify_page_groups.py` 38 项通过，日志 `.local/logs/page-groups-1.18.1-native.log`，截图 `.local/outputs/page-groups-release/`；副本页 3D 场景暗像素 261023，与原页一致。
- `check_release_version.py v1.18.1`、`verify_plugin_release_flow.py`、`compileall` 通过。
- Release CI（run 41415852485，21m46s）全部成功；`check_for_update` 从 1.17.4 发现 1.18.1，两个安装包按 `.sha256` 边车完整下载校验一致（CPU `400cae82…`，152,475,883 B；NVIDIA `1f98ace2…`，1,198,996,944 B），插件包与 `plugins-index.json` 验签通过；MatElab 记录「BandScope v1.18.1」（uid `bandscope-release-1.18.1`）已创建。
- 既有脚本 `scripts/validation/verify_page_tree.py` 在本次运行出现 4 项失败，原因是它自 v1.16.0 动态主页 ID 之后仍按字面 `home` 查找主页（脚本最后更新于 v1.12.5），并非本版回归；该脚本本次未修改，后续补验页面树时需先改为读取 `home_page_id`。

## 已知边界与下一步

- 分组与副本只在本次运行内保留，重启后清空；若要持久化，需要在 QSettings 或复现文件中新增格式与迁移，不能直接把分组塞进数据源状态包。
- 在分组副本上保存复现文件的行为未专门验证（副本会被当作普通页面导出，恢复后 `group_id` 清空）。
- 维护者人工验收：拖动到分组的手感、窄栏与多分组下的布局、真实数据跨源激活；冻结安装包与安装器验收。
