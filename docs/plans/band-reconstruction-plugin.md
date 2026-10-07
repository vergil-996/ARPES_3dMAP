# 能带重构插件（MRF Band Reconstruction）总体计划

- 状态：**阶段 0–3 已全部实施完成（2026-10-07）**；剩余的是人工验收——真实窗口
  视觉复核（用户选择先不做，交接里保持"未执行"标记）与真实实验数据核对。
  分阶段交接：[阶段 1](../handoffs/band-reconstruct-stage1-2026-10-07.md)、
  [阶段 2](../handoffs/band-reconstruct-stage2-2026-10-07.md)、
  [阶段 3](../handoffs/band-reconstruct-stage3-2026-10-07.md)；
  接口契约：[阶段 0 提案](band-reconstruct-api-proposal.md)（已由用户裁决）。
  计划仍放在这里而不是归档：人工验收没有关闭之前，它还是当前计划。
- 创建：2026-10-07
- 文献依据：Xian & Stimper et al., "A machine learning route between band mapping and band structure", *Nature Computational Science* 3, 101–114 (2023), DOI 10.1038/s43588-022-00382-2。参考代码 mpes-kit/fuller（MIT 协议，TF1 实现，本项目不直接使用，仅作算法参照）。
- 论文 PDF 与提取文本：`.local/data/papers/natcomscie_2023_Patrick.pdf`、`.local/outputs/natcomscie_2023_Patrick_text.txt`（不提交）。

## 一句话目标

把论文的 MRF 能带重构方法用 NumPy/SciPy 复现，做成 BandScope 插件 `band_reconstruct`：从宿主加载的 3D 体数据 I(kx, ky, E) 中逐带重构二维色散面 E_b(kx, ky)，先以 2D 结果页呈现，最终叠加渲染进 3D 视图。

## 全局边界（所有任务必须遵守）

1. 所有新代码使用 `bandscope.*` 绝对导入；禁止向 `sys.path` 添加目录。
2. 插件侧只依赖宿主已提供的 numpy（≥2.0）和 scipy（≥1.13）；**禁止引入 TensorFlow / PyTorch**（打包白名单也不允许）。
3. 插件不直接访问主窗口私有字段、不直接操作 VTK；跨边界能力一律走 `bandscope.extensions` 公开接口。接口不存在就先在阶段 0/2 提案，不允许绕过。
4. 测试不得依赖本机实验文件、注册表设置或已安装插件；合成数据在测试内现算。Qt 测试设置 `QT_QPA_PLATFORM=offscreen` 后再导入 PyQt5。
5. 涉及共享文件（`bandscope/extensions/*`、`bandscope/app/refactored_app.py`、构建/打包配置）的改动只能由集成任务执行，其他任务只提交接口需求。
6. 每阶段完成后在 `docs/handoffs/` 写交接：改了什么、验证了什么、哪些检查未执行及原因。未完成视觉验收不得写成已通过。

## 算法规格（阶段 1 的实现依据，所有任务共享）

**输入**：3D 强度体 I[kx, ky, E]（float，宿主中为 `core.raw_data[..., 0]`，轴向顺序 [X, Y, E]）+ 三轴坐标 + 单位。

**预处理**（对体数据）：
1. 高斯平滑，σ = (0.8, 0.8, 1.0) 像素（kx, ky, E）。
2. MCLAHE 对比度增强：默认 n_bins=128, clip_limit=0.01, threshold=1e-6，kernel 为各轴长度的 1/8。可用 scikit-image 的 CLAHE 沿 E 轴切片实现或直接移植 fuller/mclahe 的 NumPy 逻辑；允许近似，需在交接中说明差异。
3. 对称化（可选，首版不做）。

**核心模型**：每条带一张能量面 Ẽ[i, j]（i, j 为动量网格点），最小化负对数后验（论文 eq. 4）：

```
L = -Σ_ij log I(kx_i, ky_j, Ẽ_ij) + Σ_{(ij),(lm) 最近邻} (Ẽ_ij - Ẽ_lm)² / (2η²)
```

