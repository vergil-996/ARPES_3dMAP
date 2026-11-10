# Blender 薄层长方体八材质预设计划

- 日期：2026-11-09。
- 状态：八材质统一预设、右侧／前侧分组、半层高度交错及统一橙色命名已实施并验收；63 项相关自动化、1598 项全量回归、真实 Blender 15 项、工程往返 5 项及真实 WSe₂ 窗口导出均通过。CPU／NVIDIA 冻结包未按当前版本重建复验；维护者人工视觉复核仍未执行。
- 负责范围：`bandscope/exporting/` 的材质配置、场景生成、布局灯光、导出界面和工程契约；对应测试、真实 Blender 验收及使用说明。
- 共享边界：不改变科学体数据、VDB、活动窗口冻结协议和原位更新事务；打包配置仅在资源检查确有需要时由集成任务修改。本任务不新增插件 API、不改版本号、不发布。
- 状态主记录：本文件；计划索引只维护入口，实施后如实记录自动化、真实渲染、冻结包和人工视觉结果。

## 2026-11-09 统一预设需求调整（当前行为）

- 原“橙色长方体”“绿色长方体”“蓝色长方体”三个用户预设合并为一个 **薄层长方体**。预设总数变为四项：坐标轴及文字、辅助盒框、方向标、薄层长方体。
- 勾选统一预设后，每个导出条目都生成八块独立薄层长方体，八种注册材质各一块；对象与材质使用材质 ID 命名，不再使用橙／绿／蓝角色名。
- 八块薄板统一使用原橙色 `(1.0, 0.35, 0.05)`，对象、材质和灯光名称使用 `orange <材质ID>`，材质参数逐项采用 `MATERIAL_SPECS` 默认值。透射窗的默认滤色强度仍为 0，因此它按材质定义是中性透明，不额外强行染色。
- 默认采用 `right_front_groups`：平面发光、全息边缘、数据漫透射和透射窗在右侧；需要辅助灯的哑光、缎面、无折射玻璃和真实玻璃集中在前侧。两组各四块，分别沿 E 分层，中心间距 0.35，厚度 0.04。前侧组整体上移半层，`group_height_offset=0.175`，右侧为 0；两组统一移到数据体后八层按高度交替，不两两重合。四盏 Area 灯随对应薄板位于前侧并保持相同偏移。下侧仍留空，导出界面不提供布局选择。
- 删除整个“薄层长方体材质与布局”界面及其材质、参数和布局控件。旧的 `separated_right` 与 `data_aligned` 仅保留给内部兼容和材质回归验收，不再由用户导出面板调用。
- 右侧／前侧分组、高度交错、统一橙色及命名调整均已通过相关自动化、全量回归、真实 Blender、工程往返和真实窗口复验，详见当前验收记录。
- 工程级 `project.presets` 使用 `thin_boxes` 作为唯一薄板开关；八条 `box_presets` 以材质 ID 为键，并通过 `preset_group=thin_boxes` 统一启停。默认需要辅助灯的哑光、缎面、无折射玻璃和真实玻璃仍各有独立 Area 灯。
- `blender_panel.py` 的旧界面测试已替换为合并预设、设置区移除、默认八材质请求与忙碌禁用测试；工程测试覆盖八块橙色薄板、橙色命名、默认参数、布局、灯光、独立网格／材质、回读、取消预设和事务校验。
- 本节为当前行为依据，下方初版设计、实施记录及验收计数在与本节冲突时均为历史记录。

### 当前调整验收记录

- 相关自动化：`python -m unittest tests.exporting.test_blender_materials tests.exporting.test_blender_panel_ui tests.exporting.test_blender_project tests.exporting.test_blender_export -v`，**63 项通过**，覆盖统一橙色 `(1.0, 0.35, 0.05)`、橙色对象／材质命名、默认参数、右侧／前侧分组、前侧半层偏移及八层均匀交替。
- 全量回归：`python -m unittest discover -s tests -t . -v`，**1598 项通过，160.482 秒**；日志 `.local/logs/blender-orange-full-tests-utf8.log`。`python -m compileall -q bandscope plugins scripts tests start.py` 通过。
- 真实 Blender 材质验收：Blender 5.2.3 LTS 下 **15 项全部 PASS**，覆盖八种节点、统一预设八块橙色默认薄板、橙色命名、右侧／前侧各四块、前侧半层偏移、四个配套 Area 灯、独立网格／材质、混合材质内部回归、多页、Append 和原位更新；报告 `.local/outputs/blender-orange-materials/acceptance-report.md`。
- 多页工程往返：**5 项全部 PASS**，覆盖四项预设、仅勾选坐标轴与统一薄层、目录搬移、显示更新、创作对象保护、Append 和路径改向拒绝；报告 `.local/outputs/blender-orange-roundtrip/roundtrip-report.md`。
- 真实窗口：WSe₂ 数据经合并后的界面导出成功，确认材质与布局设置区不存在；八个材质 ID、默认参数、统一橙色、橙色对象／材质／灯光命名、`right_front_groups` 及前侧 `0.175` 高度偏移均通过产物核对，Blender 重开渲染投影误差 **0.000077 px**。报告 `.local/outputs/blender-orange-dialog/dialog-report.json`。
- 当前调整的 CPU／NVIDIA 冻结包：**未重新构建或复验**；既有通过记录只对应调整前版本。维护者人工审美复核：**未执行**，脚本渲染和截图查看不替代该项。

