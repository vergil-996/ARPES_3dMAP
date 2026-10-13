# 2D 裁剪页时间轴同步修复

状态：代码修复与自动化回归完成（2026-10-13）；真实窗口视觉验收未执行。

目标：2D 轴积分结果再次裁剪后，裁剪区域随本页时间滑条切换帧。
负责范围：`bandscope/app/refactored_app.py`、`tests/integration/test_crop_integration.py`。
共享文件：主窗口集成入口；本轮单任务修改，开始时工作区干净。

## 原因与修改

裁剪将轴积分页面类型从 `axis_integral` 改为 `axis_integral_crop`，但
`on_image_time_changed` 和 `_resolved_axis_integral_params` 只同步前一种类型。
裁剪页的 `source_t_index` 因而停留在创建时的帧，即使滑条已移动仍计算旧帧。

两处同步入口现均包含 `axis_integral_crop`，沿用已有逐页状态保存、按时间帧
区分的缓存与裁剪链计算。裁剪物理边界保持固定，区域内强度按当前帧更新。
时间积分来源仍沿用固定时间范围和禁用帧滑条的原有行为。

## 验证

- 使用小规模合成四维数据；修复前复现旧帧问题，修复后新增 4 项测试通过。
  覆盖二次裁剪帧切换及导出、无滑条信号时的当前帧解析、非活动页保存帧、
  时间积分裁剪的数值结果；同时覆盖旧矩形参数与裁剪链两条路径。
- `.venv\Scripts\python.exe -m unittest tests.integration.test_crop_integration tests.integration.test_time_integral_derivation tests.integration.test_time_preview tests.integration.test_time_value_box_sync -v`：44 项通过。
- `.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`：1127 项全部通过。
  日志：`.local/logs/crop-time-regression.log`（不提交）。
- 沙箱首次运行遇到测试临时隔离目录的清理权限错误；正式回归在正常临时目录
  权限下运行，未修改断言或隔离配置。

未执行：真实实验数据与完整窗口的视觉验收，本轮仅验证状态和数值链路；
冻结包及安装器检查，本轮未改构建、插件加载或资源路径。
下一步：需要发布时纳入版本，并在实际窗口拖动时间滑条复核二次裁剪画面。
