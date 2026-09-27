# 主程序开发

遵循根目录协作约定及 [模块地图](../docs/architecture/repository-map.md)。

- `app/refactored_app.py` 仍是集成入口。本次目录整理没有拆分主窗口；新增独立能力放责任模块，不继续堆进主窗口。
- 数据模型放 `core`，数值处理放 `processing`，渲染放 `rendering`，通用界面放 `ui`，导出放 `exporting`。
- 宿主插件协议在 `extensions/api.py`。该模块不得导入 Qt / VTK；UI 辅助通过 `extensions/ui.py` 单独暴露。
- 核心模块不得导入 `plugins/<id>` 的具体实现。宿主只使用协议和插件提交的效果。
- 包 `__init__.py` 保持轻量，不集中导入窗口或图形库。启动必须先配置 Qt，再创建 QApplication，再导入主窗口。
- 主窗口、公开 API、刷新时序属于共享接入点；多个任务协作时由集成任务协调修改。
- 资源路径使用启动模块的 `resource_path`，不得依赖调用方的当前工作目录。
