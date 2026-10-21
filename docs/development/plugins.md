# 插件开发

## 接口与目录

插件源码放 `plugins/<id>/`，包含 `plugin.json`、`README.md`、入口模块和功能资源；测试放 `tests/plugins/<id>/`。现有平带插件（渲染效果）、二维积分演示插件（分析任务）与 EDC 瀑布图插件（派生结果与集成动作）分别作为三类示例。

```python
from bandscope.extensions.api import (
    Plugin, PluginContext, PluginHost,        # API 1：上下文、刷新、提示
    PluginHostV2, AnalysisInput2D, AnalysisCurve1D,  # API 2：分析任务
    PluginHostV3, DerivedInput2D, DerivedWaterfallResult, IntegrationAction,  # API 3：派生结果
)
from bandscope.extensions.ui import theme, ActionButton, SyncedSlider, SyncedSwitch
```

`api` 不导入 Qt / VTK，可独立测试；`ui` 在界面初始化后使用。宿主安装管理、面板生命周期及主窗口接入属于内部实现。

插件不得直接访问主窗口私有字段、直接操作 VTK 或修改原始强度。渲染、色条、结果页与导出数值都由宿主按自己的路径产生。

## 版本与兼容声明

- `api_version` 是整数。宿主声明**支持集合**，当前为 `{1, 2, 3}`；插件声明的版本必须落在集合里，不能因为主程序小版本接近就认定兼容。
- `requires_app` 是裸版本（如 `1.12.2`）时按精确版本匹配，旧清单原样解释、不自动放宽；带运算符时按 [PEP 440 范围](https://packaging.pypa.io/en/stable/specifiers.html)解析，例如 `>=1.9.0,<2.0.0`。范围写法只有 1.12.2 起的主程序能解析：更早的主程序仍按精确匹配比较，看到范围声明会直接判为不兼容，因此放宽范围要配合新版主程序发布。
- `capabilities` 里每一项都必须由宿主支持，缺一不可：

| 能力 | 含义 |
|---|---|
| `opacity_multiplier` | 提交沿能量轴的不透明度倍率（API 1） |
| `data_snapshot_2d` | 抓取已完成二维结果的只读快照（API 2） |
| `analysis_task` | 提交后台分析任务（API 2） |
| `result_curve_1d` | 交回一维曲线，由宿主建结果页（API 2） |
| `integration_action` | 向「其他积分」下拉注册动作（API 3） |
| `derived_result` | 基于宿主冻结的来源做派生计算（API 3） |
| `plugin_settings` | 在插件管理里提供设置界面与默认值（API 3） |

安装、启动加载、构建脚本、官方目录筛选与主程序升级评估共用同一套判定函数（`bandscope.extensions.compat`），同一份声明到哪儿都是同一个结论和同一句原因。构建脚本会在打包前用当前宿主核对一遍：不通过就不出包。

插件版本号必须是可排序版本（PEP 440）。历史遗留的非标准版本仍可在本地登记表与界面上如实展示，但不参加在线“最新版本”比较。插件代码、资源、入口或兼容声明有任何变化都要提升插件版本：同一 id/version 的内容一经发布不可更改，安装事务会直接拒绝同版本不同内容的包。

## 按能力开发

### 渲染效果（API 1）

上下文、面板注册、刷新请求、页面状态与不透明度倍率。面板挂到 3D 场景的「渲染控制」页，只在三维视图显示。

### 分析任务（API 2）

最小能力范围固定为「当前已完成的 **2D 数值结果** → 后台纯数值计算 → 新增 **1D 结果页**」：

```python
class Plugin(Plugin):
    def create_panel(self, host: PluginHostV2):
        panel = MyPanel(host)          # 面板挂在「处理分析」页，只在 2D 结果页显示
        return panel

def run_job(snapshot, params, cancel):        # 纯数值，工作线程里执行
    cancel.raise_if_cancelled()               # 协作取消检查点
    return AnalysisCurve1D(x=..., y=..., x_label="E", x_unit="eV", title="...", params=params)

# 面板里：
snapshot = host.capture_analysis_input()      # AnalysisUnavailable 携带可展示原因
handle = host.submit_analysis(snapshot, run_job, title="积分", params={"axis": "x"})
host.cancel_analysis(handle)
```

约定：

- `AnalysisInput2D` 的数组顺序是 `[x, y]`，`x` / `y` 与对应维度等长且保留输入方向；`data` 与坐标都是任务独占的**只读**缓冲区。无单位元数据时 `*_unit` 为空串，宿主不会替你补 `eV` 或 `Å⁻¹`。
- 只接受已完成（精确质量）的二维结果：预览中、计算未完成、非 2D 页，以及带**擦除区域**的裁空状态都抛 `AnalysisUnavailable`，消息可直接展示。
- 一个工作线程、最多 4 个排队任务、每个插件最多一个未结束任务。忙碌时 `submit_analysis` 返回 `None` 并提示用户，不会抛给插件。
- 取消是协作式的：还没开始的任务直接出队，正在跑的在你自己的检查点退出。宿主**不会**强制结束线程。
- 结果由宿主校验：x/y 必须一维、非空、等长，x 必须有限，y 允许 `NaN` 表示缺测但拒绝无穷值（缺测不会被伪造成 0）。通过后宿主按来源关系建 `plugin_curve` 结果页，记录插件 id/版本、参数与快照修订号，**不强制切页**，也不会在插件停用后删除已生成的结果。
- `Plugin.on_analysis_finished(handle, status, detail)` 是可选回调（`succeeded` / `failed` / `cancelled`），默认什么都不做；API 1 插件不会因为这个钩子被迫实现新方法。
- 加载新数据、关闭来源页、用户取消或退出应用都会请求取消；完成回调会再核对数据代次，过期结果直接丢弃。

### 派生结果与集成动作（API 3）

最小能力范围固定为「下拉动作 → 宿主冻结来源 → 插件同步计算 → 宿主建派生结果页」，
当前用于把 EDC 瀑布图这类「来源页 + 参数 → 曲线族」的功能移出主程序：

```python
class Plugin(Plugin):
    def integration_actions(self):
        return (IntegrationAction("create", "EDC瀑布图", order=20.0),)

    def compute_derived_result(self, source: DerivedInput2D):   # 纯数值，宿主线程内同步执行
        ...                                                     # 失败抛 ValueError（消息可直接展示）
        return DerivedWaterfallResult(k=..., sample_indices=..., energy=...,
                                      raw_curves=..., curves=..., title=...,
                                      plot_title=..., params={"k_step": 0.02})

    def create_settings_panel(self, host):        # 「插件管理 → 插件设置」里的 QWidget
        return MySettingsPanel(host)

    def export_settings(self): ...                # 当前设置状态
    def restore_settings(self, state): ...        # 恢复保存的默认值（容忍缺失字段）
```

约定：

- `integration_actions()` 报出的动作键以 `插件ID:动作ID` 进入「其他积分」下拉并随页面状态保存；动作 ID 必须跨版本稳定，展示文本可以改。`order` 决定与内置项的排序（内置项按 0/10/30/40 排布）。
- 执行动作时宿主冻结当前 **X/Y 积分页**（或其矩形裁剪结果）为 `DerivedSource2D`：时间帧或时间积分区间、积分范围、裁剪范围与数据域在创建时固定，之后来源页怎么变都不影响已有结果。带**擦除区域**的来源按分析插件同样的契约拒绝。
- `DerivedInput2D` 给出只读的二维数组 `[采样轴, 曲线轴]`、坐标、单位、完整未裁剪轴（自动步长与裁剪后缀的依据）与来源元数据。**V3 的输入与结果一律使用原始坐标排列**：E 轴翻转只是显示镜像，由宿主在渲染与出版出口应用一次；数值导出与插件看到的数据始终是原始排列。
- 结果由宿主校验：`k` / `sample_indices` / `energy` 一维等长且有限，曲线形状与坐标一致，采样身份必须落在完整动量轴内，曲线允许 `NaN` 但拒绝无穷值。`curves` 是显示用归一化曲线，`raw_curves` 是导出用原始强度，`title` 用于页面名，`plot_title` 是绘图/出版用的 ASCII 安全标题（为空时回退 `title`）。
- 创建与参数更新都**先验证计算结果再提交页面状态**：失败时创建不建页、更新保留上次成功结果并在图上标注；自动标题只更新底层默认名，用户改过名时保留显示名。
- 二维采样与归一化在体数据就绪后同步执行，结果按（来源、数据修订、旋转、逐页参数）缓存；其他页激活后回来重算。
- `host.defaults()` / `host.set_defaults(values)` 读写按插件 ID 保存的「新建默认值」，与逐页参数分离，保存立即对之后新建的页面生效；`host.current_derived_page()` / `host.update_current_derived_page(params)` 供插件查询与更新当前派生页。
- 插件无法创建页面、不绘图、不导出：绘图、裁剪、出版与数值导出都由宿主按自己的路径产生。

## 构建和验证

```powershell
python scripts/release/build_plugin.py flat_band_opacity --output-dir release
python scripts/release/build_plugin.py integral_demo --output-dir release
python scripts/release/build_plugin.py edc_waterfall --output-dir release
python -m unittest discover -s tests/extensions -t . -v
python -m unittest discover -s tests/plugins -t . -v
python -m unittest tests.integration.test_plugin_derived_flow -v
```

`.bsplugin` 单独发布；基础安装包不得包含具体插件源码（两套 spec 都检查 `plugins` 模块名与源目录泄漏），宿主依赖也不重复打入插件包。

## 来源验证与官方目录

- 官方包带 `.bsplugin.sig`（Ed25519，协议 1）。签名输入是「用途前缀 + 原始文件字节」，包与目录用不同前缀。宿主只用**内置公钥**验签，不看 `author` 文本、文件名或普通 SHA-256 sidecar。来源状态分三种：官方已验证、本地未验证、历史未验证（从登记表 v1 迁移而来，不自动获得官方身份）。
- 本地导入会读取同目录签名：有签名但验不过或密钥未知**直接拒绝**，不会降级成未签名包；没有签名时界面会询问用户，默认取消，确认后按包摘要记录本地信任。
- 已经装好官方已验证插件时，换成未验证来源需要单独确认，不会被静默覆盖。
- 官方目录 `plugins-index.json` 与 `.sig` 随主程序 Release 一起发布，记录每个插件版本的不可变资源地址、大小与 SHA-256；目录本身也验签，验签失败就禁用本次在线安装。缓存只保留“最后一次验证通过”的内容，低于已接受修订号的目录不会替换缓存。
- 提交安装后、重启激活前，宿主会重新核对内容摘要，磁盘内容被改动过的插件会被拒绝加载。

密钥生成、轮换与发布流程见[构建与发布](releasing.md)。

## 安装与登记表

安装、启用 / 停用与卸载都在顶部工具栏的「插件管理」窗口内完成，均重启后生效；确认操作只改登记表里的期望配置，当前会话已加载的实例保持不变。已安装内容按内容摘要不可变保存；扩展包不得包含 `__pycache__` / `.pyc`，宿主加载插件时也不会把字节码缓存写进安装目录。

登记表为 schema v2，与 v1 不混用：迁移 v1 时会留下 `registry.json.v1.bak` 备份。降级到不理解 v2 的旧版主程序之前，必须先按该备份恢复登记表与内容目录，不能让旧版实例直接读写当前登记表。

旧插件可继续导入 `plugin_api`、`theme`、`ui_controls`，这些兼容模块与新路径共享符号和模块状态，冻结构建显式包含它们。新插件统一使用公开的新路径。
