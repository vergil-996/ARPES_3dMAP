# 文档入口

按任务选择一个入口，再读取相关章节或所选记录；不默认遍历全部文档。

| 任务 | 入口 |
| --- | --- |
| 新增文件、确认模块职责或跨模块改动 | [目录与模块地图](architecture/repository-map.md) |
| 配置环境、运行测试或真实窗口验收 | [开发与测试](development/testing.md) |
| 插件业务或宿主公开接口 | [插件开发](development/plugins.md) |
| 构建、签名、发布和安装包验证 | [构建与发布](development/releasing.md) |
| 接续尚待实施的工作 | [当前计划](plans/README.md) |
| 接续开放问题、发布跟进或剩余验收 | [当前交接](handoffs/README.md) |

协作与检索规则见 [根目录约定](../AGENTS.md)。每个开放事项由索引指定一份状态主记录；详细方案和验收证据按该记录链接读取，不在多个入口复制历史结论。

追溯旧路径时读 [迁移清单](architecture/migration-map.md)；追溯设计、旧交接或过去验收时从 [历史归档](archive/README.md) 进入。历史文件记录当时状态，不直接作为当前执行指令；代码与规范冲突或缺少最新验证时先核实。

正式界面图片放 `images/`；新生成的截图先写入 `.local/outputs/`，经人工选择后再作为文档附件提交。
