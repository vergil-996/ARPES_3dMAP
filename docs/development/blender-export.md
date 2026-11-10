# Blender 工程导出与回读

BandScope 可以把一个或多个冻结的 3D 页面转换为可继续创作的 Blender 工程，也可以重新打开自己导出的工程，调整基础显示参数后原位置更新科学资产。需要用户本机安装 Blender；应用不会自动安装软件，也不会在导出完成后自动弹出 Blender。

## 新建工程

1. 点击顶部栏 **导出 Blender 工程**，打开独立对话框。「截图样式」和 `Ctrl+S` 仍只用于图片预览与图片导出。
2. 在「数据源 → 3D 页面」树中勾选页面，支持跨数据源及同一来源的多个 3D 页面；全局分组副本按条目身份去重。默认只勾选当前 3D 页，空选择不能导出。
3. 按需统一选择四项预设：**坐标轴及文字、辅助盒框、方向标、薄层长方体**。新建导出默认全部勾选，未勾选的对象不会创建。
4. **薄层长方体**是一个统一开关：选中后为八种材质各创建一块薄层长方体，共八块。八块都使用统一的橙色 `(1.0, 0.35, 0.05)`、各自材质的默认参数和默认方位。导出界面不再提供逐薄板材质、参数或布局设置，也没有「薄层长方体材质与布局」设置区。
5. 选择 `blender.exe`。应用依次尝试已保存路径、PATH、Windows 安装记录和标准安装目录，手动选择后会记住路径。开始前还会检查 NumPy、OpenVDB、体积系数节点、八种材质节点、Cycles 和灯光链接能力；验收版本为 Blender 5.2.3 LTS。
6. 选择 `名称.blend`。导出前会提交当前编辑，在 GUI 线程逐源逐页激活并等待对应精确计算代次；冻结后，数组准备和外部 Blender 进程不再读取活动窗口。成功、失败或取消都会恢复原活动源、页面、导航和相机状态。
7. 成功后同时得到 `名称.assets/`。只有工程和全部条目资产生成并校验后才会整体提交；覆盖已有输出前需要确认，失败不会留下半套更新。

**移动或分享时必须同时携带 `.blend` 和同名 `.assets/` 整个目录。** 工程通过相对路径引用 VDB；另存工程或 Append 不会自动复制资产。

## 多页面场景与预设

- 每个条目有独立的数据 Collection、父对象、体材质、备用相机和 VDB，按页面树顺序沿世界 X 方向排列。科学网格坐标保持不变，排布只平移父对象。
- 组间距按 Blender 实际求值对象边界计算，包括文字和全部已选预设；第一个/单个条目保持父对象零位移。多页面活动相机为沿首页视角的总览相机，各页原相机保留为备用。
- 坐标轴及文字在左侧，辅助盒框在后侧，立体方向标在上方。辅助盒框默认不参与渲染。
- 八块薄层长方体都是完整六面网格，不是切角或剖切轮廓；厚度固定 0.04 Blender 单位。每块、每个数据组都拥有独立网格、独立材质和独立节点树，不共享节点组。
- 默认方位为**右侧／前侧分组沿 E 分层**（`right_front_groups`）：平面发光、全息边缘、数据漫透射和透射窗在右侧；哑光、缎面、无折射玻璃和真实玻璃这四种需要辅助灯的材质集中在前侧，与后侧辅助盒框相对。两组各有四块薄板，分别围绕对应方位沿 E 排列，相邻层中心间距 0.35；前侧组整体上移半层，即 `group_height_offset=0.175`，右侧偏移为 0。两组统一移动到数据体上后，八块薄板按高度交替排列，不会两两重合。四盏辅助灯随对应薄板位于前侧并保持相同高度偏移。下侧仍留空。两组都不与数据体在空间上重叠，因此数据漫透射和透射滤色没有与数据体的光路重叠；这些位置由默认布局固定，不需要用户在界面中选择。
- 体材质使用无散射的吸收＋发射，密度采用对象空间，弱信号裁除阈值为 0。`Density multiplier` 调整密度倍率，`Color adjustment` 以线性 RGB 乘法调整颜色。
- 在 Blender 中组合结果时使用 **File → Append → `.blend` → Collection**，只追加所需数据 Collection，不追加相机 Collection，目标场景相机就不会被替换。Append 会连同数据 Collection 内的辅助灯和接收者 Collection 一起带入，灯光链接仍然有效。

## 薄层长方体八种材质

材质注册表在 `bandscope/exporting/blender_materials.py`（纯 Python，无 Qt／bpy）。统一预设按注册表顺序为每种材质各生成一块薄板，全部使用下表默认参数。IOR、粗糙度、吸收密度等也可在导出后的 Blender 节点中继续编辑。

