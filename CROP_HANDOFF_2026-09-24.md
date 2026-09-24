# 裁剪交互迁移交接（2026-09-24）

## 交接状态

用户明确要求：**停止当前实现，写交接文档交给其他 agent 继续。** 已停止功能开发，并结束本轮仍在运行的 UI 冒烟测试调用。下述代码是未完成、未提交的工作区改动，不能视为已验收完成。

- 工作目录：`D:\ARPES_3dmap\ARPES_3dMAP`。
- 开始本轮实现时工作区干净；本轮未创建分支、未提交、未推送。
- 未启动其他 agent。接手者应在当前工作区继续，不要覆盖或重新生成已有改动。
- 本轮没有读取到适用的 `AGENTS.md`。

## 用户已经确认的需求（不要重新询问）

1. 本阶段只迁移裁剪相关功能。“图像控制”页面暂时保留旋转、显示坐标、E 轴翻转、返回原始等剩余功能，后续迁完再删除页面。
2. 顶栏新增可保持选中状态的“裁剪”按钮，代替原“切片交互”开关。开启时按钮高亮，结果画布为剪刀光标；输入框使用文本光标。
3. 裁剪模式下画布右键打开临时裁剪参数浮层，替代原右键菜单，也不再清除选区；关闭模式恢复原菜单。
4. 浮层使用独立 `QFrame + Qt.Popup`，在鼠标旁出现，保持当前深色主题，限制在屏幕内。三行六项上下限，底部“裁剪”按钮，无 Z 轴旋转。
5. 六项位置始终保留，标签随实际坐标轴变化，低维视图的无关第三行置灰。1D 的两轴是横坐标和强度/对数强度。
6. 3D 立即显示交互盒；所有 2D、1D 视图左键矩形框选。包括多曲线对比、对数结果、EDC 瀑布图。
7. 框选与数值输入双向同步。草稿范围按结果页保存，模式开关属于全局状态，不受切页恢复控件影响。
8. 浮层外点击或 Esc 关闭浮层，保留草稿；浮层已关闭且画布有焦点时 Esc 退出模式。再次点击顶栏按钮也退出。
9. 每次成功裁剪均新增并激活结果页，保留来源页；3D 压成单层也必须新增 2D 页，且落实另两轴范围。成功后浮层关闭，裁剪模式保持开启。
10. 1D 同时裁横纵坐标，按数据点保留，移除中间区段时必须断线，不跨缺口连线、不插值添加点。多曲线分别应用同一矩形。
11. 瀑布图按包含偏移的显示坐标裁剪，保留曲线标识和偏移，不重排剩余曲线。
12. 原始 3D 体积 ROI 继续覆盖全部时间帧，并保留旋转必须为 0° 的限制。其他裁剪页继承来源页的时间锁定和参数更新规则；范围固定，更新数据时重新应用。
13. 子页的来源参数独立复制；来源页修改/关闭不能使子页失效。连续裁剪不能恢复已经删除的数据。
14. E 轴翻转、降序坐标、偏移映射必须正确；截图与原有数据导出类型使用裁剪后的结果，保留原有格式支持范围。
15. 拒绝非有限数值和倒置输入；拖动方向归一化，超界取交集，无有效数据不新增页。参数更新后变空时显示空态，不留下旧画面。

## 已修改的实现

### 新增三个模块（当前均未跟踪）

- `crop_model.py`：纯数值层。
  - `CropSelection(view, axes, bounds, e_flip)`，支持字典序列化和输入校验。
  - `prepare_context` 补齐普通 2D 切片的轴信息。
  - `selection_for_context` 从当前结果生成默认物理范围。
  - `apply_selection` / `apply_crop_regions` 对网格、曲线、比较图和瀑布图裁剪。
  - 2D/3D 网格用包含端点、向外取整的索引范围；3D 单轴相等生成 2D。
  - 曲线用 NaN 表达被删除区段；瀑布图保留全部曲线槽位及 `curve_offsets`，同时遮罩 `raw_curves`。
  - `_orient` 根据选区提交时的 `e_flip` 映射到显示方向、裁剪后回到内部方向；新结果显示时沿用已有翻转逻辑。
  - `export_cropped_context` 构建不包含完整底层数组的导出数据，支持按当前翻转方向导出。
- `crop_controls.py`：`CropPopup`、`CropController` 和自绘高 DPI 剪刀光标。
  - 浮层已改成 `Qt.Popup | Qt.FramelessWindowHint`，修正系统边框导致的屏幕边缘溢出。
  - Controller 保存每页草稿、当前页、全局开启状态，负责数值编辑校验和提交信号。
