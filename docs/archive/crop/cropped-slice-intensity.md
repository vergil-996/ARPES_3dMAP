# 裁剪后的二维范围参与切片内强度积分

状态：代码与自动化回归完成（2026-10-13）；真实窗口视觉验收未执行。

目标：从裁剪后的 2D 页执行「切片内强度积分」，只累加该页保留的数据，
生成随延迟变化的强度曲线。
负责范围与共享文件：`bandscope/app/refactored_app.py`、
`tests/integration/test_crop_integration.py`。
开始时已有时间轴同步修复；工作期间另有裁剪坐标接口改动，均保留。

## 修改

- 创建积分时读取当前二维来源，冻结基础切片几何、被积轴区间、原有矩形
  参数与完整裁剪链，并继承当前来源的数据域和页面关系。
- 每个延迟帧复用旋转、切片提取、轴求和和裁剪计算，只累加最终保留的数据。
  多次裁剪、单层切片、ROI 与 E 翻转沿用二维视图的计算路径；裁空像素不参与求和。
- 已保存的二维来源不再读取当前页的实时裁剪参数；之后修改来源页或切换
  时间帧不会改变已生成曲线的空间选区。时间积分二维来源提供空间选区，
  时间曲线仍逐帧计算。
- 曲线与数值导出共用计算入口；无需旧 `clip_ranges` 即可从二维裁剪页执行。
  空二维选区不回退到旧三维范围。原有三维范围和旧积分结果保留兼容路径。

## 验证

- 新增 13 项合成数据回归，覆盖三种轴、重复裁剪、旧矩形、单层切片、
  创建动作、空范围、来源修改、非均匀与倒序坐标、E 翻转、旋转、ROI、
  裁空、时间积分来源、旧结果，以及曲线与数值导出一致性。
- `.venv\Scripts\python.exe -m unittest tests.integration.test_crop_integration tests.integration.test_time_integral_derivation tests.integration.test_crop_e_axis_coordinates tests.core.test_crop_model -v`：68 项通过。
- `.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`：最终工作区
  1147 项全部通过。日志为 `.local/logs/cropped-slice-intensity-regression.log`，不提交。
- `git diff --check`：通过。
- 首次局部测试退出时遇到沙箱临时目录清理权限错误；正式检查在正常权限下
  运行。首次完整回归遇到另一组坐标接口改动写入中的接口不匹配，接口更新
  完整后复跑通过，未修改或删除断言。

未执行：真实数据与完整窗口视觉验收；冻结包和安装器检查（本次未改构建、
插件加载或资源路径）。
下一步：需要发布时纳入版本，在实际窗口复核裁剪后的二维时间曲线。