## Context

当前 Blender 导出的橙、绿、蓝三块薄层长方体都是完整六面网格，材质为 Transparent BSDF＋Emission，单表面不透明度 30%。目标是在不改变科学体数据、色带、VDB 密度及现有默认视觉效果的前提下，让用户为每块薄板独立选择八种材质，并覆盖两条新讨论路径：薄板自身不发光，以及利用数据体发光透射或漫透射染色。

现有工程为 schema 7；新建导出对所有选中页面统一应用预设，每块薄板及每个条目保持独立网格和材质。回读后的“更新工程”只替换 VDB、数组和权威配置，不改写 `.blend`，本任务必须继续保护材质、灯光、相机及用户创作。

本轮交付全部八种材质，不设置仅占位的菜单项，不新增第三方依赖，不发布版本。

## Approach

采用“**宿主纯数据材质注册表＋Blender 独立节点工厂＋可选同轴重叠布局**”：

- 宿主负责材质 ID、中文名称、默认参数、适用控件和请求校验；冻结后把不可变的逐薄板配置写入 `scene.json`。
- `blender_script.py` 只根据配置创建节点，不从活动窗口读取状态，也不导入宿主 Qt/VTK 模块；继续作为单个随包脚本资源。
- 三块薄板可分别选材质；颜色、网格、材质数据块均独立，不共享节点组。
- 保留现有“右侧沿 E 分层”默认布局，新增“与数据体同轴、沿 E 分层重叠”布局，使透射材质在导出的默认相机链路中确实有数据可透射；布局是显式选项，不因材质选择暗中移动对象。
- 非发光实体材质使用中性 Cycles 灯光；数据漫透射不添加自发光节点。灯光随条目和 Append 的数据 Collection 一起移动，但不参与科学布局边界计算。
- 保持 Standard 色彩管理、透明背景和无散射吸收＋发射体材质，不为玻璃材质切换 AgX，也不把 Blender 材质假装成宿主 VTK 预览。

## 八种材质的统一契约

| ID / 界面名称 | Blender 节点组合 | 默认照明与用途 | 关键参数和限制 |
|---|---|---|---|
| `flat_emission` / 平面发光 | Transparent＋Emission | 当前效果，继续默认；颜色最稳定 | 单表面不透明度 0.30、发光强度 1.0 |
| `matte` / 哑光薄片 | Transparent＋Principled，Transmission=0，高粗糙度、弱高光 | 中性灯；强调明暗和厚度 | 不透明度可调；自发光补偿默认 0，可设弱补光 |
| `satin` / 缎面塑料 | Transparent＋Principled，Transmission=0，中等粗糙度和高光 | 中性灯；更精致的薄片质感 | 不透明度可调；自发光补偿默认 0，高光不得遮盖数据 |
| `holographic` / 全息边缘 | Fresnel 或 Layer Weight＋Transparent＋Emission | 正面低遮挡，边缘和掠射角发光 | 正面上限、边缘上限分别记录；无折射 |
| `data_translucent` / 数据漫透射 | Transparent＋Translucent BSDF | 不使用薄板自发光；由数据体发光产生漫透射染色 | 标为实验性；需较高采样，两种布局都验证，禁止隐藏 Emission 兜底 |
| `transmission_filter` / 透射窗／滤色片 | 中性或淡色 Transparent；无折射、无表面发光 | 直接透过数据颜色；强度 0 为透射窗，大于 0 为滤色片 | 使用同轴重叠布局验收；右侧布局下明确提示当前无光路重叠 |
| `reflection_glass` / 无折射玻璃 | Fresnel＋Transparent＋Glossy/Anisotropic | 中性灯；只保留反射与边缘高光，不弯曲数据 | 粗糙度、反射强度入配置；不是 Glass BSDF 假透明混合 |
| `optical_glass` / 真实玻璃／亚克力 | Glass 或 Principled Transmission，必要时 Volume Absorption | 中性灯；展示用途的真实折射和厚度吸收 | IOR、粗糙度、吸收密度入配置；不用 Transparent 混合伪装物理不透明度 |