- `crop_integration.py`：`CropInteractionMixin`，由主窗口继承。
  - 全局事件过滤中的右键/Esc 分流，光标更新。
  - VTK 盒与 Matplotlib RectangleSelector 的统一调度。
  - 新 `on_cut` 深拷贝来源参数并新增页；原始 3D 体积路径复用 ROI scope，其余保存 `params['crop_regions']` 操作链。
  - 轴积分裁剪继续使用 `axis_integral_crop` 类型，额外保留 `crop_base_rect` 以区分旧裁剪基础范围和新裁剪链。
  - `_render_empty_crop` 清理旧视图并显示空结果文字。

### 已修改的跟踪文件

- `refactored_app.py`
  - `My3DAnalyzer(CropInteractionMixin, QWidget)`；初始化独立 controller。
  - 顶栏“裁剪”按钮、选中样式、信号、右键分流和生命周期接入。
  - 原裁剪相关的一组方法从主类删除，由 mixin 实现；`page_image.switch_coord` 调用已替换。
  - 原 `_compute_render_context` 拆成 `_compute_base_render_context` 加统一裁剪包装层。
  - 结果渲染、空态、截图/数据导出接入新上下文；换页和加载时同步 controller。
  - 瀑布图使用 `curve_offsets`，不绘制全 NaN 曲线。
  - 比较曲线复制/粘贴取当前已裁剪上下文，而不是直接拿原始参数快照。
  - 最近两处修改：1D 标题加中文字体回退；瀑布图使用现有 `_apply_1d_plot_layout`，修正冒烟截图中纵轴文字被裁掉的问题。后者尚未完成最终视觉复核。
- `page_image_control_v2.py`
  - 移除六个范围输入、切片交互开关、应用切片按钮及其序列化状态。
  - 保留旋转、显示坐标、E 轴翻转、返回原始，以及仍供现有信号使用的隐藏全局按钮和时间控件。
  - 剩余组标题改为“旋转”“显示选项”“视图操作”。
- `publication_export.py`
  - 空裁剪拒绝截图；冻结瀑布图时包含原偏移。
- `publication_renderers.py`
  - 瀑布图图片导出使用保存的偏移，跳过全 NaN 曲线。

## 已做验证与结果

### 运行环境

- 必须使用 `.\.venv\Scripts\python.exe`，项目虚拟环境是 Python 3.12，NumPy 2.4.3。
- PATH 中的 `python` 是 `C:\Python314\python.exe`，缺少 PyQt5，不适合本项目。
- Windows 沙箱中 Python `TemporaryDirectory` 创建的目录无法继续写入；改用仓库内 TMP 路径也同样失败。这是测试环境权限问题。
- 通过工具的 `require_escalated` 在沙箱外运行完整本地 unittest 后通过，自动审核没有拒绝。

### 自动测试

- 新增 `tests/test_crop_model.py`：12 项，覆盖 3D、单层、普通切片、连续裁剪、降序、翻转、断点、多曲线、瀑布偏移、导出和空结果。
- 新增 `tests/test_crop_controls.py`：6 项，覆盖六个字段/禁用行、校验、草稿/模式、屏幕边界、Esc、右键事件优先级、旧页面控件移除。
- 调整旧 fixture：`tests/test_axis_crop_blit.py`、`tests/test_analysis_control_refresh.py`、`tests/test_data_scope.py`，改成独立 controller 驱动。
- 最近一次完整回归：**223 tests / OK**，日志 `.pytest_cache/crop-regression.log`。
- 注意：这是当时的中间版本结果。之后还有标题字体和瀑布图布局等小改动；接手后仍需对最终版本重新测试。
- 测试中既有 `publication_renderers.py` 全 NaN 输入警告，没有导致测试失败。
- **仓库 `.gitignore` 忽略整个 `/tests/`**，因此这些测试文件/修改不会显示在普通 `git status` 中。本轮没有改变这个策略，交付时需注意保留这些本地测试。

### 实际 Qt/VTK 冒烟

- 临时脚本 `.pytest_cache/crop_smoke.py`：构造 12×13×14×3 合成数据，真实创建主窗口、加载、切页、裁剪、截图快照和构造导出 payload。
- 使用 Qt `windows` 平台；Qt `offscreen` 在本机 VTK 无法取得有效 OpenGL pixel format，并不能验证真实体渲染。
- 脚本用假 UpdateController 禁止联网更新，QSettings 指向 `.pytest_cache/crop_smoke/settings.ini`，自动关闭测试窗口。
- 最新日志：`.pytest_cache/crop-smoke.log`。
- 已记录 PASS：原始 3D、连续 3D、3D 单层、普通 2D、时间积分 3D、轴积分 2D、瀑布图、单曲线、对数曲线、多曲线比较、2D 二阶导、Z 轴积分裁剪、单条 EDC 裁剪。
- 最后的 **3D 二阶导裁剪没有记录 PASS**；用户要求停止时已结束测试调用，未出现 `ALL UI SMOKE CASES PASSED`，不能宣称整套实际 UI 冒烟通过。
- 第一次冒烟的 log1d 使用收缩后的矩形选不到剩余离散点而被正确拒绝；临时脚本已调整该项为完整有效范围。这不代表已经测试了所有对数曲线矩形。
- 冒烟脚本为了直接建立轴积分页使用了现有 `_seed_time_integrated_axis_control_state`，它可能改变时间锁定状态。因此它不充分覆盖 frame/time_integral 的继承差异，需要专门补测。
- 截图位于 `.pytest_cache/crop_smoke/*-popup.png` 和 `*-result.png`。已人工查看 `axis2d-popup.png`：三行布局、第三行禁用、按钮和主题正常；看过旧版 `waterfall-result.png` 后修了纵轴布局，尚需看最新图片。
- 比较图中“基准”图例仍有中文字体缺字警告，最近只修了 1D 标题的字体回退。

