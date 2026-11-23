# 去噪管线重构（三级可选六种方法）

2026-11-23 · 已实施；2101 项全量回归、去噪定向测试与 WSe₂ 真实窗口检查通过；已随 v1.22.5 发布 GitHub 与 MatElab（2026-11-23）。本文为本事项状态主记录。

## 目标与范围

把原三级去噪管线（每级一个固定算法，部分参数只在 S-G／小波可调）改为每级都能从「None」或六种方法中选择，并把参数、校验与旧名兼容收到同一处。

- **六种方法**：高斯平滑、中值滤波、Savitzky-Golay 滤波、小波去噪、非局部均值、低秩 SVD。高斯按 Kx／Ky／E 设标准差，中值按轴设正奇数邻域，小波新增噪声标准差，非局部均值用块大小／搜索距离／强度倍率／噪声标准差，低秩 SVD 逐帧压缩 Kx×Ky 与 E 构成的矩阵。逐帧方法不跨时间帧混合信息。
- **参数与校验**：参数按方法分组，集中在「数据处理 → 去噪参数」；界面与直接计算 API 共用同一套范围检查（中值 31、NLM 块 15、搜索距离 20、SG polyorder 4）。SG 窗口按实际滑动轴长度校验；小波按各数据域 `shape[:3]` 与基函数计算可用分解层数，超限或上限为 0 时给出请求值与上限并拒绝整次应用；应用前对所有仍被页面引用的完整数据域或紧凑 ROI 预检。
- **旧名兼容**：`频域平滑`→高斯平滑、`滑动平均`→中值滤波、`贝叶斯去噪`→非局部均值，历史乱码的 SG／小波名归一到正名算法；卡尔曼滤波不再作为可选方法，旧文件中的该选择按未知方法回退为 None。

## 相关文件

- `bandscope/processing/denoise_config.py`：方法名、别名、默认参数与上限。
- `bandscope/processing/denoise_engines.py`：六种算法、规格解析、参数归一化与管线执行。
- `bandscope/ui/denoise_control_utils.py`、`blank_control_page.py`、`settings_popups.py`：参数分组控件、草稿状态与整次预检。
- `bandscope/app/refactored_app.py`、`source_workspace.py`：全局去噪状态、页面数据域、三级方法与缓存提交。
- `tests/processing/test_denoise_engines.py`、`tests/ui/test_denoise_parameter_widgets.py`、`tests/integration/test_denoise_pipeline.py`、`tests/integration/test_view_recipe_roundtrip.py`。

## 验证

- 全量 `python -m unittest discover -s tests -t .`：2101 项通过（2026-11-23，196.419 秒，日志 `.local/logs/release-1.22.5-full.log`）；`compileall` 通过。
- 去噪定向测试覆盖：六种方法的数值契约与逐帧行为、参数边界与非法值、旧名解析、三级顺序、整次拒绝与保留上一套结果、草稿与应用状态、复现文件中的方法名与参数往返。
- 真实窗口：`scripts/validation/verify_axis_assignment.py` 20 项与 `verify_independent_opacity_curve.py` 50 项通过（WSe₂，均未逐方法核对去噪数值）。
- 未执行：六种方法在真实数据上的逐方法数值对照与耗时测量；维护者人工视觉复核（参数分组布局、长耗时方法的进度与取消手感）；冻结安装包与安装器验收。

## 下一步

- 维护者按真实数据复核六种方法的观感与耗时，重点看非局部均值与低秩 SVD 在大数据域上的开销。
- 需要逐方法数值对照时，用 `.local/data/WSe2_step.npz` 在 CPU／可用 CUDA 上各跑一遍，记录耗时与结果摘要。
- 本记录在人工验收完成后归档；发布验收细节见 [v1.22.5 发布说明](../../archive/releases/v1.22.5.md)。
