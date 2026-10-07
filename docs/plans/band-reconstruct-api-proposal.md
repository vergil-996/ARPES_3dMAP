# 能带重构插件：宿主接口契约提案（阶段 0）

- 状态：待评审（评审通过后作为阶段 2/3 的实现依据）
- 创建：2026-10-07
- 上游计划：[能带重构插件总体计划](band-reconstruction-plugin.md)
- 现有协议基线：`bandscope/extensions/api.py`（API 2）、`compat.py`、`plugin_host.py`、`analysis_host.py`

## 1. 目的与边界

本提案把「MRF 能带重构」插件需要的宿主能力写成可用于实现的契约，先评审、后落笔。它**只描述宿主与插件之间的接口**，不改任何代码。

约束（沿用总体计划全局边界）：

- 插件侧只用宿主提供的 `numpy`（≥2.0）与 `scipy`（≥1.13）；不引入 TensorFlow / PyTorch，也不新增任何第三方依赖。
- 跨边界能力一律走 `bandscope.extensions` 公开接口；插件不碰主窗口私有字段、不碰 VTK。
- 旧插件（API 1 / API 2）语义完全不变：新增能力是**追加**，不是替换。

现有基线（据实，2026-10-07 代码）：

| 现状 | 位置 |
|---|---|
| API 2 最小分析集合 = `data_snapshot_2d` + `analysis_task` + `result_curve_1d` | `compat.ANALYSIS_CAPABILITIES` |
| `SUPPORTED_API_VERSIONS = {1, 2}`、`SUPPORTED_CAPABILITIES` 四项 | `compat.py` |
| 二维快照 `AnalysisInput2D`：`[x, y]` 只读、坐标等长、单位如实转述 | `api.py` |
| 队列：1 工作线程 / 最多 4 排队 / 每插件最多 1 个未结束任务；`CancelToken` 协作取消 | `analysis_host.py` |
| 结果页：`plugin_curve` 页 + `base_curve` 快照，**不切换**当前页；数据代次过期即丢弃 | `plugin_host.py:608`、`refactored_app.py:1688` |
| 分析面板挂载点：`处理分析` 页，**只在二维结果页显示** | `page_data_process_v2.py:155`、`refactored_app.py:7836` |

## 2. 能力清单与兼容声明

新增三个能力字符串（与总体计划一致）：

| 能力 | 含义 | 阶段 |
|---|---|---|
| `data_snapshot_3d` | 抓取当前页数据域的三维强度体快照（`[X, Y, E]`） | 2 |
| `result_surface_2d` | 交回能带面结果（`E_b(kx, ky)` 网格），由宿主建面结果页 | 2 |
| `render_overlay_surface` | 面结果可作为叠加图层进入 3D 视图，宿主负责着色、显隐与联动 | 3 |

组合常量：

```python
ANALYSIS_3D_CAPABILITIES = (
    CAPABILITY_DATA_SNAPSHOT_3D,
    CAPABILITY_ANALYSIS_TASK,     # 沿用：仍然走同一套后台任务
    CAPABILITY_RESULT_SURFACE_2D,
)
```

- 最小的三维分析插件声明 `ANALYSIS_3D_CAPABILITIES`；`render_overlay_surface` 是**可选增强**，声明它必须同时声明 `result_surface_2d`（校验在 `evaluate_compatibility` 之外由宿主加载时给出可读原因，或直接在 `compat` 里加一条组合规则——评审定）。
- `analysis_task` 是共用的：三维重构与二维积分走同一个 `AnalysisTaskRunner`，队列与限制语义完全一致。

版本：

- `api_version` 升到 **3**：`SUPPORTED_API_VERSIONS = frozenset({1, 2, 3})`，`API_VERSION = 3`。
- 新增 `PluginHostV3(PluginHostV2)`；`_HostBridge` 实现 v3。v1/v2 插件拿到的句柄行为不变。
- 插件 `requires_app` 用范围声明 `>=1.13.0,<2.0.0`（对应阶段 2 落地的版本号；最终以实际发布版本为准）。

面板显隐规则（宿主侧改动）：

- 声明 `data_snapshot_2d` 的分析插件：面板在二维结果页显示（现状不变）。
- 声明 `data_snapshot_3d` 的分析插件：面板在**三维视图**显示（当前页 `view == "3d"`）。
- 两类都声明时两处都显示（同一实例、同一面板，宿主保证不重复创建）。

