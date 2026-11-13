# Blender 工程导出与回读

BandScope 可以把一个或多个冻结的 3D 页面转换为可继续创作的 Blender 工程，也可以重新打开自己导出的工程，调整基础显示参数后原位置更新科学资产。需要用户本机安装 Blender；应用不会自动安装软件，也不会在导出完成后自动弹出 Blender。

## 新建工程

1. 点击顶部栏 **导出 Blender 工程**，打开独立对话框。「截图样式」和 `Ctrl+S` 仍只用于图片预览与图片导出。
2. 在「数据源 → 3D 页面」树中勾选页面，支持跨数据源及同一来源的多个 3D 页面；全局分组副本按条目身份去重。默认只勾选当前 3D 页，空选择不能导出。
3. 在 **数据体外观** 下拉框选择 **自发光**（`emission`，默认）或 **柔和受光**（`hybrid_lit`），应用于全部选中页面。自发光保持旧行为；柔和受光生成混合材质和配套灯光。导出窗口不提供材质或灯光参数，细调在 Blender 内完成；选项不跨重启记忆，提交任务时冻结并在任务期间禁用。
4. 按需统一选择五项预设：**坐标轴及文字、辅助盒框、方向标、薄层长方体、文本预设**。新建导出默认全部勾选，未勾选的对象不会创建。五项预设与数据体外观独立；全部关闭时，柔和受光仍创建所需三盏数据体灯。
5. **薄层长方体**是一个统一开关：选中后为八种材质各创建一块薄层长方体，共八块。八块都使用统一的橙色 `(1.0, 0.35, 0.05)`、各自材质的默认参数和默认方位。导出界面不再提供逐薄板材质、参数或布局设置，也没有「薄层长方体材质与布局」设置区。
6. **文本预设**是一个统一开关：选中后一次生成十种可编辑 Text 对象，正文全部为大写 `A`，实际字高统一为 0.12 Blender 单位，在数据体下方按两行五列排列。每个对象都有独立文字曲线和材质；DejaVu 字体及许可证写入配套 `fonts/`，字体同时打包进 `.blend`。未勾选时不创建文本、字体资产或文本辅助灯。
7. 选择 `blender.exe`。应用依次尝试已保存路径、PATH、Windows 安装记录和标准安装目录，手动选择后会记住路径。开始前还会检查 NumPy、OpenVDB、体积系数节点、八种材质节点、可编辑文本、Cycles 和灯光链接能力；验收版本为 Blender 5.2.3 LTS。
8. 选择 `名称.blend`。导出前会提交当前编辑，在 GUI 线程逐源逐页激活并等待对应精确计算代次；冻结后，数组准备和外部 Blender 进程不再读取活动窗口。成功、失败或取消都会恢复原活动源、页面、导航和相机状态。
9. **同时保存复现文件**默认勾选，本次窗口运行内记住取消选择，不写入全局偏好。开启时额外生成 `名称.blend.bsview.json`，保存逐页科学配方、显示与质量、来源会话分组、条目顺序，以及五项预设、数据体外观、材质、布局和渲染设置；不保存原始 NPZ 路径、Blender 路径或原始数据数组。任一页面缺少有效配方都会在转换前指出页面并中止。
10. 成功后同时得到 `名称.assets/`。工程、资产目录和可选复现配置统一暂存、校验和提交；覆盖检查包含同名配置，失败或取消整体回滚。取消保存配置并覆盖旧工程时，提示会明确旧配置也会移除，避免留下过期配置。

**移动或分享时必须同时携带 `.blend` 和同名 `.assets/` 整个目录；需要完整复现时还要携带同名 `.blend.bsview.json`。** 工程通过相对路径引用 VDB；另存工程或 Append 不会自动复制资产。

## 加载 Blender 配套复现文件

通过顶部 **加载数据** 选择 `.bsview.json`，程序按文件内 `format` 区分图片配方和 Blender 配套配置。Blender 配置可以单独分享和加载，不要求同目录存在 `.blend` 或 `.assets/`。

- 每个原来源会话组都必须主动选择一次原始 NPZ，同组多页共用这次选择；同一文件当初若被加载为两个独立会话，仍会分别询问。程序不使用旧路径、已打开数据或历史选择跳过选源，并继续核对 SHA-256 与数据布局。
- 全部来源完成选择、校验和预计算后才整体新增工作区。任一来源取消、失败或提交失败都会撤销本次全部新增内容，保留原有数据和页面。共同祖先合并一次，逐页恢复旋转、相机、显示、质量和效果；重复记录存在真实科学参数冲突时会要求重新冻结导出。
- 成功后选中首个导出页面，打开已填入原导出设置的 Blender 面板并勾选原导出页面。这些设置只是本次草稿，不自动再次导出，也不覆盖全局偏好。