- 强度项用三线性插值在 (kx_i, ky_j, Ẽ_ij) 处取值；log 前对小强度截断（如 clip 到 ε=1e-6·max）保证可微与数值稳定。
- η 默认 0.1（fuller 默认 `eta=0.1`）。可选曲率先验 `includeCurv` 首版不实现。
- 优化器：scipy L-BFGS-B（能量面限制在 E 轴量程内）或坐标下降；参考预算：fuller 为 100 epoch。首版以"收敛 + 精度达标"为准，不追求论文的 7 s/带速度。
- 逐带顺序重构，带数 N_b 由用户指定。

**初始化面**（warm start，必须携带正确的带交叉信息）：
- 解析函数：抛物面 / 高斯面 / 平面，参数用户可调；
- 外部导入：文本/npz 的 E(kx, ky) 网格（DFT 结果），双线性插值到数据网格；
- 每条带支持两个对齐参数：动量缩放、能量刚性平移（论文三超参数之二，之三是 η）。

**验收指标**（论文 eq. 9/10）：
- η_avg = sqrt(mean((E_recon − E_truth)²))，论文在 18 meV 能量间隔下为 40–50 meV/带；
- η_rel = ‖E_recon − E_truth‖₂ / ‖E_truth‖₂。

## 阶段 0 — 接口契约提案（纯文档，先行发起）

- **目标**：把插件与宿主之间的能力契约写成提案，交集成任务评审，解除后续并行等待。
- **认领目录**：仅 `docs/plans/`（新增 `band-reconstruct-api-proposal.md`）。**不改任何代码**。
- **提案内容（草案要点，评审可改）**：
  1. 新能力 `data_snapshot_3d`：`capture_analysis_input_3d() -> AnalysisInput3D`，返回只读体数据（轴向 [X, Y, E]）+ 三轴坐标 + 单位 + 数据来源溯源（参照现有 `AnalysisInput2D` 的设计与 `data_generation` 防过期机制）。
  2. 新能力 `result_surface_2d`：分析任务允许返回 `AnalysisSurface2D`（z[i, j] 2D 网格 + x/y 坐标 + 带标签 + JSON 参数），宿主据此创建能带面结果页（2D 等高线/热图呈现）。
  3. 新能力 `render_overlay_surface`（阶段 3 才实现，但契约一并评审）：宿主把能带面注册为 3D 视图叠加图层，负责着色、显隐、随 ROI/裁剪联动。
  4. 长任务约束：体数据快照大（典型 256×256×470 float32 ≈ 115 MB），需约定快照生命周期与内存策略；重构耗时分钟级，需进度回调与协作式取消（沿用 `CancelToken`）。
  5. 兼容性：能力字符串加入 `compat.py` 支持列表的方式、`api_version` 是否升到 3。
- **验证命令**：无（文档评审）。
- **交付物**：`docs/plans/band-reconstruct-api-proposal.md` + 评审结论记录。

## 阶段 1 — 算法核心复现与合成数据验证（不碰宿主）

- **目标**：纯 NumPy/SciPy 实现算法规格全部内容，精度达到论文量级。
- **认领目录**：`plugins/band_reconstruct/`、`tests/plugins/band_reconstruct/`。**不改 `bandscope/` 下任何文件**。
- **模块划分**（均为纯函数/纯数据模块，零 Qt）：
  - `preprocess.py`：高斯平滑 + MCLAHE；
  - `mrf_loss.py`：三线性插值取值 + 损失与梯度（向量化）；
  - `optimize.py`：L-BFGS-B 驱动、逐带顺序重构、收敛记录；
  - `init_surface.py`：解析初始化面 + 外部网格导入插值 + 动量缩放/能量平移对齐；
  - `synthetic.py`：合成数据生成（已知色散面 → 沿面放置高斯 EDC 峰 + 背景 + 泊松/高斯噪声，能量间隔默认 18 meV，可参量化带数、交叉、强度失衡）；
  - `metrics.py`：η_avg、η_rel。