## 3. 能力 A：`data_snapshot_3d`

```python
class PluginHostV3(PluginHostV2):
    api_version = 3

    @abc.abstractmethod
    def capture_analysis_input_3d(self) -> AnalysisInput3D:
        """抓取当前页数据域的三维强度体快照；不可用时抛 AnalysisUnavailable。"""
```

### 3.1 数据契约

```python
@dataclass(frozen=True)
class AnalysisInput3D:
    plugin_id: str
    page_id: str
    page_title: str
    snapshot_id: str
    data_generation: int          # = window.shared_denoise_version
    volume: np.ndarray            # float32, [X, Y, E]，只读，任务独占
    x: np.ndarray                 # [nx] 第一维物理坐标（只读）
    y: np.ndarray                 # [ny]
    e: np.ndarray                 # [ne] 能量坐标（只读，**未翻转**）
    x_label: str = "x"; x_unit: str = ""
    y_label: str = "y"; y_unit: str = ""
    e_label: str = "E"; e_unit: str = ""
    frame_index: Optional[int] = None
    frame_label: str = ""
    scope_id: str = "full"
    scope_label: str = "完整数据"
    rotation_angle: float = 0.0   # 快照**不含**旋转；这里只记录当前显示旋转角
    title: str = ""
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int, int]: ...
    @property
    def nbytes(self) -> int: ...
    def describe(self) -> str: ...        # 一行来源说明，进结果页与导出元数据
```

语义（与二维快照对齐）：

1. **轴向顺序固定 `[X, Y, E]`**，与 `core.raw_data[..., t]` 的 `[X, Y, E]` 一致；插件把前两维当动量平面 `(kx, ky)`、第三维当能量。哪个物理轴是 kx、哪个是 ky 由插件面板上的用户选择决定，宿主不替插件解释物理含义。
2. **取数路径**：当前页的数据域（`spec.data_scope_id`）→ 当前帧 `t`（时间轴滑块）→ 若全局去噪开启则取去噪后的体数据（与三维视图一致）。**不含**显示旋转（`rotation_angle` 只如实记录当前角度，叠加渲染时由宿主代偿）。
3. **坐标与体数据同域**：`x/y/e` 已按数据域（ROI）切到与 `volume` 各维等长；单位只如实转述，缺单位保持空串（与 `AnalysisInput2D` 同规则）。
4. **只读、任务独占**：宿主在抓取时复制一份 `float32` 缓冲区并置 `writeable=False`，插件改不动、也不必自己再复制。
5. **NaN 语义**：体数据可能含缺测（如擦除区域产生的 NaN）。宿主不保证全有限，插件需自行掩膜或填充；`volume` 中 NaN 是「无数据」，不是 0。
6. **生命周期**：宿主只在任务存续期持有快照（提交时登记、任务结束即释放）；插件不得在任务返回后继续持有 `volume` 引用。

### 3.2 不可用条件（`AnalysisUnavailable`，消息可直接展示）

- 未加载数据、当前页没有有效数据域；
- 当前结果仍在预览 / 计算未完成（沿用 `_render_exact_ready` 语义）；
- 数据域裁剪后为空（`crop_empty`）；
- 预计快照超过宿主上限（`MAX_SNAPSHOT_3D_BYTES`，建议 1 GiB）——给用户一句「数据过大」的原因。

> 评审点：是否允许在二维页抓三维快照（建议允许：只要有数据域即可，面板显隐只管入口位置）。

### 3.3 内存策略

- 单份快照 ≈ `nx·ny·ne·4` 字节（256×256×470 ≈ 115 MB）。
- 宿主侧峰值 = 队列中未结束的体数据任务数 × 单份大小；现有队列上限 4，但实际并发体数据任务通常为 1。阶段 2 需实测确认是否需要「体数据任务单独限流为 1」。
- 插件侧峰值 = 快照 + 预处理副本 + 每条带一张能量面（64×64 ≈ 32 KB/带）；预处理不得在原地改快照（宿主已置只读，原地写会直接报错，这是有意的护栏）。
- 界面显示：面板上给出「本次快照 ≈ XXX MB」的提示，运行中显示已用时间。

## 4. 能力 B：`result_surface_2d`

