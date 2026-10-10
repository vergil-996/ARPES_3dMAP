# 坐标轴积分端点与 E 翻转修复

状态：代码与自动化回归完成（2026-10-09）；真实窗口视觉验收未执行。

## 范围与实现

- 集成修改：`bandscope/app/refactored_app.py` 与 `crop_integration.py`。
- 模型与控件：`bandscope/core/axis_interval.py` 与 `bandscope/ui/axis_interval_controller.py`。
- 测试：`tests/core/test_axis_interval.py`、`tests/ui/test_axis_interval_controller.py`、`tests/integration/test_axis_interval_pages.py`。
- 初始工作区无已有改动。

E 翻转后区间控件使用显示坐标映射采样下标。切换翻转保留原选中的采样范围及亚采样端点，重新排序物理上下限；页面状态记录保存时的显示方向，恢复与创建新页面使用同一方向。三维选区仍使用原始采样下标；切片裁剪链仍保存原始物理坐标。

未锁定时，下限越过上限会推动上限，上限越过下限也会推动下限；继续拖动两端一起走，反向拖动可以重新分开。数值框提交使用同一规则。锁定时保持区间长度整体平移并限制轴边界。允许上下限相等，代表单层采样。滑条提交被轴边界约束的值时，无论模型是否发生变化，都同步回合法位置。

## 验证

- 受影响控件、区间与 E 翻转回归已执行；覆盖两端相互推动、连续拖动后反向分开、正反坐标、数值框提交、锁定平移、非均匀轴翻转、页面恢复、新页参数和选区下标。
- 最终全量：`.venv/Scripts/python.exe -m unittest discover -s tests -t . -v`，991 项通过。
- `git diff --check` 通过。
- Windows 沙箱临时目录曾出现 WinError 5；在正常临时目录权限下复跑通过。日志：`.local/logs/axis-integral-regression.log`（不提交）。
- 未执行真实数据、真实窗口视觉验收或冻结包检查；未修改构建、依赖或资源路径。

下一步：在真实窗口打开数据，选择 E 轴，复核翻转前后上下限对应的选区边界及连续拖动触边表现。
