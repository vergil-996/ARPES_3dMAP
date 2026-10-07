# 能带重构插件：阶段 2（宿主集成）交接（2026-10-07）

- 上游计划：[能带重构插件总体计划](../plans/band-reconstruction-plugin.md)
- 接口契约：[阶段 0 提案](../plans/band-reconstruct-api-proposal.md)（用户已裁决）
- 阶段 1 交接：[算法核心](band-reconstruct-stage1-2026-10-07.md)
- 状态：**已完成并通过阶段验收**；3D 叠加渲染（阶段 3）未开始

## 用户已裁决的接口问题（2026-10-07）

1. `AnalysisSurface2D` **一次交回多条共网格带面**（对计划原文的推广）；
2. 三维快照取数**与三维视图所见一致**：当前页数据域（ROI）→ 当前帧 → 含全局
   去噪，不含显示旋转，允许从任意有数据域的页面抓取。

其余 5 条（快照上限 1 GiB、进度挂 `CancelToken.report_progress`、宿主代偿显示
旋转、组合约束放加载期、结果数组的存放方式）按提案推荐执行，其中存放方式有一处
实测后的调整，见下文「与提案的偏差」。

## 改了什么

宿主（集成侧）：

| 文件 | 改动 |
|---|---|
| `bandscope/extensions/compat.py` | 新增 `data_snapshot_3d` / `result_surface_2d` / `render_overlay_surface` 三个能力常量与 `ANALYSIS_3D_CAPABILITIES`；`SUPPORTED_API_VERSIONS` 加 3，`API_VERSION = 3` |
| `bandscope/extensions/api.py` | `AnalysisInput3D`、`BandSurface`、`AnalysisSurface2D`、`validate_analysis_surface`、`PluginHostV3`、`CancelToken.report_progress`（新增 `progress_sink` 字段）、`Plugin.on_analysis_progress` |
| `bandscope/extensions/analysis_host.py` | 任务进度信号（工作线程发出、主线程收）、按 0.2 s 节流的进度接收器 |
| `bandscope/extensions/plugin_host.py` | `_HostBridge` 升 v3；`capture_analysis_input_3d`（数据域 + 当前帧 + 去噪 + 坐标切片 + 1 GiB 上限）；面结果校验与建页；分析面板按能力分视图显隐；进度转发 |
| `bandscope/app/refactored_app.py` | `plugin_surface` 页类型、`add_plugin_result_page(result_kind=...)`、`_get_plugin_surface_context`、页头带选择器、面结果导出载荷 |
| `bandscope/ui/result_workspace.py` | `set_header_extra`：页头里由宿主按页面插入的控件 |

插件（`plugins/band_reconstruct/`）：新增 `worker.py`（工作函数：能量窗口 → 预处理
→ 逐带初值/对齐 → 逐带重构 → 一批带面，全程回传进度）、`panel.py`（带数、逐带初值
与对齐参数、η/迭代/预处理开关、进度条、检查数据/重构/取消）、`entry.py`、
`plugin.json`（`api_version: 3`）。

测试与文档：`tests/extensions/test_plugin_api_v3.py`（13 项）、
`tests/plugins/band_reconstruct/test_plugin.py`（20 项）、
`tests/integration/test_plugin_surface_flow.py`（11 项，端到端）；
`docs/development/plugins.md` 增加「三维分析（API 3）」一节与能力表更新；
`tests/extensions/test_plugin_upgrade.py` 的合成目录默认接口集合改为
`SUPPORTED_API_VERSIONS`（原来硬编码 `(1, 2)`，接口版本升到 3 后会误判）。

## 验证命令与结果

```powershell
python -m unittest discover -s tests -t .                              # 1118 项通过
python -m unittest discover -s tests/plugins/band_reconstruct -t .     # 110 项通过
python -m unittest tests.integration.test_plugin_surface_flow -v       # 11 项通过
python scripts/release/build_plugin.py band_reconstruct --output-dir release
python -m compileall -q bandscope plugins scripts tests start.py
```

端到端用例走**真实**链路：`build_plugin.py` 打包 → `install_package` 安装 →
`PluginManager` 加载 → 面板抓三维快照 → 后台工作线程重构 → 宿主校验并建面结果页
→ 二维渲染路径画图 → 导出载荷。覆盖：结果页挂在来源页下且不切换当前页、快照与
三维视图取数一致（含 ROI 切片与只读性）、缺数据/计算未完成时的可展示原因、面板
只在三维视图显示、进度到达面板、取消丢弃结果、数据换代后结果作废、页头带选择器
（多带出现 / 单带消失 / 切换改变显示数据）、导出字段与矩阵导出识别。

