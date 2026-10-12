# 时间轴 3D 预览一致性验收

状态：源码实施与本机验收完成。日期：2026-10-12。

## 改动与边界

- `DATA_SOURCE` 的 3D 预览使用完整当前帧，不再按体素步长抽稀；保留 mapper 的交互质量设置、预览节流、过期请求处理和释放后的精确刷新。
- 修复抽稀丢失窄峰、预览与完成归一化范围不同、不透明度距离尺度变化，以及质量切换导致场景重建的问题。
- 其他原因的抽稀策略、2D 路径、公开插件 API 和用户设置不变。已有裁剪及页面管理工作区改动未覆盖。
- 改动范围：主窗口预览接入；合成数据集成回归及持久体渲染回归；新增 `scripts/validation/verify_time_preview.py`。验收输出、数据和日志仅在 `.local/` 保存。

## 自动化验证

新集成测试使用 137×139×133 合成体数据，超过 250 万体素阈值；奇数索引窄峰验证步长抽稀会丢失的结构。覆盖完整尺寸、极值、色带锁定、透明度折线、裁剪范围、紧凑数据域、E 翻转和插件倍率；其他刷新原因仍按原策略抽稀。

新增渲染回归验证同形状换帧与预览转完成复用 actor、mapper、grid，不增加场景重建或同帧上传，插件 alpha 与不透明度距离尺度保持一致。新增 OpenGL 用例使用仓库的环境探针。

执行命令（仓库根目录，使用 `.venv/Scripts/python.exe`）：

```powershell
python -m unittest tests.integration.test_time_preview tests.rendering.test_volume_render_session tests.rendering.test_color_lock tests.rendering.test_refresh_pipeline tests.integration.test_rotation_preview -v
python -m unittest discover -s tests -t . -v
$env:QT_QPA_PLATFORM='windows'
python scripts/validation/verify_time_preview.py
```

- 相关测试 20 项通过；完整回归 1069 项通过。完整日志：`.local/logs/time-preview-regression.log`。
- 首次沙箱运行出现 Windows 临时目录权限错误，在正常权限环境复核通过。一次组合运行在后续插件 GUI 用例处直接退出；完整发现顺序运行通过全部插件及渲染用例，未修改既有测试规避错误。
- `git diff --check` 通过。

## 真实数据与视觉结果

数据：`.local/data/WSe2_step.npz`，WSe₂ 时间分辨 ARPES，150×150×200×25。

环境：Windows 原生 Qt 窗口，隔离 INI 设置和扩展目录，不进行联网更新；CPU 计算、NVIDIA GPU 渲染；VTK 9.6.0、PyVista 0.47.1、NumPy 2.4.3。窗口请求尺寸 1280×800，高 DPI/窗口布局后的 VTK 截图为 1425×945。参数为默认 magma 色带、线性强度映射、色阶 0/50/100、未锁色带、固定默认相机。

脚本通过滑块拖动状态及取值信号驱动真实刷新路径。截图阶段临时停止 idle 精确计时器，使保存预览截图后再释放滑块；连续拖动阶段不暂停计时器，也不保存截图，保留正常调度。耗时采用渲染调用返回时刻，不包括保存截图。

| 帧索引 | 预览绘制 ms | 完成绘制 ms | 释放到完成 ms | 预览/完成像素平均绝对差（0–255） |
|---|---:|---:|---:|---:|
| 3 | 114.99 | 1.51 | 2.83 | 0 |
| 0 | 116.00 | 1.52 | 3.04 | 0 |
| 12 | 113.19 | 1.65 | 3.27 | 0 |
| 24 | 116.86 | 1.57 | 2.91 | 0 |

- 四组截图逐像素一致；形状、色阶、不透明度距离、网格边界、相机和上传计数一致。人工检查第 0 帧图像，能带结构、亮度、透明度及边界一致。
- 本轮主场景仅构建一次，换帧和释放均未重建。
- 连续遍历 25 帧，输入计时器设为 90 ms；实际总耗时 5146.80 ms。25 次预览绘制中位数 122.72 ms、P95 127.03 ms；25 次 idle 完成刷新中位数 4.55 ms、P95 6.32 ms。最终释放到完成 7.11 ms。
- 输入计时器不是实际帧率保证：本机高 DPI 窗口中，完整体素预览加节流和消息处理后实际约 4.9 次新帧/秒。小窗口离屏测量不能替代这一真实窗口结果。此次优先解决画面跳变，未据此进一步修改 mapper 采样参数。

原始结果：`.local/outputs/verify_time_preview/results.json`；场景截图：同目录 `frame_<帧>_preview.png` 与 `frame_<帧>_exact.png`；运行日志：`.local/logs/time-preview-visual.log`。

## 限制与后续

- Qt 的 `window_*.png` 抓图中原生 OpenGL 区域为黑色，不能作为体渲染外观证据；本次图像对比使用真实窗口 plotter 的 VTK 截图。未完成物理鼠标拖动的人工观感复核。
- 真窗口验收为默认参数的 3D 主页；色带锁定、裁剪、E 翻转和插件倍率由自动化回归覆盖，未逐项补做真窗口视觉验收。
- 本次不涉及资源路径、构建或插件加载改动，未重建安装包。超大数据及其他显卡的性能尚未测量；若继续优化拖动帧率，应依据各设备耗时调整绘制采样或调度，并复查同帧图像一致性。