- **验证命令**：`python -m unittest discover -s tests/plugins/band_reconstruct -t . -v`。
- **验收标准**：
  1. 单带无交叉合成数据：η_avg ≤ 50 meV（18 meV 间隔下）；
  2. 两带交叉合成数据（初始化带交叉信息正确）：η_avg ≤ 60 meV/带；
  3. 强度失衡数据（一条带强度弱 10×）：经 MCLAHE 后仍能重构，交接中对比开/关 MCLAHE 的差异；
  4. 全测试通过；单带 64×64×200 网格 CPU 运行时间 ≤ 2 min（记录实测值）。
- **交付物**：上述模块 + 测试 + `plugins/band_reconstruct/README.md`（算法说明、参数表、验收实测数据）+ 交接文档。
- **非目标**：GPU 加速、Zernike 系数压缩、论文基准测试的完整复刻、对称化后处理。

## 阶段 2 — 宿主集成第一增量：端到端跑通（集成任务主导）

- **目标**：按阶段 0 评审后的契约实现 `data_snapshot_3d` 与 `result_surface_2d`，插件接入面板 UI，能带面以 2D 结果页 + 导出呈现。**不做 3D 叠加渲染**。
- **认领目录**：
  - 集成任务：`bandscope/extensions/`（api.py、compat.py、plugin_host.py、analysis_host.py）、`bandscope/ui/page_data_process_v2.py`、`bandscope/app/refactored_app.py`（仅结果页接线）、`tests/extensions/`。
  - 插件任务：`plugins/band_reconstruct/`（新增 `entry.py`、`panel.py`、`plugin.json`、worker 包装）、`tests/plugins/band_reconstruct/`。
- **共享文件协调**：插件任务只向集成任务提交接口使用需求；所有 `bandscope/` 改动由集成任务落笔。
- **UI 要点**：带数 N_b；逐带初始化面选择与对齐参数（动量缩放、能量平移）；η；预处理开关；运行/逐带运行、进度显示、取消；能带面结果页（等高线/热图）与数据导出。
- **验证命令**：`python -m unittest discover -s tests -t . -v`（全量）+ 打包冒烟：`python scripts/release/build_plugin.py band_reconstruct --output-dir release`。
- **验收标准**：合成 npz 数据从宿主加载 → 面板发起重构 → 能带面结果页正确显示并可导出；任务可取消、数据过期结果被丢弃；全量测试通过。
- **交付物**：宿主能力实现 + 可用插件 + 测试 + 更新 `docs/development/plugins.md`（新能力文档）+ 交接文档。

## 阶段 3 — 3D 叠加渲染增量（集成任务主导）

- **目标**：实现 `render_overlay_surface`，把阶段 2 产出的能带面叠加进 3D 视图。
- **认领目录**：集成任务改宿主渲染侧（3D 视图场景管理、叠加图层、显隐/着色 UI 接线）；插件任务只提供面数据与显示偏好（颜色、透明度默认值）。
- **验收标准**：能带面与体渲染在同一坐标系对齐显示；逐带开关与着色生效；ROI/裁剪/换数据后叠加层正确联动或清除；视觉验收截图附在交接中（未完成视觉验收不得标记通过）。
- **非目标**：点击曲面交互取数、动画。

## 依赖顺序与反馈协议

```
阶段 0（契约提案，半天，先行评审）
   └─ 阶段 1（算法核心，主体工作量，可与 0 并行启动）
        └─ 阶段 2（宿主集成，等 0 评审结论 + 1 验收通过）
             └─ 阶段 3（3D 叠加，等 2 完成）
```

- 阶段 0 与阶段 1 可并行；阶段 1 不等待阶段 0 的结论（算法模块不依赖宿主接口形态）。
- 每阶段完成后由该阶段负责 agent 在 `docs/handoffs/` 提交交接（状态、负责范围、验收实测、未执行的检查及原因、下一步），由用户汇总反馈。
- 任何阶段发现本计划与事实冲突（如宿主接口实际形态不同、精度不达标），先记录偏差并暂停，由用户裁决后再改计划。
