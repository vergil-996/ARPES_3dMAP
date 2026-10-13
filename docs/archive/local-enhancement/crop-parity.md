# 局部增强与裁剪显示一致

2026-10-13，状态：源码修复、自动化与真实窗口验收完成。负责范围：processing 共享传递函数与增强显示数据、rendering 图层/体渲染、app 区域设置及 ROI 去噪接入、出版快照，对应测试和验收脚本。保留其他裁剪、轴标题排查与跨视图修改。

## 用户要求与修复

用户明确要求新增区域不改变背景，并要求局部增强采用裁剪显示规则。此前版本从剩余背景样本统计范围、2D 整图 RGBA、3D 直接对归一化强度求 alpha 和新区域默认色阶等规则已被本记录替代。

- 背景始终使用当前完整科学结果的显示范围、原色阶与映射；添加区域不排除样本、不解除背景锁定。删除区域恢复原图背景映射（删除时解除背景锁仍遵循此前约定）。新区域复制当前控件设置。
- `processing/display_transfer.py` 集中保存裁剪/增强共用的黑白场、灰场、颜色坐标与不透明度折线；强度映射方式只决定不透明度，不改变颜色。
- 2D 保留普通标量背景图与其插值方式，增强范围叠加与裁剪相同的标量图层，有效点保持不透明。整图 RGBA 即使颜色值相同也会改变插值顺序，已移除这条实时显示路径。后添加图层覆盖重叠区域，避免采样所有权掩膜在框外造成插值空洞。
- 3D 继续单 actor、二分量显示通道。各区域在独立标量区间使用与裁剪相同的 256 点颜色传递函数，并按裁剪查色表的 uint8 精度采样；透明度使用裁剪的黑白场阈值、灰场与映射方式。按独立裁剪网格的不透明度单位距离换算区域 alpha，保持相同世界长度的光路衰减。
- 是否包含零遵循当前裁剪路径：首个 home 体数据 ROI 裁剪包含零，其他上下文保留自身规则。2D 使用实际二维结果范围。
- 首个 home 体 ROI 的去噪与裁剪共用原始 ROI、验证函数和后台计算接口；结果独立缓存，预览不触发新的 ROI 去噪任务。其他裁剪路径切取当前科学结果。积分、数值导出、插件输入和背景科学数组不改写。
- 出版图冻结 2D 标量图层和 3D 颜色传递函数规则，复用同一渲染路径。框显示选择、右键显示全部及 3D→2D 继承保留。

共享接口：`VisualEngine` 保留既有方法并委托共享传递函数；`resolve_display` 可接收裁剪处理后的区域数据，输出独立显示字段；`EnhancementInteractionMixin` 管理 ROI 缓存、锁定和默认样式。没有新增插件公开接口、依赖或资源路径。

## 验证结果

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -t . -v
.venv/Scripts/python.exe scripts/validation/verify_enhancement_crop_parity.py
.venv/Scripts/python.exe scripts/validation/verify_enhancement_projection.py
.venv/Scripts/python.exe scripts/validation/verify_enhancement_outline_visibility.py
.venv/Scripts/python.exe scripts/validation/verify_local_enhancement.py
```

- 最终全量 **1213 项通过**，退出码 0；新增 5 项实际对比测试。覆盖 2D 添加/删除前后背景逐像素一致；灰场、黑白场、倒序轴、E 翻转和重叠下图层与实际裁剪一致；3D 实际 VTK 颜色和不透明度属性、正值范围包含零、单位距离和极端灰场；新增设置继承/背景锁保留；ROI 去噪边界、缓存复用和科学数组不变。
- 部分旧断言按新要求替换：背景不再排除区域；2D 强度映射不再额外制造透明度；3D 色阶按裁剪路径影响不透明度。保留重叠、时间、锁定、清理和导出检查。
- WSe₂ 原生窗口专门对比 **23 项通过**：直接创建真实 3D 裁剪页验证色阶、颜色及光路衰减，2D 图层数据/RGBA 与裁剪一致，背景像素与锁定不变，出版冻结、删除与科学样本不变。
- 窗口回归分别通过：跨视图 **22 项**、显示选择 **23 项**、实际 Qt 鼠标/VTK 交互 **27 项**。原脚本的背景及初始色阶断言已更新到新要求。
- 实窗使用 `.local/data/WSe2_step.npz`（150×150×200×25，E 索引坐标），Qt 原生 Windows、本机 NVIDIA GPU。已复核增强窗口、3D 场景和出版 PNG。日志为 `.local/logs/enhancement_crop_parity_*.log`；专门对比输出在 `.local/outputs/enhancement_crop_parity/`。
- 本目录附件：`crop_parity_acceptance.json`、`crop_parity_2d_window.png`、`crop_parity_3d_scene.png`、`crop_parity_3d_crop.png`、`crop_parity_2d_publication.png`。
- 编译与 `git diff --check` 通过。未执行发行包/安装器重建、人工 3D 拖拽手感；ROI 去噪边界由合成数据和真实计算管线接口测试覆盖，实窗此次未启用去噪。3D 整体画面还包含区域外体素的遮挡，验收比较同样采样的传递函数和光路衰减，不声称整幅场景截图与孤立裁剪图逐像素相同。

下一步：源码可使用，发行时补做打包和人工手感验收。
