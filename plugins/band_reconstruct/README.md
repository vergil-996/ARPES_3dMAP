# 能带重构（band_reconstruct）

用 MRF（马尔可夫随机场）方法从三维强度体 I(kx, ky, E) 中逐带重构二维色散面
E_b(kx, ky)。算法依据 Xian & Stimper et al., *Nature Computational Science* 3,
101–114 (2023)（DOI 10.1038/s43588-022-00382-2）；参考实现 mpes-kit/fuller
（MIT，TF1）只作算法参照，本项目是独立的 NumPy/SciPy 复现。

**当前状态：阶段 1（算法核心）、阶段 2（宿主集成）、阶段 3（三维叠加渲染）均已
实现**；阶段 3 的真实窗口人工复核未完成，见
[总体计划](../../docs/plans/band-reconstruction-plugin.md) 与
[接口契约提案](../../docs/plans/band-reconstruct-api-proposal.md)。

## 用法

1. 用主程序打开三维数据（NPZ/MAT 等），切到三维视图；
2. 在右侧「处理分析」页找到「能带重构」卡片（宿主只在三维视图显示它）；
3. 「检查数据」看一眼当前快照的规模与数据域；按需要设带数、逐带初值（抛物面 /
   高斯面 / 平面）、E₀、动量缩放与能量平移，以及 η、最大迭代与预处理开关；
4. 「重构能带」提交后台任务：进度条与状态行跟随，随时可取消；
5. 结果作为**一条带一个页面**出现在左侧结果树里（挂在来源页下，不抢当前页）；
   页头的下拉框可以在同一组带面之间切换；「导出」给当前带面的 x/y/z 与元数据；
6. 回到三维视图：重算出来的能带面会出现在右侧「渲染控制」页的「能带面叠加」卡片里，
   逐带勾选显隐、随体数据一起旋转；换数据后过期的面自动撤下（结果页本身仍在）。

插件需要宿主接口版本 3（`api_version: 3`），旧主程序会直接拒绝加载并说明原因。
安装与打包：`python scripts/release/build_plugin.py band_reconstruct --output-dir release`。

## 算法

**目标函数**（论文 eq. 4，每条带一张能量面 Ẽ）：

```
L = -Σ_ij log I(kx_i, ky_j, Ẽ_ij) + Σ_{4-邻域 (ij),(lm)} (Ẽ_ij − Ẽ_lm)² / (2η²)
```

- 强度项用三线性插值取值：动量两维是体素分数索引，能量维按物理坐标插值（支持
  非均匀、递减的能量轴）；`log` 前把强度截断到 `floor = 1e-6 × 峰值`，低于下限
  的体素梯度为 0；
- 平滑项按 4-邻域成对求和，梯度是邻接差分之和 `/η²`（边界点邻域少，梯度也小）；
- 优化器 scipy L-BFGS-B，变量为整张能量面，上下界取能量轴量程，默认最多 200 次
  迭代；**逐带顺序**重构，带之间不做扣减。

**预处理**：清理非有限值 → 高斯平滑 σ=(0.8, 0.8, 1.0) → 归一化到 [0, 1] →
MCLAHE。

**模块**（均为纯 NumPy/SciPy，零 Qt、零宿主依赖）：

| 模块 | 内容 |
|---|---|
| `preprocess.py` | 清理、高斯平滑、归一化、MCLAHE |
| `mrf_loss.py` | `TrilinearSampler`（三线性取样 + 能量方向导数）、`BandProblem`（损失与解析梯度） |
| `optimize.py` | `reconstruct_band` / `reconstruct_bands`、收敛记录、协作取消 |
| `init_surface.py` | 平面/抛物面/高斯面、外部网格导入、动量缩放 + 能量平移对齐 |
| `synthetic.py` | 高斯 EDC 合成体数据（泊松/高斯噪声、背景斜坡、强度失衡） |
| `metrics.py` | η_avg、η_rel、coverage |
| `worker.py` | 宿主工作函数（快照 → 预处理 → 逐带重构 → 一批带面）+ 进度回传 |
| `panel.py` / `entry.py` | 参数面板与插件入口（API 3） |

三维叠加渲染在宿主侧（`bandscope/rendering/surface_overlay.py`），插件只提供面数据
与显示偏好（颜色、不透明度）。