| ID / 材质名称 | 节点组合 | 默认照明与用途 | 默认参数与限制 |
|---|---|---|---|
| `flat_emission` / 平面发光 | Transparent＋Emission | 颜色最稳定 | 单表面不透明度 0.30、发光强度 1.0；不需要辅助光 |
| `matte` / 哑光薄片 | Transparent＋Principled，Transmission=0 | 中性 Area 灯；强调明暗和厚度 | 不透明度 0.50、粗糙度 0.92、弱高光；自发光补偿默认 0 |
| `satin` / 缎面塑料 | Transparent＋Principled，Transmission=0 | 中性 Area 灯；更精致的薄片质感 | 不透明度 0.40、粗糙度 0.30、较高高光；自发光补偿默认 0 |
| `holographic` / 全息边缘 | Fresnel＋Backfacing 屏蔽＋Transparent＋Emission | 正面低遮挡，边缘和掠射角发光 | 正面不透明度 0.06、掠射角上限 0.85、Fresnel 幂 2.5；无折射 |
| `data_translucent` / 数据漫透射 | Transparent＋Translucent BSDF | 不使用薄板自发光；由数据体发光产生漫透射染色 | 实验性；漫透射比例 0.65、染色强度 0.60；建议 128 采样；默认右侧布局下不与数据体重叠 |
| `transmission_filter` / 透射窗／滤色片 | 中性或淡色 Transparent | 直接透过数据颜色；无折射、无表面发光 | 滤色强度默认 0，即无染色透射窗；默认右侧布局下不与数据体重叠 |
| `reflection_glass` / 无折射玻璃 | Fresnel＋Transparent＋Glossy | 中性 Area 灯；只保留反射与边缘高光 | IOR 1.45、粗糙度 0.08、反射强度 1.0；不弯曲数据 |
| `optical_glass` / 真实玻璃／亚克力 | Glass BSDF＋Volume Absorption | 中性 Area 灯；真实折射和厚度吸收 | IOR 1.45、粗糙度 0.05、吸收密度 0；折射会改变视觉位置 |

- 统一橙色是八块薄板共同的基础颜色。透射窗默认滤色强度为 0，按材质定义表现为中性透明，而不是强行额外染色。
- 透明混合类材质一律把自发光放在非透明分支内部；`data_translucent`、`transmission_filter` 以及补偿为 0 的哑光／缎面／玻璃不存在有效薄板自发光。
- “单表面不透明度”按薄板一个表面解释。薄板是封闭六面网格，正对穿过时前后两个表面都会参与混合，理论总不透明度为 `1-(1-a)²`；滤色片的前后两个表面也会连续滤色两次。
- 全息边缘用 Fresnel 控制正面到掠射角的混合，不使用 Wireframe 节点制造三角化对角线。节点用 `Geometry → Backfacing` 把背面权重压回正面值，避免整个背面被误判为掠射角。
- 真实玻璃使用封闭薄板实际的两个界面和 0.04 厚度；`IOR`、`Roughness`、`BandScope Volume Absorption` 的 `Density` 都可继续编辑。
- 默认八材质组合的渲染采样取各材质推荐值的最大值，即 128，写入 `scene.cycles.samples`。数据漫透射效果依赖数据体亮度，提高采样只能降低噪声。

## 辅助灯与灯光链接

- 默认八薄板中，哑光、缎面、无折射玻璃和真实玻璃各有一盏中性 Area 灯，共四盏；其余四种材质不创建薄板辅助灯。
- 灯位于数据 Collection 内的灯组 Collection，父对象为对应的 `BandScope Root`；尺寸和能量随薄板范围与距离缩放。灯光对象、接收者 Collection 和灯组 Collection 都记录在 `scene.json`。
- 使用 Cycles 灯光链接（`light_linking.receiver_collection`）把辅助灯的直接照明限制到需要它的薄板。数据体、坐标轴、辅助盒框和其它薄板不在接收者 Collection 内。
- 混合材质场景仍可能有间接光；不宣称隔绝所有物理间接光。数据漫透射薄板自身不发光，也不使用辅助灯直接照明。
- 辅助灯从取景、组间距和总览相机边界计算中排除（按对象类型 `LIGHT`），不会拉大科学排版。
- 若 Blender 缺少八材质节点或 Cycles 灯光链接能力，开始导出前明确报错，不静默退回平面发光。

## 打开已有工程并更新

独立对话框中的 **打开已有工程** 只支持 BandScope 导出的 `.blend` 及完整同名 `.assets/`。普通 `.blend`、缺失资产、未来版本 schema 或结构不一致会给出具体错误，不会创建空页面。

- 打开后恢复的是带工程来源标识的**冻结 3D 结果**，不是原始实验数据；不恢复原 NPZ、时间轴、插件状态或完整处理链。
- 多页工程可按唯一条目 ID 分别调整。每个条目独立保存 colormap、黑点、gamma、白点、锁定范围、是否包含零和不透明度映射，切换条目不会串用状态。
- 宿主预览复用 BandScope 的 PyVista/VTK 体渲染管线，不嵌入 Blender/Cycles，也不直接显示 Blender 材质节点。Cycles 与 VTK 的体积分方式不同，不承诺逐像素一致。
- 点击 **更新工程** 后，BandScope 用外部 Blender 后台进程只读打开工程，核对条目、体对象、父对象和相对 VDB 路径，再暂存新的 VDB。所有修改文件经过校验、备份后整体提交；并发变化、路径改向、对象缺失、写入失败或提交后校验失败都会中止并回滚。
- 原位更新只替换修改条目的 `volume.vdb`、`arrays.npz` 和工程级 `scene.json`，**不重建、不保存、不改写 `.blend`**。相机、灯光、排版、文字、附加对象和材质创作均保留。
- 如果工程已经在 Blender 中打开，更新后必须**重新载入 VDB 或重新打开工程**，界面会显示这一要求。

