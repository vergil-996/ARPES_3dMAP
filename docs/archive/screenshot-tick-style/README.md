# 2D 截图刻线样式

状态：已完成实现及自动化验证。

## 范围与变更

- 负责 `bandscope/exporting/`、`tests/exporting/`。
- 在截图导出「微调」中增加仅 2D 可见的刻线方向（朝内、朝外）及颜色（黑色、白色）。
- 选项应用于坐标轴刻线，保留刻度数字、轴名、边框及色条的原有颜色。
- 沿用每种样式的默认方向和黑色刻线；支持预览、导出、按样式保存及恢复默认。
- 在已有字体选择改动之上增量修改三个 publication 模块，保留工作区原有其他改动。

## 验证

- `.venv/Scripts/python.exe -m unittest discover -s tests/exporting -t . -v`：103 项通过。
- `.venv/Scripts/python.exe -m unittest discover -s tests -t . -v`：通过；详细日志为本机 `.local/logs/tick-style-unittest.log`。
- `git diff --check`：通过。
- 首次沙箱测试遇到 Windows 临时目录权限错误，已在正常权限环境复跑通过。
- 自动化验证使用合成数据，覆盖三种 2D 样式、两种方向和两种颜色、设置保存及默认恢复。
- 未执行真实数据窗口视觉验收或重新打包；本次未修改构建或资源路径。

下一步：如需发行版本，将本次源码改动纳入后续发布。