哑光与缎面的“弱自发光”是同一材质的参数，不另算第九种预设。透射窗／滤色片也是同一材质，通过滤色强度区分。所有参数均可在导出的 Blender 节点中继续编辑；应用首版只暴露有明确含义的控件，不复制整套 Blender 节点编辑器。

## Key changes

### 1. 材质注册表、请求和 schema 8

- 新增纯 Python、无 Qt/bpy 导入的 `bandscope/exporting/blender_materials.py`：定义上述八种 ID、标签、说明、默认参数、数值范围、是否需要灯光、是否建议重叠布局及是否支持透明度/自发光/滤色控件。
- 扩展 `BlenderProjectRequest`：接受逐 `orange_box/green_box/blue_box` 的材质配置和统一 `box_layout`；缺省请求仍得到平面发光、右侧布局、0.30 单表面不透明度，保持既有调用兼容。启动任务后复制并冻结配置，拒绝未知 ID、NaN/Inf、越界参数和不安全元数据。
- `blender_project.py` 将解析后的配置写入工程级选择和逐条目 `box_presets`，记录参数语义、布局、灯光及推荐渲染采样；不同页面仍各自拥有独立材质名和网格名。
- 工程和条目 schema 升为 8。读取器明确接受 schema 7 与 8；schema 6 及更早仍只有限恢复。schema 7 原位更新不得被伪装成已含八材质和灯光的 schema 8 工程：保留原版本/兼容标记和原 `box_presets`，不因生成新数组而重建材质。
- `merge_updated_entry` 同时保留材质、布局和灯光元数据；现有“只更新数据资产、不改写 `.blend`”的事务及并发指纹检查不变。

### 2. 八类节点工厂与透明语义

- 把 `blender_script.py` 当前单一 `translucent_material` 扩展为按 ID 分派的节点工厂和参数校验；保留现有 `BandScope opacity` 的默认分支行为，为其余节点提供稳定、易读的名称和标签。
- Transparent 混合类材质统一把自发光放在非透明分支内部，禁止在最终透明混合之后无条件 Add Emission。`data_translucent`、`transmission_filter` 以及补偿为 0 的哑光/缎面/玻璃不得存在有效薄板自发光。
- 全息材质用 Fresnel/Layer Weight 控制从正面到掠射角的混合，不用 Wireframe 节点制造三角化对角线。
- 真实玻璃使用封闭薄板的实际两个界面和 0.04 厚度；透射滤色明确前后表面会连续滤色两次。
- 保留当前“0.30 是单表面不透明度”的兼容语义，在 UI、元数据和说明中明确；正对穿过封闭薄板时理论总不透明度为 `1-(1-a)^2`，不在本任务暗中改成另一套标定。
- 能力探测增加八类所需 BSDF、Fresnel/Layer Weight、Volume Absorption、Cycles 灯光链接等检查；缺失时明确报错，不静默退回平面发光。

### 3. 布局、灯光和 Append

- `_box_metadata` 支持 `separated_right`（现有右侧、层间距 0.35）和 `data_aligned`（以实际数据中心为 XY 中心，在数据 E 范围内有序布置三层）。科学网格、ROI 和父对象位移不变；薄板厚度不偷偷缩小，实际相交关系写入报告。
- 同轴模式按数据 E 范围布置而非机械套用过大的固定间距，保证常规及裁剪数据都有透射路径；极薄数据只记录实际交叠，不虚构完整体积包络。
- 多页面仍按 Blender 实际求值边界沿世界 X 排布；从取景、组间距和总览相机边界中排除 LIGHT，避免灯的位置拉大科学排版。
- 为需要外部照明的薄板创建逐条目中性 Area 灯组，尺寸和能量随条目范围缩放，父对象为对应 `BandScope Root`，置于其数据 Collection 内，Append 后仍能使用。
- 用 Cycles 灯光链接把辅助灯直接照明限制到需要它的薄板，避免直接照亮数据漫透射薄板和科学体。混合材质场景仍可能有间接光，文档如实说明；“全数据漫透射、无辅助灯”的纯数据照明用例必须单独通过。
- 无需灯光的材质组合不创建空灯组；默认平面发光不增加灯光，保持基线场景和视觉。数据体 `density/color` VDB 与体积节点公式不改。