## 参数表

| 参数 | 默认 | 含义 |
|---|---|---|
| `sigma` | `(0.8, 0.8, 1.0)` | 高斯平滑的像素 σ（kx, ky, E） |
| `n_bins` | 128 | MCLAHE 直方图分箱数 |
| `clip_limit` | 0.01 | 每块每箱计数的上限（该块有效体素数的比例）；越小越接近恒等映射 |
| `threshold` | 1e-6 | 低于此值的体素不进直方图、输出置 0（作用于归一化后的强度） |
| `tiles` | 8 | MCLAHE 每轴分块数（核 ≈ 轴长的 1/8） |
| `eta` | 0.1 | 平滑先验强度，**单位与能量轴相同**；能量轴以 eV 为单位时 0.1 合适，用 meV 时要按比例换算 |
| `floor` | `1e-6 × 峰值` | 强度截断下限 |
| `maxiter` | 200 | L-BFGS-B 最大迭代次数 |

## 验收实测（阶段 1）

环境：Windows 11，Python 3.12.10，NumPy 2.4.3，SciPy 1.17.1，CPU（无 GPU）。
数据全部为测试内现算的合成体数据，能量间隔 18 meV，`eta=0.1`、`maxiter=200`，
η_avg 按论文 eq. 9 计算（`sqrt(mean((E_recon − E_truth)²))`）。

| 场景 | 验收线 | 实测 η_avg | 实测耗时 |
|---|---|---|---|
| 单带无交叉 64×64×200 | ≤ 50 meV，≤ 2 min | **4.8 meV**（η_rel 0.0156，最大偏差 28.1 meV） | 预处理 0.27–0.37 s + 重构 0.16–0.18 s ≈ **0.5 s** |
| 两带交叉 64×64×200 | ≤ 60 meV/带 | **5.7 / 5.8 meV**（η_rel 0.015） | ≈ 0.7 s |
| 强度失衡 10×（弱带 0.1） | 经 MCLAHE 仍能重构 | 弱带 **4.3 meV（MCLAHE 开）/ 3.8 meV（关）**；强带 3.8 / 3.7 meV | ≈ 0.8 s |

- 运行时间余量充足（0.5 s vs 120 s 预算）；体数据 256×256×470 的预处理约
  8–11 s，是整条链路里最慢的一步。
- 迭代到 200 次时损失尾部变化已在 1e-3 量级以下（`BandResult.tail_delta`），
  继续加迭代只带来 ~0.1 meV 量级的改善：`success=False` 只表示达到迭代上限，
  不代表没收敛。
- **MCLAHE 的作用要如实看**：在这组合成数据上它把弱带的 η_avg 从 3.8 抬到
  4.3 meV（轻微退化，约 0.5 meV 量级），但远低于 60 meV 的验收线。原因是
  CLAHE 是分块单调映射：它不能凭空造出局部极大值，却会把平坦区的噪声一起
  抬起来。详见交接文档。

## 已知边界

- 一条带只有在数据里是**局部极大值**时才可能被重构出来。若弱带整个骑在强带的
  尾巴上（局部不是极大），任何单调对比度变换都救不回来，重构会滑向强带——这是
  数据里没有信息，不是优化器的问题。
- `eta` 与能量轴同单位；换单位（eV ↔ meV）时必须同步换算，否则平滑强度差 10⁶ 倍。
- 首版不做：曲率先验（`includeCurv`）、对称化、GPU、Zernike 压缩、论文基准的
  完整复刻。

## 测试

```powershell
python -m unittest discover -s tests/plugins/band_reconstruct -t . -v   # 算法 + 插件单元
python -m unittest tests.integration.test_plugin_surface_flow -v        # 宿主端到端
python -m unittest tests.rendering.test_surface_overlay -v              # 叠加层几何与对齐
python scripts/validation/verify_band_overlay.py                        # 离屏视觉验收（截图 + 指标）
```

覆盖：取样器与解析梯度（对中心差分核对）、预处理各步语义、初始化面与对齐参数、
合成数据生成、指标与 NaN 语义、优化器的收敛/上下界/取消、上面三条验收标准，以及
插件侧的工作函数/面板与宿主侧的三维快照、面结果建页、带选择器、导出、取消与作废。
