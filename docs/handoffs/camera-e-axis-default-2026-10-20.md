# 3D 默认视角与 E 轴方向

日期：2026-10-20　状态：已实施并随 v1.15.0 发布；方向与默认视角断言通过，仍有 1 项既有滚轮布局检查失败与人工视觉待验收。

## 目标与范围

- 新数据首次显示 3D 场景、以及用户点击「重置视角」时，默认等轴视角要按 E 轴方向摆正：
  E 升序数据保持原默认姿态；E 降序数据上下翻转，使较小的 E 值落在画面下方。
- E 轴翻转开关仍按原语义叠加上下 180°，开关打开时显示坐标也保持小值在下。
- 不改变科学数据、色阶锁定、裁剪或导出；不重排 2D / 1D 视图的 E 轴显示。

## 实现

- `bandscope/app/refactored_app.py`：新增 `_axis_values_descend`、`_e_axis_descends_for_view`、
  `_apply_default_3d_camera_orientation`、`_reset_3d_camera_to_default`；新数据加载与
  `on_camera_view_reset` 统一走该路径。
- E 降序时沿用 E 轴翻转的相机语义（绕相机水平轴 180°），避免只换 `view_up` 造成左右镜像。
- 验证脚本 `scripts/validation/verify_camera_view_panel.py` 与
  `tests/integration/test_camera_view_controls.py` 补充升序/降序、初始视角、重置、E 翻转组合断言。

## 验证

- 2026-10-20，仓库 `.venv` Python 3.12，全量
  `python -m unittest discover -s tests -t .`：**1323 项通过**。
- `tests/integration/test_camera_view_controls.py` 中新增的升序/降序、初始默认视角、
  重置与 E 翻转组合测试全部通过。
- 真实数据 `.local/data/WSe2_step.npz`：`scripts/validation/verify_camera_view_panel.py`
  的默认视角、重置、E 轴小值在下、E 翻转、窄窗口字段等断言通过；脚本末尾
  「滚动后底部卡片完整进入视口」**1 项失败**。截图中右侧控制页已滚动、底部
  「性能与硬件」卡片下沿仍被窗口裁掉；该检查与 E 轴/相机逻辑无关，单次滚轮未把
  内容滚到底，属既有布局断言，需另行决定是补一次滚动还是调整检查阈值。

## 未执行 / 下一步

- 真实数据窗口下人工确认默认视角与重置观感、E 翻转开关组合。
- 处理上述底部卡片滚轮布局检查；确认是脚本阈值还是控制页尾部留白问题。
- CPU / NVIDIA 冻结包与安装器端到端验收随 v1.15.0 发布流程跟进；完成后本记录归档。
