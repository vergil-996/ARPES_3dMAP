# BandScope 协作约定

## 阅读与检索

- 先读本文件及目标路径上适用的各级 `AGENTS.md`；其余文档按任务选读，不默认整套加载。
- 新增文件或跨模块改动读 [模块地图](docs/architecture/repository-map.md)；安装环境、运行测试或窗口验收读 [开发与测试](docs/development/testing.md)。
- 插件或宿主接口改动读 [插件开发](docs/development/plugins.md)；构建、签名或发布读 [构建与发布](docs/development/releasing.md)。
- 接续待实施工作从 [计划索引](docs/plans/README.md) 选对应计划；接续开放问题或验收从 [交接索引](docs/handoffs/README.md) 选状态主记录。只读本任务相关项。
- 默认先在责任源码、对应测试和相关现行文档中检索；历史归档、迁移清单、`.local/`、依赖和构建产物仅在用户指定或追溯、验证需要时按需读取。
- 大文件先定位相关段落；日志先读摘要和失败段，完整输出写入 `.local/logs/` 或 `.local/outputs/`。不反复输出整份日志、全目录清单或已读历史全文；访问失败标为未核实。

## 文件放置

- 主程序放 `bandscope/`；独立功能插件放 `plugins/<plugin_id>/`。
- 自动化测试统一放 `tests/`，按责任模块分类；插件测试放 `tests/plugins/<plugin_id>/`。
- 发布工具放 `scripts/release/`；视觉验收放 `scripts/validation/`；排查工具放 `scripts/diagnostics/`。
- 当前计划放 `docs/plans/`，未完成交接放 `docs/handoffs/`，已结束的记录连同附件放 `docs/archive/<主题>/`。
- 实验数据放 `.local/data/`，临时输出放 `.local/outputs/`，日志放 `.local/logs/`。这些内容不提交。
- 默认真实验证数据：`.local/data/WSe2_step.npz`（WSe₂ 时间分辨 ARPES，150×150×200×25，kx/ky 物理轴齐全、E 轴为索引坐标；源自 `D:\ARPES数据\npz_files\WSe2_step.npz`）。`scripts/validation/` 下需要真实数据的手动与视觉验收脚本默认使用它；自动化测试仍一律使用合成数据，不得依赖该文件。
- 根目录不新增功能源码或日期命名的 Markdown。三个兼容模块只用于旧插件导入。

## 多 agent 分工

1. 开始任务时说明目标、认领目录、需要改动的共享文件、验证命令和交付物。先检查工作区已有改动，不覆盖其他任务成果。
2. 同一轮协作由集成任务协调主窗口、插件 API、依赖列表、打包配置和 CI。其他任务需要改共享文件时，先向集成任务交代接口需求和具体变更。
3. 跨模块任务先确定输入、输出、状态归属和接入位置，再分别实现。插件通过公开接口请求宿主操作，不读取主窗口私有字段。
4. 每个开放事项只维护一份状态主记录，注明目标、当前状态、负责范围、已验收内容和下一步；索引只维护事项路由，不复制实现流水与回归计数。完成后连同附件归档；源码完成但人工或发行包验收未完成时，保留对应开放事项。
5. 目录迁移和全局导入调整由集成任务统一完成，不与同一批文件上的功能改动并行。

## 发布流程

1. 改版本号（引用点见 [构建与发布](docs/development/releasing.md)）、写 README 更新段落，跑全量回归后提交；推送 `vX.Y.Z` 标签触发 CI。GitHub 推送与标签需要代理开启；本机 `git` 不读系统代理，需显式 `-c http.proxy=… -c https.proxy=…`。
2. CI（test → 插件 → 构建 → release）产出两个签名安装包及 `.sha256`、插件包和 `plugins-index.json`。**对外发布只用 CI 产物**，本机打包（无签名、哈希不一致）仅限开发验证；GitHub 与 MatElab 两个源发同一份文件。
3. 下载安装包并按 `.sha256` 边车校验后再分发。
4. **等待维护者关闭代理**：MatElab 走所内直连，代理开启时无法访问。此步骤必须显式等待，不能在代理仍开启时尝试发布，也不能自行切换代理。
5. MatElab 发布：`python scripts/release/publish_matelab.py --version X.Y.Z --installer CPU=… --installer NVIDIA=… --notes …`。脚本走「分片上传 → 创建空记录 → update 搭模块」，记录含「manifest」（富文本 JSON：version/notes/published_at）与「安装包」（文件集）两个模块，标题固定 `BandScope vX.Y.Z`；不需要模板，不需要手工建记录结构。实测细节（文件名改写、delModule 参数形式、慢网断点续传）见 releasing.md「实测注意」。
6. **GitHub** 发布后用客户端逻辑只读验证：`check_for_update` 从上一版本能发现新版本，文件名/大小/SHA-256 与 CI 一致；抽样或完整下载校验通过才算发出。**MatElab 不做回传校验**：安装包上传完成、发布记录创建成功后即视为发出，不再下载回来复验（2026-10-20 约定）。
7. 收尾：更新计划/交接文档的验收记录（如实标注未执行项），README 与 releasing.md 同步，提交推送。**推送需要再次等待维护者开启代理**；若代理仍关闭，保留本地提交并明确告知待补推，不能写成已推送。
8. 清理本地 `release/`：只保留历次上传状态/审计记录 `.matelab-publish-*.json`；安装包、`.sha256`、插件包、插件目录、发布说明文本等其余产物均可删除——正式产物在 GitHub Release，CI 可重新下载，不把 `release/` 当长期归档，也不提交。

## 交付检查

- 新代码使用 `bandscope.*` 导入；不要通过添加功能目录到 `sys.path` 解决包依赖。
- 新测试不得依赖本机实验文件、用户注册表设置或已安装插件。复用 `tests/support/` 中的合成数据和隔离辅助。
- 代码改动运行受影响模块测试，集成前运行 `python -m unittest discover -s tests -t . -v`；仅文档改动检查差异、文件引用和状态依据，除非用户要求，不运行无关自动化测试。
- 修改构建、插件加载或资源路径后，补做相应打包和启动检查。
- 交接只写当前目标、关键约束、相关文件、已验证内容和下一步，不复制整段聊天或历史日志。明确哪些检查未执行及原因；不要把未完成视觉验收写成已通过。
- 提交前检查 `git status --short`，只包含本任务范围的代码、测试和说明；不提交 `.local/`、虚拟环境和构建产物。
