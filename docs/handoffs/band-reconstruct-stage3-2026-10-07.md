# 能带重构插件：阶段 3（三维叠加渲染）交接（2026-10-07）

- 上游计划：[能带重构插件总体计划](../plans/band-reconstruction-plugin.md)
- 前序交接：[阶段 1（算法核心）](band-reconstruct-stage1-2026-10-07.md)、
  [阶段 2（宿主集成）](band-reconstruct-stage2-2026-10-07.md)
- 状态：**代码与离屏视觉验收完成**；**真实窗口人工复核未执行**（按计划要求，
  未完成视觉验收不得标记通过）

## 目标与范围

把阶段 2 产出的能带面叠加进三维视图：与体渲染同一坐标系对齐、逐带开关与着色、
ROI/裁剪/换数据后正确联动或清除。插件只提供面数据与显示偏好，宿主负责全部渲染。

## 改了什么

| 文件 | 改动 |
|---|---|
| `bandscope/rendering/surface_overlay.py`（新增） | `OverlaySurface`、`surface_world_geometry`（纯函数几何换算）、`SurfaceOverlayManager`（actor 的创建/更新/显隐/清理/预览旋转跟随） |
| `bandscope/ui/page_render_control.py` | 「能带面叠加」卡片：逐带勾选框 + 色块，`set_overlay_layers` 只在不重建控件的前提下同步勾选状态 |
| `bandscope/app/refactored_app.py` | 叠加层接线：三维渲染后 `_sync_surface_overlays`、层列表 `_surface_overlay_layers`（按数据代次过滤过期结果）、勾选回调、滚轮预览与复位时同步 actor 变换、换数据域/关窗时清理 |
| `bandscope/extensions/compat.py` | `render_overlay_surface` 加入 `SUPPORTED_CAPABILITIES`（渲染已落地才加，不是先声明后空转） |
| `plugins/band_reconstruct/` | 版本升 **1.1.0**，清单声明 `render_overlay_surface`；README 补用法 |
| `scripts/validation/verify_band_overlay.py`（新增） | 离屏视觉验收：真实工作函数重构 → 真实 `VolumeRenderSession` + 叠加层渲染 → 截图 + 对齐指标（含真值面对照） |
| `tests/rendering/test_surface_overlay.py`（新增，17 项）、`tests/support/overlay.py`（新增替身）、`tests/integration/test_plugin_surface_flow.py`（+5 项） | 几何换算、与体数据旋转的对齐、actor 生命周期、叠加层随结果/代次/旋转/显隐联动 |
| `docs/development/plugins.md`、`testing.md` | 能力表与三维分析一节；验证脚本清单 |

## 验证命令与结果

```powershell
python -m unittest discover -s tests -t .                          # 1140 项通过
python -m unittest tests.rendering.test_surface_overlay -v         # 17 项通过
python -m unittest tests.integration.test_plugin_surface_flow -v   # 16 项通过
python scripts/validation/verify_band_overlay.py                   # 离屏视觉验收
python scripts/release/build_plugin.py band_reconstruct --output-dir release   # 1.1.0 出包
```

对齐验证分三层，互不依赖：

1. **变换本身**：把体数据里一个孤立体素用真实后端 `CpuComputeBackend.rotate_volume`
   转过去，质心与 `rotate_indices` 的预测相差 **0.05 体素**（30°/90°/-45°/137°
   多角度核对）；世界坐标映射与「索引 × spacing」逐点一致。
2. **落在亮脊上**：离屏验收脚本对每个面点检查它是否落在体数据 E 方向剖面峰值
   ±1 体素内（真值面同时算一遍作为上限对照）：

   | 旋转 | 重构面 命中率 / 对比度 | 真值面对照 |
   |---|---|---|
   | 0° | 100.0% / 99.8% | 100.0% / 99.9% |
   | 30° | **97.1% / 99.5%** | 97.1% / 99.5% |

   重构面与真值面**完全一致**，说明剩余差距只来自体数据的插值模糊，不是叠加几何。
   `η_avg = 3.6 meV`（合成抛物面）。
3. **画面**：截图见本目录 [`band-reconstruct-stage3/`](band-reconstruct-stage3/)
   （体数据无叠加 / 叠加能带面 / 旋转 30° 含与不含叠加）。离屏渲染走的是与窗口
   相同的 `VolumeRenderSession`、相同的后端旋转函数、相同的叠加管理器。

## 视觉核对抓到的真问题（已修）

第一版截图里，旋转 30° 后叠加层在画面四角多出四个尖角，与体数据轮廓对不上：

- **根因**：体数据的旋转是 `reshape=False`，转到原数组框外的内容被裁掉；而叠加层
  是逐点做索引旋转，方格的四个角落到了框外（实测索引范围 `[-8.6, 55.6]`），
  照画不误。
- **修法**：`surface_world_geometry` 把旋转后落在 `[-0.5, n-0.5]` 之外的点按
  「无解」处理，与 NaN 走同一条路径（丢点 + 丢四边形）。
