# 插件开发

## 接口与目录

插件源码放 `plugins/<id>/`，包含 `plugin.json`、`README.md`、入口模块和功能资源；测试放 `tests/plugins/<id>/`。现有平带插件可作为目录组织示例。

```python
from bandscope.extensions.api import Plugin, PluginContext, PluginHost
from bandscope.extensions.ui import theme, ActionButton, SyncedSlider, SyncedSwitch
```

`api` 不导入 Qt / VTK，可独立测试。`ui` 在界面初始化后使用。宿主安装管理、面板生命周期及主窗口接入均属于内部实现。

目前 API 版本为 1，提供能量轴上下文、面板注册、刷新请求、页面状态与不透明度倍率能力。不要假设它已支持任意分析插件。新增能力先定义宿主协议，再实现插件；插件不得直接访问主窗口私有字段、直接操作 VTK 或修改原始强度。

清单格式与兼容性规则保持不变，`requires_app` 当前要求与应用版本精确一致。扩展安装到 `%LOCALAPPDATA%\BandScope\extensions`，源码目录不自动视为已安装扩展。

## 构建和验证

```powershell
python scripts/release/build_plugin.py flat_band_opacity --output-dir release
python -m unittest discover -s tests/extensions -t . -v
python -m unittest discover -s tests/plugins/flat_band_opacity -t . -v
```

`.bsplugin` 和 SHA-256 文件单独发布。基础安装包不得包含具体插件源码；两套 spec 都检查 `plugins` 模块名和源目录泄漏。宿主依赖不重复打入插件包。

旧插件可继续导入 `plugin_api`、`theme`、`ui_controls`，这些兼容模块与新路径共享符号和模块状态，冻结构建显式包含它们。新插件统一使用公开的新路径。