### 4. 导出界面

- 在 `blender_panel.py` 的预设区域增加三行材质下拉框（橙/绿/蓝独立）、薄板布局下拉框，以及按所选材质启用的不透明度、自发光补偿和滤色强度控件；默认值与现有导出一致。
- 每种材质显示简短用途和限制说明。透射窗在右侧布局提示“需与数据体重叠才有透射染色”；数据漫透射标明高采样和数据强度依赖；真实玻璃标明折射会改变视觉位置。
- 不透明度只应用到支持该语义的材质；真实玻璃和透射滤色不显示误导性的同一 Alpha 控件。高级 IOR、粗糙度、吸收等使用注册表默认值，在 Blender 中编辑。
- 忙碌/取消时全部新控件按现有规则禁用；请求冻结后修改 UI 不影响已启动任务。参考缩略图仍明确不是 Blender 材质预览，不在 VTK 中仿制八种材质。
- 新建导出默认仍是平面发光；是否勾选六预设、跨源冻结、精确代次等待、取消和事务覆盖逻辑不改。

### 5. 校验、文档和打包边界

- `validate_box_presets` 和宿主 `_validate_entry_report` 从“只检查一个 Mix Fac”升级为按八种契约检查节点类型、链接、关键默认值、无自发光要求、布局、独立材质和灯光；缺对象或材质意外共享仍拒绝提交。
- 新增 `scripts/validation/blender_material_acceptance.py` 及必要的 Blender 内检查段，逐一真实生成、重开、渲染八种材质，并覆盖混合材质、部分勾选、同轴透射、多页面和 Append。
- 更新 `docs/development/blender-export.md`：八材质表、节点与参数、双表面透明/滤色、两种布局、灯光链接、采样成本、实验性限制、Append 和 schema 8 回读边界。
- 以本文件作为开放事项状态主记录并维护 `docs/plans/README.md` 入口；实施时同步 `docs/architecture/repository-map.md` 的 schema/模块说明、`docs/development/testing.md` 的验收命令和资产 README 模板。若实施中改变约定，同步适用 AGENTS.md。
- `blender_script.py` 仍是唯一新增 Blender 随包脚本资源，宿主注册表由正常导入收集，原则上无需改两个 spec；必须分别用 CPU/NVIDIA 配置验证资源解析，不能只验证源码启动。

## Verification

### 自动化

新增/更新 `tests/exporting/`、`tests/integration/` 和 `tests/support/` 的合成测试，不依赖本机实验文件或 Blender：

- 八种 ID、默认值、参数范围、逐薄板独立选择、非法配置、冻结后 UI 变化和所有预设关闭。
- schema 7/8 回读、旧工程有限恢复、原位更新保留版本/材质/灯光/布局且 `.blend` 哈希不变。
- 两种布局的坐标、ROI/降序轴/裁剪、多页无碰撞、总览取景排除灯光、部分预设和请求负向校验。
- 重开报告按八种节点契约校验；缺节点、错误发光、共享材质、缺灯/不应有灯、透射无重叠及缺失报告均拒绝提交。

运行：

```powershell
python -m unittest tests.exporting.test_blender_materials tests.exporting.test_blender_project tests.exporting.test_blender_panel_ui tests.exporting.test_blender_export -v
python -m unittest discover -s tests/exporting -t . -v
python -m unittest discover -s tests -t . -v
python -m compileall -q bandscope scripts tests start.py
```

### 真实 Blender 与端到端

```powershell
python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe
python scripts/validation/blender_material_acceptance.py --blender D:/blender/blender.exe --data .local/data/WSe2_step.npz
python scripts/validation/blender_project_roundtrip.py --blender D:/blender/blender.exe
python scripts/validation/blender_dialog_acceptance.py .local/data/WSe2_step.npz --blender D:/blender/blender.exe --multi
```

