# 文件对话框目录记忆

状态：实现与自动化验证完成（2026-10-12）。

## 范围和行为

- 新增 `bandscope/ui/file_dialogs.py`，统一保存 QSettings 键
  `file_dialogs/last_directory`；使用应用原有设置，不新增依赖。
- 数据加载、MAT 转 NPZ 保存、数值导出、PNG/PDF 图片导出、插件包安装、平带
  预设导入导出共享最近一次确认选择的目录，重启后恢复。保存时保留建议文件名。
- 取消选择不更新；目录不存在时使用调用方默认位置。目录在确认路径时记录，
  即使后续文件读取或写入失败也记住用户选过的位置。
- `bandscope/extensions/ui.py` 公开 `RememberingFileDialog`，插件从公开 UI 接口
  使用，旧宿主缺少该工具时回退到普通 Qt 对话框。平带插件升到 1.2.2。
- 主窗口共享文件只替换 QFileDialog 导入；保留任务开始时已有的裁剪、时间预览、
  页面管理等未提交修改。

## 验证

- `.venv/Scripts/python.exe -m unittest tests.ui.test_file_dialogs -v`：7 项通过，
  覆盖操作之间共用目录、新设置实例恢复、取消、中文路径、目录失效、父级设置、
  建议文件名以及所有现有入口接入。
- `.venv/Scripts/python.exe -m unittest discover -s tests -t . -v`：1076 项全部通过。
- `.venv/Scripts/python.exe scripts/release/build_plugin.py flat_band_opacity --output-dir .local/outputs/file-dialog-history`：
  1.2.2 插件构建及包校验通过，产物在忽略目录内。
- 受影响模块 `compileall` 和 `git diff --check`：通过。
- Windows Qt 平台下使用 `tests` 隔离设置和扩展目录，创建 QApplication 与
  My3DAnalyzer，运行短时事件循环并关闭：通过，输出 `STARTUP_SMOKE_OK`。

首次沙箱内测试遇到 Windows 临时目录权限拒绝；在正常临时目录权限下复核通过。
新测试不使用真实实验数据或用户注册表设置。

## 未执行

未对原生文件选择器逐项进行桌面人工交互验收；未重建 CPU/NVIDIA 安装包、
未执行冻结包验收、未发布插件。主程序重新启动会使用代码修改；已安装的平带插件
需要更新至 1.2.2 才能接入其预设对话框。下一步可在发布前人工核对原生对话框路径，
再随正常发布流程打包。