```python
@dataclass(frozen=True)
class BandSurface:
    z: np.ndarray                 # [i, j] 每点能量值；NaN = 无解/未定
    label: str                    # 如 "Band 2"
    color: Optional[str] = None   # 显示偏好（阶段 3 用），如 "#ff8800"
    opacity: Optional[float] = None   # 显示偏好默认不透明度（0–1）

@dataclass(frozen=True)
class AnalysisSurface2D:
    x: Sequence[float]            # [i] 动量坐标（kx）
    y: Sequence[float]            # [j] 动量坐标（ky）
    surfaces: Sequence[BandSurface]
    x_label: str = "kx"; x_unit: str = ""
    y_label: str = "ky"; y_unit: str = ""
    z_label: str = "E";  z_unit: str = ""
    title: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)
```

**与总体计划原文的差异（需评审确认）**：原文写的是「`z[i, j]` + x/y + 带标签」，本提案把结果推广为**一次任务交回若干共网格的带面**。理由：

1. 面板的「运行」一次就是逐带顺序重构，N 条带若只能返回一个面，插件必须用 N 个串行任务接力（宿主每插件只允许 1 个未结束任务），既慢又难取消；
2. 阶段 3 的叠加图层天然需要同一组的全部带面；
3. 单带场景就是 `len(surfaces) == 1`，没有额外成本。

校验（`validate_analysis_surface`，与 `validate_analysis_curve` 同级）：

- `x` / `y` 必须一维、非空、有限、**严格单调**（图与插值都依赖）；`z.shape == (len(x), len(y))`；
- `z` 允许 NaN（无解），拒绝 ±inf；
- `surfaces` 非空、条数上限（建议 64）；`label` 非空、去重后仍唯一（重复时宿主补编号）；
- 通过后由宿主复制成只读缓冲区；`params` 必须是可序列化普通数据。

结果页：

- 新页类型 `plugin_surface`（对照现有 `plugin_curve`），挂在来源页下，**不切换**当前页，数据代次过期即丢弃（沿用 `_result_is_stale`）。
- 页面呈现：二维热图（默认）+ 等高线选项；多条带时提供带选择器与逐带着色。
- 数据存放：宿主在内存注册表（`page_id -> SurfaceResult`）持有只读数组，`spec.params` 只放可序列化元数据（`surface_kind`、标签、单位、带数与颜色、来源快照 id/代次）。**不**把几万个浮点塞进 params（1D 的 `base_curve` 列表做法不适合 2D）。
- 导出：走宿主二维数据导出路径，导出 x/y/z（多带按带分组）+ 元数据；至少覆盖数值 npz/csv，色图出图沿用宿主既有出图能力。

## 5. 能力 C：`render_overlay_surface`（阶段 3 实现，契约现在评审）

- **不加插件侧调用接口**：宿主在创建 `plugin_surface` 结果页时，自动把该页的带面登记为「3D 叠加图层」；插件只通过 `BandSurface.color` / `opacity` 提供显示偏好。
- 宿主职责：把面转成场景几何（与体渲染同一套物理坐标映射）、着色、随体渲染的 ROI/裁剪/旋转联动、逐带显隐开关（宿主 UI，放在「渲染控制」页）。
- 失效与清理：数据代次变化、来源页/结果页关闭、插件停用后重启时，叠加图层随之清除；不写回任何强度数据。
- 坐标系：快照不含显示旋转，叠加渲染时宿主按当前 `rotation_angle` 对叠加层施加同样的旋转（与体数据同一变换），保证对齐。

已核实的宿主渲染事实（2026-10-07 调查，供阶段 3 实现参照）：

| 事实 | 位置 |
|---|---|
| 场景世界坐标 = **绝对体素索引 × spacing**，`spacing[axis] = 200/(full_shape[axis]-1)`，域映射到 `[0,200]³` | `render_core.py:13-33`、`1293-1296` |
| ROI 裁剪时 `extent = data_bounds`（绝对索引），与全量路径逐点等价 | `render_core.py:13-33` |
| 目前**没有**通用叠加 actor 机制；`VolumeRenderSession.clear()` 只移除 `main_vol` 与 3D 色条，其它 actor 必须自管生命周期 | `render_core.py:1058-1081` |
| `RefreshCause.OVERLAY` 已存在且属于「无计算直接重绘」类别 | `refresh_pipeline.py:15-24`、`refactored_app.py:1387-1390` |
| 两种旋转要分开处理：数据级旋转（`_get_rotated_frame`，体数据本身被旋转）与 actor 级滚轮预览旋转（`SetOrigin/SetOrientation`） | `refactored_app.py:2835-2905`、`5790-5812` |
| 显示 E 翻转会同时翻转数据与坐标，叠加层必须跟随 | `refactored_app.py:3036-3090` |
| 插件宿主的 `request_refresh` 目前固定发 `TRANSFER_FUNCTION`，插件若需驱动叠加刷新要扩展该桥 | `plugin_host.py:732-745` |

