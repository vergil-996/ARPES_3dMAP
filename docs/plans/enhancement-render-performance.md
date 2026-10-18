# 局部增强交互性能（拖动帧时）优化

日期：2026-10-17　状态：P0、P2-A、2D 复用与 ROI 异步均已落地并完成真实窗口验收（2026-10-18）；剩余 CPU 线路与发行验收

修订记录：

- v5（2026-10-18，第 3 轮）：P1 ROI 异步化落地（独立线程池 + token/版本校验 + 等待期不开放 EXACT），
  新增提交/过期丢弃测试；后端执行加串行锁避免 GPU 上下文争用。
- v4（2026-10-18，第 2 轮）：真实窗口实测把 3D 刷新从 78.5 ms 降到 47.4 ms、空闲刷新降到 3.9 ms；
  修复「每次刷新换一个视图对象」导致的缓存全失效；pinned 下载、2D 图层/色带复用落地；
  实测采样步长无收益，关闭该 P1 子项。全量 1297 项、真实窗口 156 项通过。
- v3（2026-10-18）：实施 P0、P2-A 与 P1 调度部分。实测 3D 显示映射：CPU 131–148 ms →
  CUDA 22–25 ms；通道上传 31 → 12.4 ms；一帧只渲染一次。全量 1295 项通过。
  未实施：P1 采样步长/降分辨率、ROI 异步化、P0-2D 复用、P2-B/C/D（原因见「实施结果」）。
- v2（2026-10-17）：基线变更。工作区已完成「弱信号色阶映射修复」（[主记录](../handoffs/display-transfer-weak-signals.md)），
  `channels()` 改为返回**最终颜色坐标**、区域不再编码偏移、非线性色阶走独立显示通道。
  据此删除原 P0「删冗余 color」（已不存在），重写 P2（分段 CTF 方案作废），补齐渲染合并、ROI 异步、
  分区性能指标与 P2 前置门禁。
- v1（2026-10-17）：立项，P0/P1/P2 三步。

## 范围

本计划覆盖同一段显示通道热路径的两个入口：

