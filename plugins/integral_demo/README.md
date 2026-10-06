# 二维积分演示（integral_demo）

最小分析插件（API 2）的完整示例：**当前已完成的二维数值结果 → 后台纯数值积分 →
新增一维结果页**。它同时是分析接口的端到端验收对象，不随正式 Release 发布。

## 做什么

1. 在二维结果页（例如「对坐标轴积分」得到的 kx–E 结果）打开「处理分析」页；
2. 选积分方向后点「分析当前二维结果」；
3. 宿主抓取当前结果的只读快照，工作线程沿指定方向求和，交回一维曲线；
4. 宿主校验后新建 `plugin_curve` 结果页，挂在来源页下（不会强行切换当前页）。

沿 x 轴积分得到以 y 轴坐标为横轴的曲线，反之亦然。结果里的坐标、单位与来源页
一起登记，导出走宿主已有的 1D 路径。

## 不做什么

- 不读取快照以外的任何数据，不碰主窗口、不碰 VTK；
- 不修改原始强度：快照数组在宿主侧就是只读缓冲区；
- 不自己建页面、不自己写文件、不自己做持久化（参数由宿主按页面保存）。

## 接口要点

```python
snapshot = host.capture_analysis_input()   # 不可用时抛 AnalysisUnavailable（含原因）
handle = host.submit_analysis(snapshot, run_integral, title=..., params={"axis": "x"})
host.cancel_analysis(handle)               # 协作取消：任务在下一个检查点退出
```

忙碌（上一个任务没结束或队列已满）时 `submit_analysis` 返回 `None`，宿主已经提示
用户；插件不需要处理异常。任务结束后宿主回调
`Plugin.on_analysis_finished(handle, status, detail)`，本插件用它恢复按钮状态。

详细约定见[插件开发](../../docs/development/plugins.md)。

## 构建与测试

```powershell
python scripts/release/build_plugin.py integral_demo --output-dir release
python -m unittest tests.plugins.integral_demo.test_integral_demo -v
python -m unittest tests.integration.test_plugin_analysis_flow -v
```
