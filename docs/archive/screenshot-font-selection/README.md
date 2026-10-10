# 截图样式中英文字体选择

状态：实现完成，2026-10-09。

## 范围与行为

- 负责目录：`bandscope/exporting/`、`tests/exporting/`。未修改主窗口、插件 API、依赖或打包配置。
- 截图样式微调面板增加英文、中文两个独立下拉框，各提供四种字体。
- 英文：DejaVu Sans（默认）、Arial、Times New Roman、Georgia。
- 中文：微软雅黑（默认）、宋体、黑体、楷体。
- 字体覆盖随当前视图族和样式保存，导出成功后提交；取消不提交，恢复样式默认值同时恢复字体。
- 预览与正式导出共用字体链，覆盖标题、轴名、刻度、图例、面板编号及瀑布图标注。拉丁字形优先使用英文字体，中文逐字形使用中文字体。
- 未安装的候选字体在下拉框中标注，渲染时排除缺失字体并使用现有回退链。不随程序分发字体文件。

## 验证

环境：Windows，本仓库 `.venv/Scripts/python.exe`，合成数据、隔离设置。

- `python -m unittest discover -s tests/exporting -t . -v`：100 项通过。
- `python -m unittest discover -s tests -t . -v`：995 项通过。
- `git diff --check`：通过。
- 首次沙箱测试遇到临时目录 WinError 5；在正常权限环境中重跑上述两套测试均通过。完整日志位于 `.local/logs/screenshot-fonts-unittest.log`，不提交。
- 未执行真实数据人工视觉验收、冻结安装包重建；此次未改构建、插件加载或资源路径。

下一步：用户在截图样式微调面板选择所需字体，可按科研排版需要复核输出外观。