- 同一非对称合成数据分别渲染八张图，检查实际节点、重开、非空输出、透明通道和相机投影；另导出混合材质工程验证三块薄板互不联动。
- 数据漫透射分别验证右置和同轴布局，并比较 32/128/256 采样；纯数据照明用例不得有薄板 Emission 或辅助灯直接照明。若效果不达标，调整 Translucent 比例、布局或采样，不得偷偷加自发光冒充通过。
- 透射窗/滤色片在同轴布局验证实际光路和前后两次滤色；真实玻璃验证厚度、折射和独立参数；其余材质检查高光、边缘、遮挡和体数据颜色可读性。
- 检查平面发光相对现有基线无非预期变化；多页、目录搬移、Append、显示更新后材质/灯光/创作对象保持，`.blend` 字节摘要不变。
- 实际打开窗口逐类选择并导出至少一个八材质组合、一个同轴透射工程，确认控件说明、禁用状态、冻结和取消行为；查看全部渲染图，维护者人工审美验收与脚本结果分别记录。
- 按现有发布文档构建 CPU/NVIDIA 开发验证包，分别运行 `verify_frozen_blender_flow.py` 和新增材质导出链路；不将开发包称为签名发行包。

全部自动化、八材质真实 Blender 渲染和源码端到端通过后才可标记源码完成；冻结包或维护者视觉未完成时必须如实保留开放事项。

## Assumptions

- “八种”指：平面发光、哑光薄片、缎面塑料、全息边缘、数据漫透射、透射窗／滤色片、无折射玻璃、真实玻璃／亚克力；弱自发光和滤色强度是参数，轮廓线框不属于本轮。
- 三块薄板在导出时可独立选材质；布局与共用数值控件统一应用于所有选中页面，导出后仍可在 Blender 中逐块、逐组自由编辑。
- 默认继续右侧布局和单表面 30% 不透明度，不自动根据材质移动薄板；同轴重叠是用户可选择的配套布局，界面会提示透射材质需要它。
- 数据漫透射严格遵守“薄板无自发光”；混合材质场景只能限制辅助灯直接照明，不宣称隔绝所有物理间接光。
- 高级光学参数先提供经过渲染校准的默认值并暴露在 Blender 节点中，本轮不扩大为完整材质编辑器，也不加入金属、次表面散射、轮廓线框或 Eevee 专用预设。
- 保留工作区现有全部未提交修改，不回退或覆盖其他任务成果；既有无关失败单独报告。本任务不改版本号、不签名、不发布。

## 实施记录（2026-11-09）

### 代码与契约

- 新增 `bandscope/exporting/blender_materials.py`：八种材质 ID、中文名称、默认参数、范围、UI 控件声明、是否需灯光、是否建议同轴布局、推荐采样和节点契约；请求阶段拒绝未知 ID／参数／布局、布尔参数、NaN/Inf 和越界值。
- `blender_project.py` 升为 schema 8：`BlenderProjectRequest` 接受逐 `orange_box/green_box/blue_box` 的材质配置和统一 `box_layout`；`scene.json` 的工程级 `box_materials`／`box_layout`／`box_lighting`／`render` 与条目级 `box_presets`／`box_lighting` 记录材质、参数、中心、尺寸、层间距、是否与数据体相交、灯光和推荐采样。读取器接受 schema 7 与 8；schema 7 原位更新保留原版本、原 `box_presets`，不升级为八材质工程。原位更新同时保留 `box_lighting`，继续不改写 `.blend`。
- `blender_script.py`：按 ID 分派节点工厂；自发光只放在非透明分支内；数据漫透射与透射滤色不存在薄板 Emission；用 Backfacing 屏蔽全息材质的背面 Fresnel；真实玻璃使用封闭薄板的两个界面与 Volume Absorption；两种布局（右侧分层 0.35、同轴 E 范围三层）；逐条目中性 Area 灯、Cycles 灯光链接接收者、灯组名称、采样和重开校验。
- `blender_panel.py`／`blender_project_dialog.py`：三行逐薄板材质下拉、布局下拉、按材质启用的不透明度／自发光补偿／滤色强度控件、用途与限制说明；默认平面发光、右侧布局、单表面 0.30。导出面板放入滚动区并按桌面可用高度确定对话框初始尺寸，避免 125% 缩放下控件压缩重叠。
- 宿主 `_validate_entry_report` 按八种契约检查材质 ID、参数、透明度、滤色强度、自发光、布局、几何相交、独立材质、灯组和接收者；缺失报告、缺灯／不应有灯、错误发光、共享材质、无重叠均拒绝提交。README 与 `docs/development/blender-export.md` 同步。

### 自动化

