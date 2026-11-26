# 能量两点定标（E1 零点 + E1–E2 目标间距）

2026-11-26 · 已实施并随 v1.23.0 发布 GitHub 与 MatElab。自动化回归、WSe₂ 真实窗口脚本与 CI 产物校验通过；维护者人工视觉复核、冻结包／安装器验收尚未执行。本文为本事项状态主记录。

## 目标与范围

在轴分配（「重新分配轴」）窗口增加能量定标入口：用户在能量–动量切片上选两条水平线 E1／E2，输入两点实际间距与单位，按逻辑索引重新生成均匀、只读的能量坐标；强度数据不变，同源页面保留。

- **入口与窗口**：数据体卡片的「重新分配轴」窗口新增「能量定标」按钮；窗口默认 XZ 切片，可切 YZ、可垂直翻转，E1／E2 以水平虚线表示，图外有可拖动手柄，另可输入目标间距与单位。
- **数值契约**：E1 为零点，E2 等于目标间距，能量坐标按逻辑索引线性重算并标为只读；强度数组与轴分配的非坐标字段不变。
- **接入与页面几何**：应用后同源替换 `AnalyzerCore`（`copy_with_energy_calibration`），页面保留并按新坐标重映射能量区间、轴域与裁剪区域；失败时回滚几何修改并保留原 core。
- **保存**：沿用定标后完整数据体的 NPZ／MAT 导出（`calibrated_source`），与 Kx/Ky 定标共用保存入口。
- **定标窗口统一**：Kx/Ky 与能量两个窗口的切片滑条改为 `CalibrationSliceSlider`（复用 `DualRangeSlider` 的黑白双端、中点整体拖动与合拢锁定徽章），不显示长度输入框，figure 只含一个 axes 且不添加 colorbar；Kx/Ky 窗口注明取区间中心最近采样、不做积分。

## 相关文件

- `bandscope/processing/energy_calibration.py`：逻辑索引两点定标与不可变结果。
- `bandscope/ui/energy_calibration_dialog.py`：切片、手柄、E1／E2 拖动与目标间距输入。
- `bandscope/ui/momentum_calibration_dialog.py`：`CalibrationSliceSlider` 与 Kx/Ky 窗口改造。
- `bandscope/core/analyzer_core.py`、`bandscope/app/source_workspace.py`：`copy_with_energy_calibration`、同源 core 替换与页面几何重映射。
- `tests/processing/test_energy_calibration.py`、`tests/ui/test_energy_calibration_dialog.py`、`tests/integration/test_energy_calibration.py`、`tests/ui/test_momentum_calibration_dialog.py`。
- `scripts/validation/verify_energy_calibration.py`、`verify_momentum_calibration.py`。

## 验证

- 全量 `python -m unittest discover -s tests -t .`：2335 项通过（2026-11-26，190.359 秒，日志 `.local/logs/release-1.23.0-full2.log`）；`compileall` 通过。
- 真实窗口（`.local/data/WSe2_step.npz`）：`verify_energy_calibration.py` 报 `[result] PASS`（入口、XZ／YZ 切换、垂直翻转、图外手柄拖动、应用后数值契约与关闭复核）；`verify_momentum_calibration.py` 报 `[result] PASS`（双端滑条样式、无长度输入框与 colorbar、NPZ／MAT 保存回读的坐标键与单位契约）。截图与 `report.json` 在 `.local/outputs/energy-calibration/`、`.local/outputs/momentum-calibration/`。
- 未执行：能量定标的 NPZ／MAT 保存回读（脚本明确标注由自动化覆盖）；截图人工视觉复核；冻结安装包与安装器验收。

## 下一步

- 维护者人工复核：能量定标窗口的手柄拖动手感、E1／E2 虚线在深浅主题下的可读性、YZ／翻转切换后的取样正确性；Kx/Ky 窗口去掉长度框后的布局。
- 需要在真实数据上做保存回读时，用保存按钮导出 NPZ／MAT 后按 `AnalyzerCore` 核对坐标键、单位与强度。
- 发行包验收以 CI 产物为准；发布细节见 [v1.23.0 发布说明](../../archive/releases/v1.23.0.md)。验收完成后归档本记录。
