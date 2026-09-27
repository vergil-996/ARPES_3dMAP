# 测试约定

- 使用标准库 `unittest`，命名为 `test_*.py`。新测试子目录需要 `__init__.py`，供统一发现命令递归收集。
- 按主要责任模块归类；跨主窗口、状态和模块接入的测试放 `integration`。
- 测试包启动时隔离 QSettings 与扩展根目录，并默认采用 Qt 离屏模式。不要覆盖成用户真实目录。
- 临时文件用 `TemporaryDirectory`；数组与 NPZ 使用小规模、固定种子的合成数据。真实实验数据只用于人工验收脚本。
- 可复用构造器放 `support`，不从另一个测试文件导入辅助函数。
- GUI 测试复用 `QApplication.instance()`，保留对象生命周期；不要在 QApplication 创建前构造字体或控件。
- 从仓库根目录运行 `python -m unittest discover -s tests -t . -v`，单模块运行 `python -m unittest tests.core.test_axis_mapping -v`。
- 不用跳过或删除断言掩盖迁移引起的问题；分别报告既有失败、环境限制和新增回归。