- `python -m unittest tests.exporting.test_blender_materials tests.exporting.test_blender_project tests.exporting.test_blender_panel_ui tests.exporting.test_blender_export -v`：**63 项通过**；日志 `.local/logs/blender-schema8-materials-tests.log`。
- `python -m unittest discover -s tests/exporting -t .`：**332 项通过**；日志 `.local/logs/blender-schema8-exporting-tests.log`。
- `python -m unittest discover -s tests -t .`：**1598 项通过，83.128 秒**；日志 `.local/logs/blender-schema8-full-tests.log`。
- `python -m compileall -q bandscope scripts tests start.py` 通过。

### 真实 Blender 与端到端

- 新增 `scripts/validation/blender_material_acceptance.py` 与 Blender 内检查段 `blender_material_scene.py`。Blender 5.2.3 LTS 下 **16 项全部 PASS**：八个材质逐一真实生成、重开、渲染（透明通道与可见像素）；混合材质只让哑光／玻璃薄板收到辅助光且数据体不在接收者内；混合材质目录搬移与原位更新后材质／灯光保持且 `.blend` 摘要不变；部分勾选；数据漫透射右置 vs 同轴、无自发光／无辅助灯、32/128/256 采样序列；透射窗强度 0 与 0.8 的实际染色差异；多页独立灯组、无碰撞和总览取景；Append 后灯光链接保持；WSe₂ 真实数据同轴漫透射／滤色／玻璃渲染。报告 `.local/outputs/blender-material-acceptance/acceptance-report.md`。
- `python scripts/validation/blender_project_roundtrip.py --blender D:/blender/blender.exe`：5 项通过，覆盖多页、部分预设、目录搬移后回读与显示编辑、`.blend` 摘要不变、多页 Append、VDB 路径改向拒绝更新。
- 真实窗口：`blender_dialog_acceptance.py ... --materials` 与 `--transmission` 均通过，报告 `.local/outputs/blender-schema8-dialog-materials/dialog-report.json`、`.local/outputs/blender-schema8-dialog-transmission/dialog-report.json`；可在面板逐薄板选材质、切换同轴布局、核对控件启用状态和说明，并真实导出 + Blender 重开渲染。截图顶部受主窗口淡入影响存在半透明残影，控件状态以 report 与独立布局探针为准；取消流程只由自动化协作取消用例覆盖，未在真实窗口人工点击。

### 发现并修复

- Blender 5.2 中 `ShaderNodeBsdfGlossy` 实际节点类型为 `ShaderNodeBsdfAnisotropic`；材质契约和重开校验同时接受两者，避免无折射玻璃导出失败。
- 封闭薄板背面的 Fresnel 在 Cycles 中会按原始法线参与计算，整块背面被误判为掠射角而失去“正面低遮挡”；全息材质增加 `Geometry → Backfacing` 屏蔽，真实渲染的正面 alpha 从约 0.8 降到约 0.09（边缘仍保留高光）。
- 125% 缩放下新增材质组导致对话框超过桌面高度并压缩重叠；改为滚动区承载导出面板、参考缩略图限制到 300×160、材质说明固定 5 行高度，探针确认无滚动条时面板完整显示。

### 冻结包

- 按现有构建文档分别构建本机未签名 CPU／NVIDIA 开发验证包：`.local/dist-validation/cpu/BandScope/BandScope.exe`、`.local/dist-validation/gpu/BandScope_NVIDIA/BandScope_NVIDIA.exe`（非发行安装器，不签名、不发布）。
- 两个包分别运行 `verify_frozen_blender_flow.py` 均全部通过：包内 `bandscope/exporting/blender_script.py` 资源存在；冻结进程完成平面发光默认工程导出、回读和原位更新；同一进程再生成 schema 8、同轴布局、哑光／数据漫透射／真实玻璃的八材质工程；外部 Blender 重开后 VDB、材质、灯光链接与 CPU 渲染通过，投影误差 **0.000032 px**。日志 `.local/logs/blender-schema8-frozen-cpu-validation.log`、`.local/logs/blender-schema8-frozen-gpu-validation.log`；报告与渲染在 `.local/outputs/blender-schema8-frozen-cpu/`、`.local/outputs/blender-schema8-frozen-gpu/`。

### 未完成项（必须保留）

- 维护者人工审美验收：**未执行**。实施中查看了八材质渲染、透射图、多页与真实数据图，但不据此声称维护者验收通过；高光、边缘、折射对数据位置的影响和整体观感仍需维护者复核。
- 真实窗口的取消点击：未单独人工点击验证；面板的协作取消、忙碌禁用和冻结后请求不变由 `tests/exporting/test_blender_panel_ui.py` 覆盖。