因此阶段 3 的落地方式：新增叠加层管理器（`plotter.add_mesh(..., name=...)`）挂在 3D 刷新末尾与 `_invalidate_scope_render_state` / `closeEvent` 清理路径；叠加点的世界坐标由「面网格索引 → `coords['X']/['Y']/['E']` 物理值 → 逻辑索引 → ×spacing」换算，并跟随 E 翻转与旋转设置。
- 首版不做：曲面点击取数、动画、逐点置信度着色。

## 6. 长任务：进度、取消与生命周期

进度回传（建议加在 `CancelToken` 上，**不改工作函数签名**，新旧插件零破坏）：

```python
@dataclass
class CancelToken:
    ...
    def report_progress(self, fraction: float, message: str = "") -> None:
        """可选：向宿主汇报进度（0–1）。未接线时是无副作用的空操作。"""
```

- 宿主把进度节流（≤5 Hz）后路由到主线程：更新分析面板状态行，并回调新增的可选钩子
  `Plugin.on_analysis_progress(handle, fraction, message)`（默认空实现，v1/v2 插件不受影响）。
- 取消沿用 `AnalysisCancelled` + 检查点语义；未开始的任务直接出队。重构应在逐带循环与 L-BFGS-B 的 `callback` 里检查取消。
- 宿主**不强制结束线程**（现状不变）；插件必须在检查点退出，退出前不写任何文件、不碰控件。

## 7. 兼容、构建与打包影响

| 项目 | 改动 |
|---|---|
| `compat.py` | 新增 3 个能力常量、`ANALYSIS_3D_CAPABILITIES`、`SUPPORTED_API_VERSIONS` 加 3、`SUPPORTED_CAPABILITIES` 加 3 项 |
| `api.py` | 重导出新常量；新增 `AnalysisInput3D`、`BandSurface`、`AnalysisSurface2D`、`validate_analysis_surface`、`PluginHostV3`、`CancelToken.report_progress` |
| `analysis_host.py` | 进度信号、体数据快照的登记/释放（必要时体数据任务限流为 1） |
| `plugin_host.py` | `_HostBridge` 升 v3；`capture_analysis_input_3d`；结果页/叠加层注册表 |
| `refactored_app.py` | `plugin_surface` 页的渲染上下文与导出载荷；分析面板按快照能力的显隐 |
| 打包 | 插件依赖白名单不变（只用 numpy/scipy，由宿主提供）；`.bsplugin` 体积不因本功能增加 |

兼容核对：宿主当前为 `{1, 2}` 支持集合，声明 `api_version: 3` 的插件在旧宿主上会被安装/加载直接拒绝并给出「需要接口版本 3」的原因——这是预期行为，不需要额外处理。

## 8. 待评审问题清单

1. `AnalysisSurface2D` 一次携带**多条带**（本提案）还是严格按计划原文的单面？
2. 三维快照取数：是否对未落盘的去噪结果取数（本提案：取，与三维视图一致）；是否允许从二维页抓三维快照（本提案：允许）。
3. 快照上限 `MAX_SNAPSHOT_3D_BYTES` 取多少（本提案建议 1 GiB，超出给可展示原因）。
4. 进度接口放 `CancelToken.report_progress`（本提案）还是改工作函数签名。
5. 结果数组放内存注册表（本提案）还是塞进 `spec.params`（沿 1D 的 `base_curve` 做法）。
6. 3D 叠加首版是否接受「宿主代偿显示旋转」（本提案：接受；若不做，则快照需带旋转、坐标同时旋转）。
7. `render_overlay_surface` 与 `result_surface_2d` 的组合约束放在 `compat` 还是宿主加载期（本提案：宿主加载期给可读原因）。

## 9. 不做的事

- 不改动任何现有能力（`opacity_multiplier` / `data_snapshot_2d` / `analysis_task` / `result_curve_1d`）的语义与校验；
- 不为插件开放 VTK/Qt 或主窗口私有字段；
- 首版不做三维快照的 GPU 传输、增量更新、跨会话持久化。