打包冒烟：`BandScope-band_reconstruct-1.0.0.bsplugin` 构建成功（宿主 1.12.3 支持
接口 1/2/3）；插件包不含宿主依赖，体积未因本功能膨胀。

## 与提案的偏差（都经过实测）

1. **面结果数组放在 `spec.params["base_surface"]`，没有另建内存注册表。**
   提案原本担心「几万个浮点塞进 params」，实测真正的风险是 Python 列表开销，而
   `base_curve`（1D）本来就是这么存的；页面 spec 不落盘、随页面删除自动释放、
   裁剪时的 `deepcopy` 也只复制数组引用。存只读 `ndarray` 后内存与生命周期都
   比额外注册表更简单，也不引入「页删了注册表没清」的新故障模式。
2. **一条带一页 + 页头带选择器**：`AnalysisSurface2D` 交回 N 条带时宿主建 N 个
   `plugin_surface` 页（都挂在来源页下），页头的下拉框在同一组的带面之间切换。
   这样多带结果既能逐个查看/导出，也不需要在宿主里发明页面级工具栏。
3. **`render_overlay_surface` 没有进 `SUPPORTED_CAPABILITIES`**：宿主还没有叠加
   渲染实现，把能力先声明进支持集合会让插件「声明了却什么也不会发生」。声明它的
   插件现在会被判为「宿主尚未支持的能力」——这是事实。阶段 3 落地后再加。
4. **面结果页天然支持 2D 裁剪/擦除**：因为复用了 `render_2d_slice` 的上下文约定
   （`plot_axes` / `plot_logical_bounds` / `coords`），裁剪与擦除会按坐标区间切面
   数据，导出走宿主既有的「裁剪结果」路径；没有额外写一套。

## 顺带修掉的既有缺陷

- `PluginSession.submit_analysis` 只接受 `AnalysisInput2D`，三维快照会被判为
  「快照无效」；现在接受两类。
- `PluginSession.notify` 在窗口拆除 / 测试桩上用 `getattr(window, "toast_manager")`
  会抛 `RuntimeError`（PyQt 未初始化对象），提示失败会打断任务提交；现在兜住。
- `set_analysis_cards_visible` 原来只看「是否 2D」，三维插件的面板永远不显示；
  现在按插件声明的快照能力决定显隐视图。
- `_analysis_page()` 改为优先使用真正挂过面板的那个页面，不再重新从窗口上取。

## 未执行的检查及原因

- **真实窗口 + OpenGL 的视觉验收**：`plugin_surface` 页走 matplotlib 二维路径，
  已在离屏 Agg 上验证「画得出、落在正确的物理坐标、标题为带名」；但**整窗显示、
  页头下拉框的真实外观与交互、三维视图里插件卡片的位置**没有做人工视觉复核
  （需要桌面环境，且连续弹窗会打断用户）。**未标记为通过。**
- **冻结包（PyInstaller）冒烟与 CI**：本机未重建冻结包；CI 未跑（本轮没有推送）。
  `scripts/release/build_plugin.py` 侧的打包校验已跑。
- **真实实验数据**：本阶段验收用合成数据；自动化测试不得依赖 `.local/data/`。
- **体数据任务单独限流**：提案里说「阶段 2 实测确认是否需要」。当前实测（1 插件
  1 个未结束任务 + 队列上限 4）下没有观察到内存问题，**没有**加额外限流；若将来
  有多个三维插件并行，需要重新评估。
- 观察到一次 `tests/extensions/test_plugin_store.py::test_two_processes_do_not_lose_updates`
  偶发失败（多进程 registry 读写，子进程返回码 1）。随后扩展套件 ×3、全量 ×2
  均通过；本次改动未触碰 `plugin_store`，判定为既有的偶发用例，未做处理。

## 下一步（阶段 3）

1. 3D 叠加渲染：叠加层管理器（`plotter.add_mesh(..., name=...)`）、世界坐标换算
   （索引 × `200/(full_shape-1)`）、随 E 翻转与两种旋转的联动、清理路径挂到
   `_invalidate_scope_render_state` / `closeEvent`；
2. 把 `render_overlay_surface` 真正加进 `SUPPORTED_CAPABILITIES`，插件清单再声明它
   （插件版本要一起升）；
3. 视觉验收：截图附在交接里，未完成不得标记通过。
