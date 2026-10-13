# EDC 瀑布图（edc_waterfall）

API 3 的完整示例：**下拉动作 → 宿主冻结来源 → 插件同步计算 → 宿主建派生结果页**。
它把原先写在主程序里的 EDC 瀑布图（X/Y 积分结果 → 按 k 步长抽样 → 逐曲线归一化 →
错位堆叠）整体移入插件，同时接管步长设置与自动标题。

需要宿主接口版本 3（`requires_app` 里的范围声明只能由 1.12.2 及以上的主程序解析，
真正的门槛是 `api_version: 3`：更早的主程序会在兼容检查里给出“需要宿主接口版本 3”
的明确原因）。

## 做什么

1. 在「其他积分」下拉注册动作「EDC瀑布图」（权重 20，位置与迁移前一致）；
2. 用户在当前 X/Y 积分页（或其矩形裁剪结果）上执行动作时，宿主冻结来源并调用
   `compute_derived_result`：插件按 k 步长最近邻抽样（端点必选、按物理 k 升序）、
   逐曲线归一化、生成自动标题（页面用中文，绘图用 ASCII），交回
   `DerivedWaterfallResult`；
3. 宿主校验后建 `plugin_derived` 结果页并切换过去：绘图、裁剪、出版与数值导出仍
   走宿主路径；
4. 在「插件管理 → 插件设置」里提供自动 / 手动 k 步长；宿主负责「保存为新建默认值」
   与「应用当前瀑布图」两个操作。

## 不做什么

- 不建页面、不绘图、不碰 VTK、不写文件；
- 不读取冻结来源以外的数据，也不改动任何强度数组；
- 不做持久化（默认值由宿主按插件 ID 存进 QSettings，逐页参数存在页面参数里）。

## 接口要点

```python
def integration_actions(self):
    return (IntegrationAction("create", "EDC瀑布图", order=20.0),)

def compute_derived_result(self, source: DerivedInput2D):
    # 只读输入：data[k, E] + 坐标 + 完整轴 + 来源元数据（原始坐标排列）
    return DerivedWaterfallResult(k=..., sample_indices=..., energy=..., raw_curves=...,
                                  curves=..., title="EDC瀑布图 [...]",
                                  plot_title="EDC Waterfall [...]",
                                  params={"k_step": 0.02, "k_step_mode": "auto"})
```

- 自动步长 = 完整未裁剪 k 轴的中位相邻间距（下限 0.0001，回退 0.01）；
- 手动步长范围 0.0001–10，越界抛 `ValueError`（消息直接展示，宿主不建页）；
- `sample_indices` 是采样点在完整动量轴上的下标（含裁剪偏移），用于出版图的曲线
  身份与调色板位置；
- `params` 回填解析后的参数（实际步长、曲线数），供回显与复现。

详细约定见[插件开发](../../docs/development/plugins.md)。

## 构建与测试

```powershell
python scripts/release/build_plugin.py edc_waterfall --output-dir release
python -m unittest discover -s tests/plugins/edc_waterfall -t . -v
python -m unittest tests.integration.test_plugin_derived_flow -v
python scripts/validation/verify_derived_waterfall.py <npz路径>
```