### 基础往返与已烘焙效果

第一版保证普通全局色阶、colormap 和不透明度的基础往返。局部增强和插件透明度倍率已经解析到体素，不能拆分恢复为原规则；含这些效果的条目在覆盖保存前会明确警告，基础显示重算会改变或丢弃这些效果。

schema 6 及更早工程只做有限恢复：可读取强度、坐标和已有色阶换算信息；未保存的 colormap 必须重新选择，并显示无法判断烘焙内容的通用警告。完整局部增强规则、插件参数、原始处理链和时间轴恢复不在当前范围内。

schema 7 工程仍可打开和原位更新：宿主保留原版本号、原 `box_presets`、材质、灯光和布局，不把只更新数组的旧工程伪装成已含八材质和灯光的 schema 8 工程。需要新的八薄板统一预设时请重新导出，或继续在 Blender 中编辑已有材质。

## schema 8 资产契约

`名称.assets/` 的典型结构为：

```text
名称.assets/
├── scene.json
├── README.txt
└── entries/
    ├── entry-001/
    │   ├── arrays.npz
    │   └── volume.vdb
    └── entry-002/
        ├── arrays.npz
        └── volume.vdb
```

- `scene.json` 是唯一权威编辑配置，按工程和条目记录资产路径、来源、坐标、相机、布局、预设、八薄板材质与参数、辅助灯、推荐采样及 `bandscope_edit` 编辑状态。`.blend` 内嵌的 `scene_metadata` 仅供生成期诊断，不作为回读依据，更新时也不改写它。
- 工程级 `project.presets` 只记录四项用户预设，其中 `thin_boxes` 控制全部八块薄板；`project.box_layout` 记录布局 ID；`project.box_materials` 以八种材质 ID 为键记录标签、默认参数、所需灯光和推荐采样。
- 每个条目的 `box_presets` 包含八条记录，分别记录 `preset_group=thin_boxes`、`layout_slot=right/front`、`group_height_offset`（右侧 0、前侧 0.175）、实际中心、尺寸、厚度、层间距、统一橙色、是否与数据体相交以及材质参数；`box_lighting` 记录灯组、接收者 Collection 和灯光对象名。
- `arrays.npz` 保存处理后强度、显示颜色、alpha 和体密度，供复核与重新转换。重新调色以强度数组和编辑参数为准，不从最终 RGB 或 Blender 材质反推科学强度。
- `volume.vdb` 包含 `density` FloatGrid 和 `color` Vec3SGrid，是 Blender 的实际渲染资产。
- BandScope 的 0–200 显示空间按 `(display - 100) × 0.01` 映射为居中的 2 个 Blender 单位。ROI、物理坐标、降序坐标及 E 轴相机翻转语义原样保留，E、kx、ky 不解释为毫米。
- 透明度按 `σ = -log(1-α) / 单位距离` 换算，并对接近 1 的 alpha 做有限截断。色阶锁定、gamma、局部增强和插件倍率在导出时只应用一次。
- 原位更新保留项目级材质、布局、灯光和条目级 `box_presets`；只替换修改条目的 VDB、`arrays.npz` 和 `scene.json`。

## 验收

从仓库根目录运行；普通 `unittest` 使用合成数据，不要求本机安装 Blender。

```powershell
python -m unittest discover -s tests/exporting -t . -v
python -m unittest discover -s tests -t . -v
python scripts/validation/blender_export_acceptance.py --blender D:/blender/blender.exe
python scripts/validation/blender_export_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe
python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
python scripts/validation/blender_project_roundtrip.py --blender D:/blender/blender.exe
python scripts/validation/blender_dialog_acceptance.py .local/data/WSe2_step.npz --blender D:/blender/blender.exe
```

合成验收覆盖四种投影、VDB 逐体素回读、CPU 渲染、目录搬移和 Append。多页往返验收覆盖四项及部分预设、组间不碰撞、总览与备用相机、压缩工程回读、显示参数更新、`.blend` 摘要不变及创作对象保护。八材质验收逐个真实生成、重开并渲染八种材质，并明确检查统一预设一次生成八块橙色默认薄板，另覆盖独立材质、灯光链接、多页、Append 和原位更新。真实窗口脚本检查合并后的预设、设置区移除和实际默认导出产物。

冻结验证包构建后运行：

```powershell
python scripts/validation/verify_frozen_blender_flow.py <BandScope.exe> --blender D:/blender/blender.exe
```

自动化和真实渲染不能替代维护者人工视觉复核；弱信号观感、VDB 边缘、八种材质的观感与灯光高光、数据漫透射的实验性效果、真实玻璃折射对数据位置的影响、多组排版及 Append 后创作体验仍需单独检查。冻结包脚本资源与外部进程链路也分别记录，不与源码验收混为一项。
