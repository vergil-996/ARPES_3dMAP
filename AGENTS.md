# BandScope 协作约定

## 先读什么

- [目录与模块职责](docs/architecture/repository-map.md)：确定代码放置位置。
- [开发与测试](docs/development/testing.md)：安装环境、运行检查。
- [插件开发](docs/development/plugins.md)：宿主公开接口及插件边界。
- [当前交接](docs/handoffs/README.md)：尚待确认的事项。

## 文件放置

- 主程序放 `bandscope/`；独立功能插件放 `plugins/<plugin_id>/`。
- 自动化测试统一放 `tests/`，按责任模块分类；插件测试放 `tests/plugins/<plugin_id>/`。
- 发布工具放 `scripts/release/`；视觉验收放 `scripts/validation/`；排查工具放 `scripts/diagnostics/`。
- 当前计划放 `docs/plans/`，未完成交接放 `docs/handoffs/`，已结束的记录连同附件放 `docs/archive/<主题>/`。
- 实验数据放 `.local/data/`，临时输出放 `.local/outputs/`，日志放 `.local/logs/`。这些内容不提交。
- 根目录不新增功能源码或日期命名的 Markdown。三个兼容模块只用于旧插件导入。

## 多 agent 分工

1. 开始任务时说明目标、认领目录、需要改动的共享文件、验证命令和交付物。先检查工作区已有改动，不覆盖其他任务成果。
2. 同一轮协作由集成任务协调主窗口、插件 API、依赖列表、打包配置和 CI。其他任务需要改共享文件时，先向集成任务交代接口需求和具体变更。
3. 跨模块任务先确定输入、输出、状态归属和接入位置，再分别实现。插件通过公开接口请求宿主操作，不读取主窗口私有字段。
4. 每项功能只维护一份当前计划或交接，写明状态、负责范围、验收结果和下一步。完成后归档；历史文档不是当前执行指令。
5. 目录迁移和全局导入调整由集成任务统一完成，不与同一批文件上的功能改动并行。

## 交付检查

- 新代码使用 `bandscope.*` 导入；不要通过添加功能目录到 `sys.path` 解决包依赖。
- 新测试不得依赖本机实验文件、用户注册表设置或已安装插件。复用 `tests/support/` 中的合成数据和隔离辅助。
- 运行受影响模块测试，集成前运行 `python -m unittest discover -s tests -t . -v`。
- 修改构建、插件加载或资源路径后，补做相应打包和启动检查。
- 交接说明改了什么、验证了什么、哪些检查未执行及原因。不要把未完成视觉验收写成已通过。
- 提交前检查 `git status --short`，只包含本任务范围的代码、测试和说明；不提交 `.local/`、虚拟环境和构建产物。