## 多页面场景与预设

- 每个条目有独立的数据 Collection、父对象、体材质、备用相机和 VDB，按页面树顺序沿世界 X 方向排列。科学网格坐标保持不变，排布只平移父对象。
- 组间距按 Blender 实际求值对象边界计算，包括全部已选预设；文字采用实际求值网格顶点，避免空心轮廓在保存重开后的边界估计差异。第一个/单个条目保持父对象零位移。多页面活动相机为沿首页视角的总览相机，各页原相机保留为备用。
- 坐标轴及文字在左侧，辅助盒框在后侧，立体方向标在上方，十种文本预设在下侧。辅助盒框默认不参与渲染。
- 八块薄层长方体都是完整六面网格，不是切角或剖切轮廓；厚度固定 0.04 Blender 单位。每块、每个数据组都拥有独立网格、独立材质和独立节点树，不共享节点组。
- 默认方位为**右侧／前侧分组沿 E 分层**（`right_front_groups`）：平面发光、全息边缘、数据漫透射和透射窗在右侧；哑光、缎面、无折射玻璃和真实玻璃这四种需要辅助灯的材质集中在前侧，与后侧辅助盒框相对。两组各有四块薄板，分别围绕对应方位沿 E 排列，相邻层中心间距 0.35；前侧组整体上移半层，即 `group_height_offset=0.175`，右侧偏移为 0。两组统一移动到数据体上后，八块薄板按高度交替排列，不会两两重合。四盏辅助灯随对应薄板位于前侧并保持相同高度偏移。两组都不与数据体在空间上重叠，因此数据漫透射和透射滤色没有与数据体的光路重叠；这些位置由默认布局固定，不需要用户在界面中选择。
- `density` 表示对象空间中裁剪后的**总消光系数**，弱信号裁除阈值为 0。两种外观使用完全相同的强度、颜色、alpha、density 数组和 VDB 网格；外观模式不改变科学数据转换。
- 在 Blender 中组合结果时使用 **File → Append → `.blend` → Collection**，只追加所需数据 Collection，不追加相机 Collection，目标场景相机就不会被替换。Append 会连同数据 Collection 内的辅助灯和接收者 Collection 一起带入，灯光链接仍然有效。

## 数据体两种外观

两种模式沿用 `Volume Coefficients`、对象空间密度、裁剪遮罩和线性 RGB 色带。令 `σ` 为裁剪后密度乘 `Density multiplier`，`c` 为 `Color adjustment` 调整后的线性 RGB：

| 系数 | 自发光 `emission` | 柔和受光 `hybrid_lit` |
|---|---|---|
| 散射反照率 `ω` | 0 | `0.65 × clamp(c, 0, 1)` |
| 散射系数 | 0 | `σ × ω` |
| 吸收系数 | `σ` | `σ × (1 − ω)` |
| 发射系数 | `σ × c` | `0.15 × σ × c` |
| 各向异性 | 0 | 0 |

- 混合模式逐 RGB 通道保持吸收＋散射＝原总消光，不额外增厚数据体；零密度处三类系数都为零。节点保留 `Density multiplier=1`、`Color adjustment`，并提供 `Scattering amount=0.65`、`Emission compensation=0.15`、`Anisotropy=0`。自发光模式对应值为 0、1、0。
- 每个条目、每种模式都创建独立可变材质，不跨条目共享。混合模式至少 128 samples、`volume_bounces=2`，并保留去噪、Standard 色彩管理和透明背景；自发光渲染设置不变。
- 混合模式每个条目创建三盏中性白色 Area 灯。令 `L=max(数据体最大边长, 0.1)`、`d=2L`，灯边长均为 `L`，朝向数据体中心。主光方向 `(-1,1,1.5)`、功率 `60×d²` W；补光 `(1,0.3,1)`、主光的 25%；轮廓光 `(0.5,1,-1)`、主光的 50%。方向用导出相机的 right/up/backward 基向量归一化后乘 `d`。
- 灯组与专用接收者位于对应数据 Collection 内，灯父对象为条目 Root，接收者只含该 Volume。初始方位由相机确定后不绑定相机运动；Cycles 灯光链接只限制直接照明，不宣称隔绝所有间接光。灯不参与排版、总览相机或取景边界。
- 数据漫透射薄板依赖数据体发光，混合模式发光减弱后可能变暗；导出不会暗中增加薄板发光补偿恢复旧外观。上述材质与灯光数值是首版默认值，仍需维护者人工视觉复核。