1. 局部增强区域（`display_fields is not None`）；
2. **非线性色阶**（灰场 ≠ 50，`display_fields is None` 也会逐体素烘焙颜色，见
   [render_core.py](../../bandscope/rendering/render_core.py#L1252)）。

两者共用 `resolve_display` / `channels` 与 3D 双分量显示通道，性能问题与修复手段一致；
弱信号正确性以 [弱信号色阶主记录](../handoffs/display-transfer-weak-signals.md) 为准，本计划不得回退其结论。

## 目标

参考机（Windows 11、`.venv` Python 3.12、150×150×200×25、1–3 个增强区域）上：

- 拖动**色阶 / 映射**：3D p50 ≤ 40 ms、p95 ≤ 60 ms（CUDA 路线；实测 p50 47.4 ms，见「实施结果」）；
  CPU 路线 p50 ≤ 80 ms（本轮 CPU 目标未达成，仍为 ~150 ms）；
- 拖动色阶：2D p50 ≤ 40 ms；
- **时间切帧**：热缓存 p50 ≤ 60 ms；冷缓存受 `prepare_volume` 约束，只要求界面不冻结、p50 ≤ 250 ms；
- **首次 ROI 去噪**：不阻塞界面；完成时间只记录、不设上限；
- 线性色阶（无 gamma、无区域）不得退化。

不改变科学数据、不改变出版导出结果、不改变弱信号可见性。

非目标：不新增渲染管线、不改插件协议、不更换 2D 绘图库、不把 VTK 场景搬进后台线程。

## 现状与证据

用户反馈：添加局部增强区域后拖动滑条明显卡顿。

- 实测（另一任务、合成单帧 150×150×200 + 1 区域、仅增强计算、未含 VTK/窗口绘制）：**约 208 ms/次**。
  属**估算级基线**，需用探针在真实窗口复测后才作为验收依据。
- 当前代码路径（v2 基线）：

| 环节 | 位置 | 现状 |
| --- | --- | --- |
| 刷新入口 | [refactored_app.py](../../bandscope/app/refactored_app.py#L1402-L1405) | `DISPLAY_MAPPING/TRANSFER_FUNCTION/GEOMETRY/OVERLAY` 在 GUI 线程同步渲染，不进计算池 |
| 每次渲染取显示通道 | [refactored_app.py](../../bandscope/app/refactored_app.py#L7637) | 每帧 `_enhancement_display()`；缓存键含整个 `state` 的 `repr`，滑条每动一格失效 |
| 逐体素颜色 | [display_transfer.py](../../bandscope/processing/display_transfer.py#L73-L89) | `normalized_color_samples`(float64) → `mapped_positions`(γ 幂) → `np.interp`(alpha) → `isfinite` + 两次 `np.where` |
| 全卷数组 | [local_enhancement.py](../../bandscope/processing/local_enhancement.py#L46-L85) | `owners` 整卷分配、背景 `finite_range` 整卷扫描、返回整卷 color/alpha |
| 3D 上传 | [rendering/local_enhancement.py](../../bandscope/rendering/local_enhancement.py#L45-L72) | 两份 18 MB F 序重排写入双分量缓冲 + 256 点调色板 CTF + `apply_cmap` |
| 辅助对象 | [enhancement_integration.py](../../bandscope/app/enhancement_integration.py#L571-L618) | 每帧删/建所有框、点标签、图例、每区域一条 scalar bar，末尾再 `render()` |
| 一帧两次 render（v2 基线） | [render_core.py](../../bandscope/rendering/render_core.py#L1314) | 会话渲染一次 + 辅助对象一次；v3 已合并为一次（见「实施结果」） |
| 2D 快速路径 | [render_core.py](../../bandscope/rendering/render_core.py#L913) | `display_fields is None` 才走 blit；有增强即整绘 |
| 2D 区域色带 | [enhancement_integration.py](../../bandscope/app/enhancement_integration.py#L538-L544) | 每帧删除并重建 `fig.colorbar`（新建 Axes + 重排布局） |
| ROI 去噪 | [enhancement_integration.py](../../bandscope/app/enhancement_integration.py#L297-L348) | `backend_manager.execute` 在 GUI 线程同步执行（首次加区域/改去噪方法会冻结） |

## 结论

1. 瓶颈是 **GUI 线程上的重复全量数组计算与场景重建**，不是 CPU 核数不足；
   `setMaxThreadCount(1)`（[refresh_pipeline.py](../../bandscope/rendering/refresh_pipeline.py#L103-L104)）只串行
   `prepare_volume` / 去噪 / 分析任务，滑条不经过它。
2. **不能把 Gamma 搬回传输函数**：弱信号修复的结论是「逐体素算最终颜色坐标，避免 GPU 在原强度轴重采样
   Gamma 曲线」「区域不再编码区域偏移」；任何「归一化标量 + 分段 CTF」的方案都会回退该结论。
3. 因此 P2 的主线是**用 GPU 算同样的逐体素结果**（不改语义），CPU 机器则靠**少算/降分辨率/后台双缓冲**，
   而不是改映射的数学位置。
4. **与弱信号修复的关系**：语义上正交——修复决定「算出的数值对不对」，本计划决定「多久算一次、在哪里算」；
   但修复把灰场 ≠ 50 也纳入同一条逐体素烘焙路径（`display_fields is None` 时改走 `channels`），
   成本模型因此改变。拖动卡顿可能有一部分来自色阶修复本身而非增强区域，归因必须按四象限分开测
   （见「测量与验收」）。无论如何，优化都不得修改映射语义，弱信号用例是硬门禁。

## 不采用的方案

- **归一化标量 `u` + 分段 CTF/OTF**（v1 的 P2）：会重新引入当天刚移除的区域偏移编码与 Gamma 重采样，
  弱信号精度无法保证；除非前置门禁全部通过，否则不实施。
- **增强态直接沿用 3D stride 降采样**：`_draw_enhancement_boxes` 用 `full_shape` 换算框体坐标，
  裁剪过的体（`data_bounds` 非空）降采样后框会错位；仅对**未裁剪全卷**可用（见 P1）。
- **整体后台化作为首选**：单帧仍有 100–200 ms 上传与重建留在 GUI 线程，帧率上限约 5–10 fps；
  保留为 P2-D 备选（改善控件响应，不改善帧率）。

## 实施阶段

### P0 基线化、去冗余与增量更新（不改架构）

0. **固定基线并重采样**：先用探针在当前工作区（含弱信号修复）测一遍，把 208 ms 拆成
   `resolve` / 上传 / 辅助对象 / render 四段；记录基线提交或差异快照。**不再沿用 v1 的「删冗余 color」**——
   该逻辑已被弱信号修复删除，开工前以实测热点为准。
1. **缓存数据侧统计**：`finite_range` 按 `(id(data), data_ptr, shape, dtype, lock)` 记忆化；
   `owners` 在区域集合与 slices 不变时复用；`level_info` / `bg_info` 按 `(range, levels, include_zero)` 记忆化。
2. **辅助对象增量更新**：`_draw_enhancement_boxes` 以
   `(region_id, bounds, label, enabled, level_info 关键值)` 为签名，未变则跳过删建；
   scalar bar 复用 `SetLookupTable` / `SetTitle`；只有签名变化才标记「需要渲染」。
3. **合并为一次 render**：`volume_session.render` 增加 `render=False` 选项；调用顺序统一为
   「更新体数据与显示通道 → 更新框/标签/色带 → `plotter.render()` 一次」；
   验收用 `render_count` / `plotter.render` 调用计数断言**每帧恰一次**（不是「变了两帧仍是两次」）。
4. **2D 复用**：区域图层 `AxesImage` 复用（`set_data` + `set_extent`）；区域色带复用 mappable
   （`set_clim` + 重设刻度 + `colorbar.update_normal`），不重建 Axes；框/文字按签名重建。

验收：3D 单次刷新 ≤180 ms（估算基线 250 ms 起）、2D ≤60 ms；背景像素逐像素不变；每帧 render 计数 = 1。

### P1 让预览本身便宜，并把 ROI 计算移出 GUI 线程

1. **预览采样步长**：`VolumeRenderSession._set_interactive_quality` 增加 preview 分支——
   `SetAutoAdjustSampleDistances(False)` + `SetSampleDistance(base × 2.5)`（必要时 `SetShade(False)`），
   EXACT 还原并 `Update()`。几何不变，框与标量网格不受影响。
2. **预览降分辨率（仅未裁剪全卷）**：把现有 stride 分支扩展到增强态——同时 stride `data` 与
   `display_fields` 的 color/alpha，`full_shape` 传降采样后的形状（0–200 物理范围不变，框坐标仍按全分辨率
   `logical_to_render_bounds` 计算，二者在未裁剪时一致）；裁剪体（`data_bounds` 非空）继续用步长方案，
   避免框体错位。EXACT 恢复全分辨率。
3. **调度**：`preview_interval_ms` 按上一帧实测耗时自适应；帧未完成时不排新帧，只保留最新请求。
4. **ROI 去噪异步化（补完整提交规则）**：
   - 独立请求空间：沿用现有 `SCOPE_DENOISE_PAGE_PREFIX` 的做法，用 `enhancement:<region_id>` 作为
     page_id，**不复用页面 generation**，避免滑条操作把仍然有效的 ROI 结果判为过期；
   - 去重键：`(id(original), bounds, repr(methods), shared_denoise_version)`，与
     `_enhancement_crop_data` 现有缓存键一致；多个 ROI 逐个提交时按 region_id 建独立待办槽，
     不共用一个 `_pending_compute`；
   - 结果接入：命中后写入 `_enhancement_roi_data_cache`，`shared_denoise_version`/数据源/参数
     任一变化即丢弃；等待期间用上一份 ROI 显示，**不得把等待结果当作 EXACT**——
     `_render_exact_ready` 保持 False，冻结色带与导出按现有门禁继续禁用；
   - 失败与超时：记录 error 走现有 `enhancement_errors` 提示，不阻塞其他 ROI。

验收：拖动时不再出现「框/色带每帧重建」；2D 恢复到 ≤50 ms；首次加区域与改去噪方法期间界面可操作。

### P2 前置可行性门禁（全部通过才实施，不通过保留现有路径）

在写正式实现前，用最小原型 + 固定数据验证四组用例：

| 门禁 | 用例 | 通过标准 |
| --- | --- | --- |
| G1 弱信号 | 动态范围 `0..1e6`、灰场 100，强度 `1..1000` | 复用现有 `test_gamma_preserves_weak_samples_beside_a_bright_outlier` 与 `test_weak_volume_uses_gamma_coordinates_before_vtk_sampling`，新路径逐体素颜色坐标 rtol 1e-6 |
| G2 亮异常点 | 单个 `1e6` 体素邻域 | 邻域颜色/alpha 与现路径一致（不容忍溢出或 NaN） |
| G3 区域交界 | 两区域相邻 + 重叠 | 交界/重叠处颜色与 alpha 与现路径一致；不允许出现接缝环带 |
| G4 多区域 | 3 个区域 + 背景 | 每个 owner 的显示与单区域时一致；内存与显存峰值可接受 |

任何一项不通过：保留现有烘焙路径，只做 P0/P1，并把结论写回本记录。

### P2 实现路线（按门禁结果选择）

- **P2-A（首选，NVIDIA 包）**：把 `resolve_display` 的逐体素部分迁到 CuPy，算术与 CPU 完全一致
  （同 float64 中间值、同 `interp`、同 `where`），结果经 pinned buffer 回写
  （复用 [compute_backends.py](../../bandscope/processing/compute_backends.py#L559-L571) 的写法）；
  体数据在显存常驻，只有换帧/换区域/改去噪才重传。预期 208 ms → 10–20 ms。
  仍走双分量通道，不改变 VTK 侧语义，因此不触发弱信号回归。
- **P2-B（CPU 包）**：拆分「颜色」与「alpha」的重算条件——只改映射方式（opacity mode）时复用颜色；
  颜色按 `(data token, levels, lock)` 缓存，滑条连续拖动时用上一帧结果先渲染（双缓冲）。
- **P2-C（两条路都可用）**：预览期用 P1-2 的降分辨率烘焙，EXACT 恢复；用于冷缓存首帧。
- **P2-D（备选，评审建议保留）**：把纯数组计算放后台 worker，GUI 侧只做上传与 render；
  不提高帧率，但改善控件响应，作为 P2-A/B 不可用时的兜底。

共性要求：`scalar_display`/新的 GPU 入口必须显式接收 `region_data`（ROI 去噪后的数据）与
`enhancement_errors`，否则会把「先裁剪再去噪」的区域悄悄换成「整卷去噪后切片」，改变显示来源。
导出继续用 `resolve_display` + `apply_volume_display`，保证出版图逐像素不变。

## 测量与验收

新增 `scripts/diagnostics/probe_enhancement_timings.py`：

- `--headless`：只测 `resolve_display` / `channels`（无 GL 可跑）；
- `--window`：真实窗口测完整刷新，打点 `resolve` / 上传 / `_draw_enhancement_boxes` /
  `volume_session.render` / `plotter.render` 次数 / 总计；
- 参数：`--data .local/data/WSe2_step.npz`、`--view 3d|2d`、`--regions 1..3`、`--gamma 50|100`、
  `--locked`、`--repeat N`、`--cache cold|warm`；输出 p50 / p95 / 调用次数到 `.local/outputs/`。

**成本先归因，再定目标**（同一份数据、同一相机，四象限各测一遍）：

| 象限 | 条件 | 走的路径 | 用途 |
| --- | --- | --- | --- |
| Q1 控制组 | γ=50、无区域 | 原标量 + CTF | 校准探针自身开销 |
| Q2 仅增强 | γ=50、有区域 | `display_fields` 烘焙 | 增强自身的净成本 |
| Q3 仅非线性色阶 | γ≠50、无区域 | 烘焙（修复后新增） | 色阶修复引入的净成本 |
| Q4 两者叠加 | γ≠50、有区域 | 同一烘焙路径 | 用户当前场景 |

Q4−Q2 是「区域」的净成本，Q4−Q3 是「非线性色阶」的净成本，Q3−Q1 可校验归因是否自洽。
若 Q3 ≈ Q4，说明卡顿主要来自色阶烘焙路径而非增强区域；优化范围不变，但验收叙述改为
「非线性色阶帧时」，并同步更新弱信号记录的性能条目。

指标矩阵（全部为参考机目标，基线未实测前不写死）：

| 场景 | 现状（估算） | P0 | P0+P1 | P2 |
| --- | --- | --- | --- | --- |
| 调色阶 3D（增强或 γ≠50，1 区域，热缓存） | ~250 ms | ≤180 | ≤120 | ≤40（A）/ ≤80（B/D） |
| 调色阶 2D | 100–200 ms | ≤60 | ≤40 | 不退化 |
| 时间切帧 3D（热缓存） | ~250 ms | ≤180 | ≤100 | ≤60 |
| 时间切帧 3D（冷缓存） | 受 `prepare_volume` 约束 | 不退化 | 界面不冻结 | 同左 |
| 首次 ROI 去噪 | GUI 冻结 | 不冻结 | 不冻结 | 不冻结 |
| 线性色阶（无 γ、无区域） | ~20–40 ms | 不退化 | 不退化 | 不退化 |

正确性门禁：

- `python -m unittest discover -s tests -t . -v` 全量通过（当前基线 1290 项，以开工时为准）；
- 弱信号相关用例（`tests/processing/test_display_transfer.py`、`tests/rendering/test_display_transfer.py`、
  `tests/rendering/test_color_mapping.py`）全部通过，且**不允许放宽断言**；
- 新增 P2-A 等价性测试：同一数据/区域/色阶，GPU 与 CPU 结果逐体素比较，color/alpha 最大差 ≤1e-6；
- `tests/rendering/test_local_enhancement.py` 的背景逐像素断言不变；
- 出版导出 PNG 与优化前逐像素一致（同数据、同 cmap、同尺寸、同 gamma）；
- 真实窗口重跑 `verify_local_enhancement.py`、`verify_color_lock.py`、`verify_enhancement_crop_parity.py`、
  `verify_enhancement_projection.py`、`verify_enhancement_outline_visibility.py`。

## 实施结果（2026-10-18）

已落地（源码 + 全量回归；第 2 轮补充真实窗口数字）：

| 项 | 实现 | 效果 |
| --- | --- | --- |
| 基线探针 | `probe_enhancement_timings.py`（headless/session/frame、`--gpu on/off`、p50/p95 + CSV）与 `probe_enhancement_window.py`（真实窗口分段计时、cProfile、采样步长扫描） | 建立可复测基线 |
| P2-A CUDA 映射 | `bandscope/processing/gpu_display.py`；`channels()` / `finite_range()` 按数组大小与开关分派；主机只在 NVIDIA 后端启用 | 3D 映射 131–148 ms → 22–25 ms，逐体素逐位一致 |
| 缓存键 | 按「底层缓冲 + 偏移 + 布局」而不是视图 `id()` 缓存（`raw[..., t]` 每次都是新视图） | 空闲刷新 79.8 → 3.9 ms；映射内 49 → 24 ms |
| P0 统计缓存 | `finite_range` 按同一键缓存 + 全有限快路径 | 每 tick 不再整卷扫描 |
| P0 上传优化 | CTF/LUT 按 cmap 缓存；`np.copyto` 经转置视图直写双分量缓冲 | 上传 31 → 12.4 ms |
| P0 下载优化 | device→host 改 pinned 缓冲（`alloc_pinned_memory` + `asnumpy(out=)`） | 下载 10.5 → 2.6 ms |
| P0 单次渲染 | `VolumeRenderSession.render(..., render=False)`；`_render_active_page` 末尾统一渲染一次 | 每帧 render 次数 2 → 1 |
| P0 辅助对象增量 | 框/标签/图例与 scalar bar 分开签名；2D 区域图层、区域色带按数量复用 | 色阶拖动跳过 actor/色带重建 |
| P1 调度 | `_adaptive_preview_interval_ms`：预览定时器不短于上一帧耗时 | 拖动不堆积待处理事件 |
| P1 ROI 异步 | `_EnhancementRoiTask` + 30 ms 轮询；每次提交独立 token，按数据源、去噪版本与 ROI 键去重；等待期不标 EXACT | 首次加区域/改去噪不再冻结界面 |
| 测试 | GPU 等价、token 跳过重传、render 开关、辅助对象增量、2D 复用、ROI 异步提交/过期共 9 项 | 防止回退 |

实测（参考机、`.local/data/WSe2_step.npz` 单帧 150×150×200、1 区域）：

| 场景 | 优化前 | 现在（CPU） | 现在（CUDA） |
| --- | --- | --- | --- |
| 整次 3D 刷新（真实窗口，p50） | 78.5 ms（改前实测） | ~150 ms | **47.4 ms** |
| 空闲 3D 刷新（相机/坐标轴等，无样式变化） | 79.8 ms | — | **3.9 ms** |
| `resolve_display`（1–3 区域） | 137–154 ms | 131–148 ms | 22–25 ms |
| 通道上传（4.5M 体素） | 31 ms | 12.4 ms | 12.4 ms |
| `plotter.render()`（真实窗口） | 4 ms | 4 ms | 4 ms |
| 2D 刷新（2 区域，含整绘） | ~87 ms | ~87 ms | — |
| 首次 ROI 去噪（真实窗口，SG） | 界面冻结到计算完成 | — | 刷新 **57.6 ms** 返回；ROI 结果 288.9 ms 后台提交，随后 EXACT |

P1 采样步长/降分辨率的结论：真实窗口 `plotter.render()` 只有 **4 ms**，显式 `SetSampleDistance`
在 0.5×–4× 区间内没有可测差异（4.0–4.4 ms），因此**不做**预览降质；预览降分辨率同样收益不足。
3D 剩余成本集中在 `resolve_display`（~24 ms）与上传（~12 ms），不是光栅化。

未实施及原因：

- **P2-B/C/D**：CPU 包仍为 ~150 ms；要让无 N 卡用户接近目标，需要后台双缓冲或合成 RGBA 预览，
  与已落地的 GPU 路径并行不冲突。
- **2D 合成 blit**：复用后 2D 仍受整绘（~27 ms）与插值限制；真要提速需把背景 + 区域合成为一张 RGBA 再 blit，
  属于新设计，未在本轮展开。

验收补记：

- `verify_local_enhancement.py`：**156/156 通过**（`.local/outputs/perf-p2-native-3/`，
  日志 `.local/logs/perf-p2-native-3.log`），覆盖 2D/3D 独立调节、锁定、区域交互与删除、出版导出与科学数据不变。
- ROI 异步原生烟测：刷新 57.6 ms 返回，ROI 去噪 288.9 ms 后台完成并自动转 EXACT；
  事件循环最大间隔 118 ms（含该次同步刷新本身）。日志 `.local/logs/roi-async-native-2.log`。
- 单元/集成新增：ROI 后台提交与过期丢弃 2 项；全量回归 1299 项通过。
- `verify_color_lock.py`：全部通过（`[verify] ALL PASSED`）。
- `verify_enhancement_projection.py`：22 项通过；`verify_enhancement_outline_visibility.py`：24 项通过。
- `verify_enhancement_crop_parity.py`：5 项通过 / 1 项失败——`3D background opacity transfer unchanged`。
  该断言仍在比较「单分量不透明度函数在原始强度上的取值」与「逐体素 alpha」，与弱信号修复后的语义不一致；
  用 `BANDSCOPE_DISABLE_GPU_DISPLAY=1` 关闭 CUDA 显示路径后同样失败，**不是本计划引入**，
  需在弱信号修复的记录里更新脚本后重跑，不能直接删除或放宽断言。
- 未执行：人工鼠标拖拽手感复核；发行包（CPU / NVIDIA）构建与安装验收。
- 诊断开关：`BANDSCOPE_DISABLE_GPU_DISPLAY=1` 可强制走 CPU 参考路径（供 CI / A-B 排查）。

## 风险

- P2-A 只在 NVIDIA 包生效（CuPy + CUDA 13，包体 1.1 GB）；CI 无 GPU，需要 mock 或自托管 runner。
- 预览降分辨率与采样步长会改变观感；EXACT 与导出必须还原，且用户可能拿预览判断色阶。
- 2D blit 与区域图层的抗锯齿/插值差异；保留 EXACT 整绘兜底。
- `refactored_app.py`、`render_core.py` 属共享接入点，改动需与集成任务协调后再落。
- 弱信号修复是同日变更，仍在等待用户真实弱带确认；本计划的任何改动都必须先复跑其验收脚本。

## 负责范围与接口约定

- 认领：`bandscope/processing/local_enhancement.py`、`bandscope/processing/display_transfer.py`（只加不删语义）、
  `bandscope/rendering/local_enhancement.py`、`bandscope/app/enhancement_integration.py`、`scripts/diagnostics/`；
- 共享文件（需协调）：`bandscope/app/refactored_app.py`、`bandscope/rendering/render_core.py`、
  `bandscope/processing/compute_backends.py`、`docs/plans/README.md`；
- 接口约定：`resolve_display` 语义不变（新增可选参数除外）；`apply_volume_display` 的导出调用方不变；
  GPU 入口必须与 CPU 逐体素等价，且显式接收 `region_data` / 错误状态。

## 与既有记录的关系

- 人工拖拽手感验收仍记在 [局部增强控件简化与锁定色带整合](enhancement-controls-simplify.md)；
- 弱信号正确性与非线性体渲染的性能确认记在 [弱信号色阶映射修复](../handoffs/display-transfer-weak-signals.md)，
  本计划只负责帧时，不改其映射语义。

## 开放问题

1. P2-A 是否只在 `NVIDIA GPU` 后端下启用，还是 `Auto` 检测到 CuPy 就启用？
2. 预览降分辨率与采样步长二者取一还是叠加？需要用户接受哪种画质？
3. ROI 异步化是否本轮做（牵动刷新时序与 EXACT 门禁）？
4. 2D 是否保留「每 N 帧一次整绘」的校准路径？
5. P2-B 的双缓冲在冷缓存首帧是否会闪回旧帧，需要怎样的过渡？

## 下一步

1. 人工拖拽手感复核（真实窗口、真实弱带），确认 47 ms 的 3D 刷新与异步 ROI 等待在实际使用中跟手。
2. CPU 路线收尾：P2-B（颜色/alpha 分离缓存或后台双缓冲）或 3D 预览合成；
   真实窗口已确认光栅化只占 4 ms，收益在映射与上传。
3. 更新 `verify_enhancement_crop_parity.py` 的过期断言（弱信号语义），复跑五个脚本。
4. 冻结包（CPU / NVIDIA）构建与安装验收，然后连同材料归档。