## 接手后重点继续项（不是已确认完成）

1. **先审查新模块和接线，再补完测试，不要把当前代码当成完成版。** 尤其检查仍残留的旧裁剪状态/辅助函数与新 controller 是否有冲突。
2. 补完真实 3D 二阶导的裁剪测试，检查最后一项为何未完成；不要直接认定是功能错误或仅是中断。
3. 对时间帧、时间积分锁定、源参数独立复制、关闭来源页后子页仍能刷新、空结果恢复做专门集成验证；目前主要只有纯数值层和普通创建页路径的覆盖。
4. **派生分析与裁剪链的传播是重点风险**：裁剪页保留原 page_kind，但许多旧分析 builder 自己重建来源参数，未必携带 `crop_regions`。
   - 例如从已裁剪的 time_integral/second_derivative 3D 再做轴积分，可能只继承 data_scope 而遗漏显示结果裁剪。
   - 新 3D 单层页由 crop_regions 把 3D context 转成 2D，`home_slice_info` 可能仍为 None；旧的时间积分/二阶导 builder 有些依赖该字段，应核对它们是否错误按 3D 处理。
   - 新轴积分页同时有 `crop_regions`、`crop_base_rect` 和 `crop_k/e_*` 元数据。普通同页连续裁剪已测，但转换为其他分析的翻转语义还需检查。
5. **坐标一致性**：核对开启 E 翻转后框选、再次翻转、连续裁剪、导出，以及 3D 相机翻转与物理上下限的关系。当前 `_orient` 复刻既有低维翻转，3D 沿用原逻辑；这部分值得独立复查。
6. 核对瀑布图调整 k 步长/全局偏移参数后固定范围是否仍正确，偏移和曲线标识是否真正保留；当前保留槽位、只隐藏空曲线。
7. 检查非均匀坐标、单像素网格、零跨度输入、全 NaN 数据和异步刷新中的选区状态。`CropSelection` 目前拒绝低维零跨度和 3D 多轴同时相等。
8. 数据导出目前对带 crop_regions 的页走通用 `export_cropped_context`；需要核对原有科学元数据（积分范围、时间参数、二阶导参数等）是否应补回，以及 MAT/CSV/TSV 的列轴匹配。截图仍走统一渲染上下文。
9. 手动/Qt 事件回归：右键不会触发 VTK 原缩放动作或清选区；浮层外点击、输入框 Esc、画布 Esc、切页、无数据/配置页、忙碌状态和光标恢复。当前有事件层单测，但没有覆盖所有真实鼠标路径。
10. 文档尚未更新：README 仍描述“图像控制”中的切片交互、右键清除选区等旧流程。`scripts/verify_color_lock.py` 仍引用已删除的 `page_image.set_slice_values` 和 `page_image.btn_cut`，应迁移。
11. 清理主文件删除方法留下的多余空行和无用 imports/旧 helper（例如旧 Rectangle/RectangleSelector import、候选范围字典相关辅助函数），但先确认其他分析仍是否引用。新 mixin 也有可清理的未使用 import。
12. 最终运行适当回归并检查 `git diff --check`；不要在用户停止指令后自动提交/发布。本轮没有改 README、没有最终 diff 审查、没有最终交付。

## 建议的继续验证命令

```powershell
# 快速裁剪单测，普通沙箱可以运行
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_crop*.py'

# 完整回归：本机 TemporaryDirectory 权限原因需要沙箱外运行
.\.venv\Scripts\python.exe -m unittest discover -s tests

# 临时实际 UI 冒烟（会短暂显示测试窗口；先审查脚本）
.\.venv\Scripts\python.exe .pytest_cache\crop_smoke.py
```

命令重定向日志后，应保存 Python 的 `$LASTEXITCODE` 再读取日志并返回该值，避免 `Get-Content` 成功掩盖测试进程失败。

本交接文档写完后，原 agent 不再继续实现或测试。
