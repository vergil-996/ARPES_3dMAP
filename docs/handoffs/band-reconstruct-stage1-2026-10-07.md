# 能带重构插件：阶段 1（算法核心）交接（2026-10-07）

- 上游计划：[能带重构插件总体计划](../plans/band-reconstruction-plugin.md)
- 接口契约：[阶段 0 提案](../plans/band-reconstruct-api-proposal.md)
- 状态：**已完成并通过阶段验收**；阶段 0 提案待评审（不阻塞本阶段）

## 任务目标与范围

把论文的 MRF 能带重构方法用 NumPy/SciPy 复现成纯算法模块，用合成数据验证精度
达到论文量级。**本阶段不碰宿主**：没有改动 `bandscope/` 下任何文件，没有新增
依赖，没有新增共享文件。

认领目录（均已交付）：

```
plugins/band_reconstruct/{__init__,preprocess,mrf_loss,optimize,init_surface,synthetic,metrics}.py
plugins/band_reconstruct/README.md
tests/plugins/band_reconstruct/{__init__,test_metrics,test_preprocess,test_mrf_loss,test_optimize,test_init_surface,test_synthetic,test_acceptance}.py
```

## 改了什么

- 新增 7 个算法模块（见下），全部纯 NumPy/SciPy，零 Qt、零宿主对象、无 `sys.path`
  操作、无新第三方依赖（scikit-image 未使用：MCLAHE 自己实现）。
- 新增 8 个测试文件、90 条用例，数据全部测试内现算、固定种子。
- `plugins/band_reconstruct/README.md`：算法说明、参数表、验收实测数据、已知边界。

模块职责：

| 模块 | 关键接口 |
|---|---|
| `preprocess.py` | `preprocess_volume`、`gaussian_smooth`、`mclahe`、`normalize_volume`、`sanitize_volume` |
| `mrf_loss.py` | `TrilinearSampler`（三线性取样 + ∂I/∂E）、`BandProblem.energy_terms`、`default_floor` |
| `optimize.py` | `reconstruct_band`、`reconstruct_bands`、`BandResult`（收敛历史、`tail_delta`） |
| `init_surface.py` | `plane_surface` / `parabolic_surface` / `gaussian_surface`、`resample_surface`、`align_surface`、`load_surface_grid` |
| `synthetic.py` | `gaussian_edc_volume`、`SyntheticBand`、`SyntheticDataset` |
| `metrics.py` | `eta_avg`、`eta_rel`、`compare_surfaces` |

## 验证命令与结果

```powershell
python -m unittest discover -s tests/plugins/band_reconstruct -t . -v   # 90 项通过
python -m unittest discover -s tests -t .                               # 1074 项通过（含本插件 90 项）
```

验收实测（Windows 11 / Python 3.12.10 / NumPy 2.4.3 / SciPy 1.17.1 / CPU，
合成数据、能量间隔 18 meV、`eta=0.1`、`maxiter=200`）：

| 验收标准 | 结果 |
|---|---|
| ① 单带无交叉 64×64×200，η_avg ≤ 50 meV | **4.8 meV**（η_rel 0.0156，最大偏差 28.1 meV） |
| ② 两带交叉（初值带交叉信息），η_avg ≤ 60 meV/带 | **5.7 / 5.8 meV** |
| ③ 强度失衡 10×，经 MCLAHE 仍能重构 | 弱带 **4.3 meV**（MCLAHE 开）/ 3.8 meV（关），均远低于 60 meV |
| ④ 单带 64×64×200 CPU 运行时间 ≤ 2 min | **约 0.5 s**（预处理 0.3 s + 重构 0.17 s） |

额外核对：解析梯度与中心差分逐点一致（`test_mrf_loss`）；取样器与 `np.interp`
逐点一致、递减/非均匀能量轴一致；取消令牌在回调与目标函数两处都会生效
（`AnalysisCancelled` 能从 `scipy.minimize` 里正常传出）。

## 与 fuller / 论文的差异（算法规格允许近似，此处逐条说明）

1. **MCLAHE 用的是分块 CLAHE，不是 fuller 的核卷积 MCLAHE**。每轴分 8 段
   （对应「核为轴长的 1/8」），块内直方图 → 按 `clip_limit` 限幅并均匀重分配 →
   8 邻块 CDF 三次线性插值。差异：块是互不重叠的硬边界 + 插值，核卷积是连续滑窗；
   在体数据很小时（如 64×64×200）每块体素少、直方图稀疏，局部统计噪声比核卷积大。
2. **CDF 在箱内做线性插值**。只按整箱取值会把弱特征量化成台阶，实测会让弱带
   η_avg 从 3.9 退化到 5.2–6.7 meV；加插值后回到 3.8–3.9 meV。
3. `clip_limit` 按「该块有效体素数的比例」解释（与 scikit-image 一致）；块内主箱
   被削平后，映射向恒等靠拢，即 `clip_limit` 越小增强越弱。实测 0.01 时中段强度
   基本维持原值（0.514），0.9 时接近全局均衡（0.89）。
4. `threshold` 解释为「低于此值的体素不进直方图且输出置 0」。归一化后默认 1e-6
   相当于只压制真零背景。
5. 论文的 `includeCurv`（曲率先验）与对称化未实现（本阶段非目标）。

## 值得注意的实测事实

- **MCLAHE 在这组合成数据上让弱带略微变差**（3.8 → 4.3 meV）。原因是 CLAHE 是
  分块单调映射：不能凭空造出局部极大值，却会把平坦区的噪声一起抬起。强带几乎
  不受影响（3.7 → 3.8 meV）。验收线（≤60 meV）留有很大余量，因此不影响通过；
  但**不能**在文档里写成"MCLAHE 提升了弱带重构精度"。
- **局部极大值是硬前提**：把弱带放在强带尾巴上（间隔 0.05–0.12 eV、强带幅度
  1.0 / 弱带 0.05）时，无论 MCLAHE 开关，重构都会滑向强带（η_avg 42–112 meV）。
  这是数据里没有信息，不是优化器缺陷；面板上应提示用户用初始化面/参数挑出弱带
  所在的能量窗口。
- `success=False` 只说明 L-BFGS-B 到达迭代上限（本问题损失尾部变化已在 1e-3 以
  下）。判断收敛请看 `BandResult.tail_delta`，不要只看 `success`。
- `eta` 与能量轴同单位。主轴以 meV 为单位时 0.1 会让平滑项几乎失效，阶段 2 的
  面板必须把单位换算或给出 `eta` 的显式输入与提示。

## 未执行的检查及原因

- **真实实验数据验证**：本阶段非目标（阶段验收用合成数据）；仓库内 `.local/data/`
  的实验文件不能进自动化测试（协作约定禁止）。
- **GPU 加速、Zernike 压缩、论文基准完整复刻、对称化**：总体计划明确列为本阶段
  非目标。
- **宿主集成与打包冒烟**：`plugin.json` / `entry.py` / `panel.py` 属阶段 2，本阶段
  尚未产出，因此 `scripts/release/build_plugin.py band_reconstruct` 现在会因缺清单
  失败——这是预期的，不是缺陷。
- **视觉验收**：本阶段没有界面，无视觉验收项（也未被标记为通过）。

## 下一步

1. **等待阶段 0 提案的 7 条评审结论**（其中"多条带 vs 单面"与"3D 快照取数语义"
   影响阶段 2 接口）；
2. 阶段 2：按评审后的契约实现宿主能力 + 插件面板/入口 + 结果页与导出；
3. 阶段 2 落地时补 `plugin.json`（`api_version: 3`、`requires_app` 用范围声明）
   与打包冒烟。
