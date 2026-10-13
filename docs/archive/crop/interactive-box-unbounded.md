# 3D 交互盒越界范围保留

状态：源码修复及回归完成（2026-10-13）。

## 目标与范围

解决 3D 选区超过当前体数据范围后，数值被截断、刷新时盒子缩回的问题。
负责范围为 `bandscope/app/crop_integration.py`、核心坐标转换与裁剪模型、
相关回归测试和真实窗口验收脚本。共享主窗口只补充 E 翻转时保留盒子几何的处理；
工作区原有切片时间积分改动及其他同步增加的测试、说明均保留。

## 行为

- 交互盒及数值草稿保留越界范围，可向外扩展、平移，再移回数据内。
- 物理坐标、逻辑位置与渲染坐标转换增加显式 `clamp=False`；普通调用仍默认限制在数据域内。
- 物理坐标越界采用端部采样间距线性外推，支持非均匀、反向坐标与 E 翻转。
- 切页、刷新和 E 翻转保留草稿的绝对空间位置。E 翻转不再用积分轴控件的全范围薄层覆盖盒子。
- 实际裁剪/裁空沿用数据交集逻辑；完全不相交的裁剪提示无有效数据，裁空保持数据。
- 子页不会因扩框恢复来源页已被裁掉的数据；需要更大数据域时回到来源页裁剪。

## 验证

- `.venv/Scripts/python.exe -m unittest discover -s tests -t . -v`：1148 项全部通过。
  新增交互盒回归 8 项，覆盖 ROI 外与完整数据域外的草稿、数字输入及 VTK 初始回调、
  页面切换、E 正反向与翻转、部分/无交集和默认坐标转换兼容。
  初次沙箱局部回归的两个临时目录权限错误在正常权限完整回归中通过。
- `.venv/Scripts/python.exe -m compileall -q bandscope scripts/validation/verify_crop_box_bounds.py tests/integration/test_crop_box_bounds.py`：通过。
- `.venv/Scripts/python.exe scripts/validation/verify_crop_box_bounds.py`：通过。
  默认 `.local/data/WSe2_step.npz`，150×150×200×25；Windows 原生 Qt/VTK 窗口，
  隔离设置与扩展目录，CPU 计算/NVIDIA GPU 渲染。
  创建内部 ROI 后用 VTK PlaceWidget 与交互事件驱动扩框，检查刷新、E 翻转与拖回后的
  真实 widget 几何、选区数值及数据域。此项为脚本驱动窗口验收，不等同于人工鼠标拖动验收。
- 已查看 `.local/outputs/crop_box_bounds/roi_expanded_scene.png`、`roi_returned_scene.png`
  及范围浮窗截图，确认盒子在数据外延伸、返回后与 ROI 对齐。
  Qt 整窗抓图的 OpenGL 区域为空，场景证据以 VTK 截图为准。
- `git diff --check`：通过。

日志与截图保存在 `.local/`，不提交。未执行人工鼠标手势验收、冻结包与安装器验证；
本次未修改构建、插件加载或资源路径。源码启动验证已由真实窗口脚本覆盖。

下一步：随后续版本打包；人工使用时复核边界手柄拖动手感。