## 文本预设十种样式

文本注册表在 `bandscope/exporting/blender_texts.py`（纯 Python，无 Qt／bpy）。每个条目的十个 A 都朝向该条目原始相机，采用两行五列；按实际字形高度而不是仅按 Blender 字号参数统一为 0.12。每个 Text 对象都可直接修改正文、字体、挤出、倒角和材质，不需要转成网格。

| ID / 预设 | 字体与默认效果 |
|---|---|
| `sans_regular` / 无衬线常规 | DejaVu Sans，黑色平面 |
| `sans_bold` / 无衬线粗体 | DejaVu Sans Bold，黑色平面 |
| `serif_regular` / 衬线常规 | DejaVu Serif，黑色平面 |
| `serif_italic` / 衬线斜体 | DejaVu Serif Italic，黑色平面 |
| `monospace_regular` / 等宽常规 | DejaVu Sans Mono，黑色平面 |
| `extruded_bold` / 立体粗体 | 粗体、少量挤出与倒角、深色哑光 |
| `outline` / 空心描边 | 无填充的可编辑文字轮廓，带细线厚度 |
| `emissive` / 发光文字 | 青色自发光，强度 2.0，少量挤出 |
| `metallic` / 金属文字 | 银灰金属，Metallic=1.0、Roughness=0.24，挤出与倒角 |
| `glass` / 玻璃文字 | Glass＋Transparent，玻璃权重 0.65、IOR=1.45、Roughness=0.04，挤出与倒角 |

五种 DejaVu 字体文件从应用已有的 Matplotlib 资源复制到工程级 `fonts/`，并打包进 `.blend`；`fonts/LICENSE_DEJAVU` 一同分发。文本和字体在所有页面间共享字体文件，但不共享 Text 对象、文字曲线或材质。金属、玻璃及立体效果依赖视角、灯光和背景，不承诺在任意背景下都与平面黑字具有相同对比度。

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
- 文本预设启用时，每个条目另有一盏中性 Area 灯，接收者只包含该条目的十个 Text 对象；科学体、薄板、坐标轴和辅助盒框不在文本接收者 Collection 内。
- 灯位于数据 Collection 内的灯组 Collection，父对象为对应的 `BandScope Root`；薄板灯的尺寸和能量随薄板范围与距离缩放，文本灯位于文本组的相机相对方位。柔和受光另有独立三灯灯组和只含 Volume 的接收者。灯光对象、实际位置、颜色、功率、尺寸、接收者 Collection 和灯组 Collection 都记录在 `scene.json`。
- 使用 Cycles 灯光链接（`light_linking.receiver_collection`）分别限制薄板、文本和数据体辅助灯的直接照明。混合材质场景仍可能有间接光；不宣称隔绝所有物理间接光。数据漫透射薄板自身不发光，也不使用薄板辅助灯直接照明。
- 辅助灯从取景、组间距和总览相机边界计算中排除（按对象类型 `LIGHT`），不会拉大科学排版。
- 若 Blender 缺少八材质节点、可编辑文本能力或 Cycles 灯光链接能力，开始导出前明确报错，不静默退回简化预设。

## 打开已有工程并更新

独立对话框中的 **打开已有工程** 只支持 BandScope 导出的 `.blend` 及完整同名 `.assets/`。普通 `.blend`、缺失资产、未来版本 schema 或结构不一致会给出具体错误，不会创建空页面。