- **效果**：30° 命中率 86.9% → **97.1%**，对比度 90.3% → 99.5%；截图四角恢复干净。
- 回归测试：`test_rotation_clips_to_the_data_frame`（渲染层）+ 集成用例里对旋转
  前后几何形状的断言。

**这条只有看图才能发现**——数值指标当时是 86.9%，看起来"还行"，肉眼一比就露馅了。

## 验收复核（2026-10-07，独立复跑）

由集成方对阶段 3 成果独立复核，全部交接声明得到复现：

- 全量测试 `python -m unittest discover -s tests -t .`：**1140 项通过**（.venv，与交接一致）；
- 离屏验收 `scripts/validation/verify_band_overlay.py`：η_avg = 3.6 meV；0° 命中率 100.0% / 30° 97.1%，重构面与真值面差距 0.0 个百分点——数值与交接一致；
- **截图人工复核（本轮补做）**：叠加前后差分图（4 倍增益）显示叠加差异精确覆盖体数据轮廓——0° 时为完整平行四边形，30° 时为旋转后带裁剪的不规则轮廓，四角无尖角（上一版抓到的缺陷保持已修），叠加层随体数据一起旋转、一起被裁到数据框内；
- 打包冒烟：`BandScope-band_reconstruct-1.1.0.bsplugin` 构建成功（宿主 1.12.3，接口 1/2/3）；
- 代码抽查：`bandscope/rendering/surface_overlay.py` 几何换算为纯函数、actor 生命周期接口清晰；坐标约定（索引空间旋转 vs 世界坐标 actor 旋转）在模块头注释中与交接描述一致。

**验收结论：阶段 3 代码与离屏视觉验收通过。** 仍保持未关闭的两项（见下「未执行的检查」）：真实窗口人工复核、真实实验数据核对——均按 2026-10-07 与用户确认的约定推迟到用真实数据时进行。

## 真实数据验收（2026-10-07，WSe2_step.npz）

应用户要求，用默认真实验证数据 `.local/data/WSe2_step.npz`（WSe₂，150×150×200×25）
跑通真实数据核对，脚本 `scripts/validation/verify_band_overlay_wse2.py`（已登记进
`docs/development/testing.md`；该数据已写入 `AGENTS.md` 成为默认验证数据）。
t=−50/0/+50 fs 三帧平均，能量窗口 E 索引 [90,199]，全网格 150×150，η=3.0（索引单位），
重构 3 条约 1.6 s/带，全部产物在 `.local/outputs/band_overlay_wse2/`。

- **通过项**：E-k 切片对照图（`05_ek_slice_bands.png`，关键证据）显示两条强色散价带
  脊被重构面紧贴跟随（信号区局部脊距 ±1 体素 60–64%、±2 体素 77–81%、平均 1.6–1.8
  体素）；3D 叠加层与体数据对齐、30° 旋转裁剪正确；全流程（数据加载 → 重构 → 叠加
  渲染）在真实数据上无报错。
- **如实记录的局限**：①两条下价带在 43% 的点上合并（数据本身只有一条宽脊，与
  「局部极大值是硬前提」一致，非优化器缺陷）；②最上面的 Band 3 跟的是宽弥散结构、
  动量中心区近似噪声，置信度低；③η 需要先标定——索引能量轴上 η≤1 会因平滑项过强
  把面拉平（本数据合适值 ≈3，即约 3 体素），面板提示语应据此修正（阶段 1 交接中
  「η=0.1 会让平滑项几乎失效」的说法对索引轴不成立，方向恰好相反）。
- **本项至此关闭**（真实数据核对已执行并记录）；真实窗口整窗人工复核仍保持未关闭。

## 未执行的检查及原因

- **真实窗口人工复核（未执行，不得标记为通过）**：需要在有桌面与 OpenGL 的环境里
  跑主程序，确认——三维视图里能带面与体数据对齐、旋转字段拖动后仍对齐、滚轮预览
  旋转时两者一起转、「能带面叠加」卡片的勾选与着色、ROI 裁剪后叠加层不再出现
  过期面。本轮只做了离屏渲染与自动化断言；离屏截图**不能**替代整窗复核。
  **2026-10-07 与用户确认：先不做**，等用真实实验数据时顺带看一眼；本项保持未关闭。
- **冻结包（PyInstaller）冒烟与 CI**：本机未重建冻结包；CI 未跑（本轮没有推送）。
- ~~真实实验数据~~：已于 2026-10-07 用 WSe2_step.npz 执行并关闭（见上节）。
- 阶段 2 交接里记录过多进程 lease 用例的一次偶发失败，与本阶段无关，未再复现。

## 下一步

按计划，能带重构插件四个阶段到此结束；真实数据核对已完成。剩余的是真实窗口整窗
人工复核（随时可做）与随主程序发布（版本号、插件清单 `requires_app` 随下一版主程序
更新）。面板 η 提示语修正留作后续小改进。
