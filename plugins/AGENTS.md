# 插件开发

- 一个插件一个目录，目录名等于 `plugin.json` 的 `id`。清单、实现、资源和使用说明放在该目录内。
- 公开协议使用 `bandscope.extensions.api`，主题和复用控件使用 `bandscope.extensions.ui`。
- 不直接导入主窗口、宿主管理器、宿主渲染内部类；不直接操作 VTK 或改写原始强度数据。
- 接口不足时，先在任务交接中提出宿主能力需求，由集成任务协调接口变更。
- 自动化测试放 `tests/plugins/<id>/`；需要真实窗口的可重复验收脚本放 `scripts/validation/`。
- 插件源码不进入基础安装包。使用 `python scripts/release/build_plugin.py <id>` 单独生成 `.bsplugin`。
- 不随插件分发 NumPy、Qt、VTK、CUDA 等宿主依赖。检查清单版本与应用版本匹配。
- 新插件不使用根目录兼容导入；兼容模块只服务已有插件包。
