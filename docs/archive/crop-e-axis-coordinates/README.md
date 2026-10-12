# 3D E 翻转后的裁剪与裁空坐标

2026-10-12。状态：坐标修复、自动化验证与真实窗口裁剪／裁空坐标验收完成。
扩展路径的四项不一致见[当前验收交接](../../handoffs/e-axis-consistency-acceptance.md)。

## 检查结论

3D 显示在 E 翻转时反转完整 E 坐标数组，体素顺序和绝对 ROI 下标保持不变。
原范围弹窗、选区框换算、裁剪与裁空模型仍使用原始 E 坐标，因此完整范围的
最小值和最大值看起来正确，但局部 ROI 或拖动后的边界与画面坐标不一致。
上下限表示数值最小值与最大值，翻转后仍应满足下限不大于上限。

## 修改范围

- `bandscope/core/crop_model.py`：3D 默认范围和裁剪、裁空按显示 E 坐标解释；
  返回结果恢复原始坐标，包含塌缩为单层切片的情况；翻转导出坐标与显示一致。
- `bandscope/app/crop_integration.py`：输入范围与绝对选区框下标双向转换；
  新页面的裁剪链存储原始物理坐标，保证切片位置控件与后续重算一致。
- `bandscope/ui/crop_controls.py`：另一页面切换翻转后恢复本页草稿时，保留体素位置。
- `tests/integration/test_crop_e_axis_coordinates.py`：合成数据回归，覆盖非均匀升降序
  E 坐标、局部 ROI、裁剪与裁空、分数下标、跨页草稿恢复、单层切片和导出。

未改动已有页面树任务涉及的主窗口、结果工作区、页面树及其测试与验证脚本。

## 验证

使用 `.venv/Scripts/python.exe`，Qt 离屏，合成数据。Windows 沙箱第一次执行后
出现临时目录清理权限错误，随后在正常临时目录权限下复核。

- 相关回归：`python -m unittest tests.integration.test_crop_e_axis_coordinates
  tests.core.test_crop_model tests.integration.test_crop_controls
  tests.integration.test_crop_integration tests.integration.test_erase_regions
  tests.integration.test_e_axis_flip -v`，71 项通过。
- 完整回归：`python -m unittest discover -s tests -t . -v`，运行 1065 项，2 项失败，
  为 `tests.ui.test_page_tree.PageTreeMultiSelectionTests.test_inactive_page_keeps_existing_selection_focus_and_delete_target`
  与 `tests.ui.test_page_tree.PageTreeMultiSelectionTests.test_inactive_page_keeps_the_search_query_and_selection`。
  该模块属于工作区已有并仍在变化的页面树任务，不在本次修复范围内。
- 本次修改文件 `git diff --check` 通过。
- 日志位于 `.local/outputs/crop-e-axis-focused-tests.log` 和
  `.local/outputs/crop-e-axis-tests.log`，不提交。

后续使用 `.local/data/WSe2_step.npz` 完成真实窗口验收：E 翻转后输入 133–159，
选区对应绝对 E 下标 40–66，裁剪与裁空数值结果正确；场景、弹窗及生成 ROI
的截图已检查。证据与复跑命令见上述交接。未执行连续拖动各边界的完整手动
手势验收、冻结包验证或安装器重建。