- 打开后恢复的是带工程来源标识的**冻结 3D 结果**，不是原始实验数据；此入口不恢复原 NPZ、时间轴、插件状态或完整处理链。需要完整分析工作区时，改从“加载数据”打开同名 `.blend.bsview.json`。
- 多页工程可按唯一条目 ID 分别调整。每个条目独立保存 colormap、三点色阶、锁定范围、是否包含零、显示样式和质量，切换条目不会串用状态；schema 7–9 只显示旧映射曲线。
- 回读窗口只读显示**导出时的数据体外观**；切换模式需要重新导出。宿主预览复用 BandScope 的 PyVista/VTK 体渲染管线，不嵌入 Blender/Cycles，不模拟 Blender 材质与灯光。Cycles 与 VTK 的体积分方式不同，不承诺逐像素一致。
- 点击 **更新工程** 后，BandScope 用外部 Blender 后台进程只读打开工程，核对条目、体对象、父对象和相对 VDB 路径，再暂存新的 VDB。所有修改文件经过校验、备份后整体提交；并发变化、路径改向、对象缺失、写入失败或提交后校验失败都会中止并回滚。配套 `.blend.bsview.json` 存在时也纳入并发指纹和同一提交事务。
- 原位更新只替换修改条目的 `volume.vdb`、`arrays.npz`、工程级 `scene.json` 和已有配套复现配置，**不重建、不保存、不改写 `.blend`**。相机、灯光、排版、文字、附加对象和材质创作均保留。`scene.json` 只描述生成时设置，不声称反映用户随后在 Blender 中的修改；新建产物的严格默认参数校验不会用于拒绝手调工程的原位更新。
- 配套配置更新采用“原科学配方不变 + 逐条目显示覆写”。被编辑移除的局部增强和插件透明度倍率会明确记录，之后加载配置时不得重新启用；未修改条目的配方与覆写保持不变。
- 没有配套配置的旧工程继续支持现有显示编辑和原位更新，并提示从原始数据重新导出可获得完整复现配置，不会凭空补写科学配方。
- 如果工程已经在 Blender 中打开，更新后必须**重新载入 VDB 或重新打开工程**，界面会显示这一要求。

### 基础往返与已烘焙效果

第一版保证普通全局色阶、colormap 和不透明度的基础往返。局部增强和插件透明度倍率已经解析到体素，不能拆分恢复为原规则；含这些效果的条目在覆盖保存前会明确警告，基础显示重算会改变或丢弃这些效果。

schema 6 及更早工程只做有限恢复：可读取强度、坐标和已有色阶换算信息；未保存的 colormap 必须重新选择，并显示无法判断烘焙内容的通用警告。完整局部增强规则、插件参数、原始处理链和时间轴恢复不在当前范围内。

schema 7–10 工程仍可打开和原位更新：缺少数据体外观时按自发光解释，宿主保留原版本号、原预设、材质、灯光和布局，不升级 schema，也不补柔和受光灯组。需要独立透明度、归一化、新薄板／文本预设或柔和受光时请重新导出，或继续在 Blender 中编辑已有对象。

新工程为 **schema 11、显示编辑格式 2**。`bandscope_edit` 除原色带、三点色阶、锁定范围和旧映射曲线外，还保存独立显示样式、已解析强度范围及渲染质量。schema 10 起支持独立显示编辑；schema 7–9 不接受原位启用独立显示或显式质量配置。

## schema 11 资产契约

`名称.assets/` 的典型结构为：

```text
名称.assets/
├── scene.json
├── README.txt
├── fonts/
│   ├── DejaVuSans.ttf
│   ├── DejaVuSans-Bold.ttf
│   ├── DejaVuSerif.ttf
│   ├── DejaVuSerif-Italic.ttf
│   ├── DejaVuSansMono.ttf
│   └── LICENSE_DEJAVU
└── entries/
    ├── entry-001/
    │   ├── arrays.npz
    │   └── volume.vdb
    └── entry-002/
        ├── arrays.npz
        └── volume.vdb
```

关闭文本预设时不创建 `fonts/`；启用时字体也打包进 `.blend`，但移动、分享和回读仍以完整同名 `.assets/` 为准。

- `scene.json` 是唯一权威编辑配置，按工程和条目记录资产路径、来源、坐标、相机、布局、预设、八薄板材质与参数、十种文本样式、字体、辅助灯、推荐采样及 `bandscope_edit` 编辑状态。`.blend` 内嵌的 `scene_metadata` 仅供生成期诊断，不作为回读依据，更新时也不改写它。
- 工程级 `project.presets` 只记录五项用户预设，其中 `thin_boxes` 控制全部八块薄板，`text_presets` 控制全部十种文本；`project.box_layout` 记录布局 ID；`project.box_materials` 以八种材质 ID 为键记录标签、默认参数、所需灯光和推荐采样；`project.text_styles` 与 `project.text_fonts` 记录文本注册表和字体资产；`project.display_edit_format=2` 标记当前显示编辑契约。
- `project.volume_shading` 保存 `emission`／`hybrid_lit` 模式及生成时解析后的材质参数；`project.render.volume_bounces` 和采样设置记录对应渲染要求。
- 每个条目的 `volume_lighting` 保存灯组、专用接收者、灯对象及生成时的实际局部位置、尺寸、颜色和功率。用户随后在 Blender 中手调节点或灯光不会回写到这些字段，原位更新会原样保留它们。
- 每个条目的 `bandscope_edit.display_style` 保存独立／兼容模式、归一化策略及透明度参数，`render_quality` 保存插值与静止质量，`resolved_range` 保存实际已解析强度范围。schema 10 及以上工程可调整这些设置，但不允许把旧工程原位升级成独立显示。
- 每个条目的 `box_presets` 包含八条记录，分别记录 `preset_group=thin_boxes`、`layout_slot=right/front`、`group_height_offset`（右侧 0、前侧 0.175）、实际中心、尺寸、厚度、层间距、统一橙色、是否与数据体相交以及材质参数；`box_lighting` 记录灯组、接收者 Collection 和灯光对象名。
- 每个条目的 `text_presets` 包含十条记录，统一 `body=A`、`preset_group=text_presets`，并记录样式、字体、材质、两行五列的位置和实际中心；`text_layout` 记录下方布局、相机朝向和 0.12 字高，`text_lighting` 记录独立文本灯组和接收者。
- `arrays.npz` 保存处理后强度、显示颜色、alpha 和体密度，供复核与重新转换。重新调色以强度数组和编辑参数为准，不从最终 RGB 或 Blender 材质反推科学强度。
- `volume.vdb` 包含 `density` FloatGrid 和 `color` Vec3SGrid，是 Blender 的实际渲染资产；`density` 为总消光系数，不随外观模式改成散射系数。
- BandScope 的 0–200 显示空间按 `(display - 100) × 0.01` 映射为居中的 2 个 Blender 单位。ROI、物理坐标、降序坐标及 E 轴相机翻转语义原样保留，E、kx、ky 不解释为毫米。
- 透明度按 `σ = -log(1-α) / 单位距离` 换算，并对接近 1 的 alpha 做有限截断。色阶锁定、gamma、局部增强和插件倍率在导出时只应用一次。
- 原位更新保留项目级材质、文本、字体、布局、数据体外观、灯光和条目级 `box_presets`、`volume_lighting`、`text_presets`；只替换修改条目的 VDB、`arrays.npz` 和 `scene.json`，不改写 `.blend` 中的创作。

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
python scripts/validation/blender_volume_lighting.py --blender D:/blender/blender.exe
python scripts/validation/blender_dialog_acceptance.py .local/data/WSe2_step.npz --blender D:/blender/blender.exe --volume-mode emission
python scripts/validation/blender_dialog_acceptance.py .local/data/WSe2_step.npz --blender D:/blender/blender.exe --volume-mode hybrid_lit
python scripts/validation/blender_recipe_acceptance.py --blender D:/blender/blender.exe
```

合成验收覆盖四种投影、VDB 逐体素回读、CPU 渲染、目录搬移和 Append。多页往返验收覆盖五项及部分预设、两组独立十种文本、组间不碰撞、总览与备用相机、压缩工程回读、显示参数更新、`.blend` 摘要不变及创作对象保护。八材质验收逐个真实生成、重开并渲染八种材质，并明确检查统一预设一次生成八块橙色默认薄板，另覆盖独立材质、灯光链接、多页、Append 和原位更新。文本自动化与真实重开检查统一大写 A、0.12 字高、两行五列、独立曲线和材质、字体打包、材质节点、文本灯光链接及关闭开关后的对象缺席。

`blender_volume_lighting.py` 另覆盖灰白与彩色合成体两模式、隔离环境照明后的开关灯对照、移动灯几何不变、多页独立灯组、全预设关闭与共存、目录搬移、Append、手调材质／灯光后的原位更新，以及 WSe₂ 同视角两模式 128／256 samples 图像、局部裁图和耗时。报告与图像写入 `.local/outputs/blender-volume-lighting/`，日志写入 `.local/logs/`。真实窗口脚本分别验证两个模式的下拉选择、忙碌冻结和实际导出产物。

冻结验证包构建后运行：

```powershell
python scripts/validation/verify_frozen_blender_flow.py <BandScope.exe> --blender D:/blender/blender.exe
```

自动化和真实渲染不能替代维护者人工视觉复核；两模式的内部连续性、遮蔽、偏色、过曝、弱带与去噪、八种材质观感与灯光高光、十种文本的字号与材质观感、多组排版及 Append 后创作体验仍需单独检查。冻结包脚本、Matplotlib 字体资源与外部进程链路也分别记录，不与源码验收混为一项。
