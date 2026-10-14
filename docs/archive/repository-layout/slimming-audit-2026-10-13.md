# 项目瘦身审查报告（2026-10-13；2026-10-14 复核修订）

审查路径：`D:\ARPES_3dmap\ARPES_3dMAP`（工作区根）。原报告由另一 AI 编写，本次针对当前检出版本及可访问的本机材料复核并修订。

本次范围：目录内容、容量、Git 跟踪状态、引用、构建与签名流程、历史存档和模块职责。未进行完整代码死代码分析，也未全量比对构建文件内容。只修改本报告，不执行既有文件清理、源码迁移或忽略规则修改。

## 结论摘要

| 项目 | 复核结论 |
| --- | --- |
| 原报“12.46 GB / 86,071 文件” | 缺少完整清单、单位及排除项口径；`.venv/` 原报未统计，部分目录不可读，不能作为完整仓库总量 |
| 原报“可释放 9.12 GB / 73%” | 撤回确定性结论：有重复计数、历史证据及保留需求未确认，单位也不一致 |
| 四个构建目录 | 复核文件逻辑长度合计 **8,933,995,072 字节（8.32 GiB）**；可优先评估，尚不能视为可释放量 |
| 字节码候选 | 根缓存整体与源码目录根外孤儿字节码去重后 **3.53 MiB**，不再重复累计发现 7、8、14 |
| 观察项 | 保留原编号 1–20；其中包含保留项、待确认项和重叠目录，不等于 20 个删除候选 |
| 本次改动 | 仅本报告；未删除既有项目文件 |
| 全量回归 | 正常权限、隔离临时目录下 **1213 项通过**（Python 3.12.10）；首次沙箱运行受阻，见下文 |
| Git 状态 | 开始复核时本报告为既有未跟踪文件；两个历史临时目录读取受限，不能声称扫描完整或工作区干净 |

本报告使用 MiB/GiB 表示二进制单位（1 MiB = 1,048,576 字节，1 GiB = 1,073,741,824 字节）；容量是文件逻辑长度之和，不等同于磁盘实际可释放空间。表中标为“原报”的数值仅保留历史扫描陈述，不混入本次已核实总量。

**处置记录区分：**原报告自称“仅报告、用户确认不删除”，同时记载删除了其测试生成的 6 个 `tmp*` 目录。未附这些目录清单及操作日志，本次无法核实该执行记录或原会话授权，不把它写成本轮已执行动作。

**测试证据区分：**原作者报告过一次 1213 项运行中
`tests.extensions.test_plugin_store.LockAndLeaseTests.test_two_processes_do_not_lose_updates`
出现 `PermissionError`。原审查前后日志及环境未附，不能仅凭该异常确定为文件锁问题、证明审查前后均失败或排除其他原因。现存
`.local/logs/enhancement_crop_parity_full.log`、
`.local/logs/crop_mpl_compat_full.log`、
`.local/logs/enhancement_always_enabled_full.log`
均记录 1213 项通过，且该用例通过。

## 保留范围与扫描限制

| 位置 | 处理原则 |
| --- | --- |
| `.venv/`、`.git/` | 不列清理候选；本次未递归统计。`.git/` 包含历史与未推送状态，不能当缓存重建 |
| `PyQt-SiliconUI-main/` | 本机依赖检出目录，本次未核实内部；原报“7 个空目录”不作为清理依据 |
| `.local/data/` | 保留实验数据与参考材料；默认验证数据 `WSe2_step.npz` 为仓库约定保留项 |
| `docs/archive/` 及被交接引用的本机证据 | 保留历史记录、原始附件、修复前对照与未完成验收证据 |
| 两个 `bandscope-plugin-*` 目录 | 当前拒绝访问，内容及容量未知，不能判为空目录 |
| `.pytest_cache/crop_tmp/tmpq2754j7m` 与部分 `.local/outputs/` 子目录 | 本次列举出现访问错误；可访问文件的统计不是完整目录容量 |

读取不到内容应记为扫描盲区；不能将“读取失败”“Git 未列出”“未被文本引用”直接解释为“空目录”或“可安全删除”。

## 发现清单

### A 类：构建目录与诊断环境（有条件的评估对象）

**1. `dist/` — 4050.79 MiB / 22,052 文件**

本次核实 4,247,557,395 字节。被 `.gitignore` 忽略，未被 Git 跟踪，含 CPU 与 NVIDIA 冻结构建。
目录时间戳只能辅助定位，不能证明源码、依赖、工具链或构建批次相同。

当前源码的 CPU 构建命令：
`python -m PyInstaller --noconfirm --clean packaging/pyinstaller/ARPES_3dMAP.spec`。
NVIDIA 版使用 `ARPES_3dMAP_gpu.spec` 及对应依赖环境，详见 `docs/development/releasing.md`。
这些命令构建当前源码，不保证恢复历史冻结包的相同字节。本次未执行重建。

处理建议：确认不再需要旧版启动、冻结包回归或安装验收后评估清理；风险中。

**2. `.local/dist-validation/` — 4055.22 MiB / 22,140 文件**

本次核实 4,252,201,453 字节。目录迁移验收记录
`docs/archive/repository-layout/validation-2026-09-27.md` 说明其历史用途。

与 `dist/` 按相对路径比较，CPU 共有 9,747 个文件，NVIDIA 共有 12,305 个文件；
各自主程序 EXE 的大小及 SHA-256 均不同，其余共有文件本次仅确认大小相同，未全量核对内容。
验证目录每种构建各多 44 个运行库文件，两种构建合计多 **88 个**，还包含 `ucrtbase.dll`。

原报“9,747 个共有文件 MD5 完全一致（抽样 40 个）”不能成立：9,747 只对应 CPU，抽样也不能证明全量一致。
它们是不同构建快照，不能归为“完全重复、无判断成分”的副本。

构建当前验证输出需在发现 1 的命令后追加：
`--workpath .local/build-validation --distpath .local/dist-validation`。
历史快照恢复还需当时源码、依赖与工具链。

处理建议：确认历史验收及冻结包回归保留需求后评估；风险中。

**3. `build/` — 207.36 MiB / 32 文件**

本次核实 217,428,021 字节，为两个 PyInstaller 构建的中间产物，已忽略且未跟踪。
当前源码重建可生成新的中间产物，不保证复原旧构建记录。

处理建议：确认没有正在使用的构建任务，也不需留存诊断信息后可清理；风险低。

**4. `.local/build-validation/` — 206.76 MiB / 32 文件**

本次核实 216,808,203 字节。与 `build/` 的目录结构、文件数相同不能证明内容相同；
它是历史验收构建的中间产物。重建当前验证输出须使用发现 2 的独立输出参数。

处理建议：确认不再需要旧构建诊断与验收上下文后可清理；风险低。

**5. `.local/venv-ci-probe/` — 469.09 MiB / 7,771 文件**

本次核实 491,876,755 字节。`docs/handoffs/ci-no-opengl-2026-09-27.md` 记录其 CI 无 OpenGL 复现用途，
`docs/archive/page-tree/page-tree-bulk-delete.md` 也记录其回归与窗口验收用途，不能称为无用途记录的环境。

`pyvenv.cfg` 记录 Python 3.12.10 与 `--system-site-packages`，目录中有 `_project_venv.pth`，
并非完全独立环境。恢复需结合这些配置及对应记录，仅安装当前 `requirements.txt` 不能保证等价复现。

处理建议：确认当前诊断与验证已不再依赖它后评估；风险中。

### B 类：字节码、混合临时材料与验收证据

**6. `.pytest_cache/` — 可访问部分 2.77 MiB / 64 文件**

目录混有手写脚本、日志、数据和截图，并非全部为 pytest 自身缓存。发现
`crop_smoke_ext.py`、`panel_smoke.py`、`crop_smoke.py`、`layout_check.py`、`vtk_key_probe.py`
五份脚本；`scripts/validation/` 中没有同名替代，现有验收是否完整覆盖这些检查尚未逐项比较。

`panel_smoke.py` 的 `Path(__file__).resolve().parents[1]` 在当前位置恰好指向仓库根，
不能据此推断脚本原来位于根目录或路径已失效。部分脚本确有旧根模块导入与旧业务流程，需要单独处理。

pytest 只能重建自己的缓存，不能重建手写脚本或历史证据；一个子目录拒绝访问。
处理建议：先分类，需要保留的脚本再按职责迁入 `scripts/diagnostics/` 或连同记录归档；风险中，本次不迁移。

**7. `__pycache__/`（仓库根）— 2.48 MiB / 65 文件**

本次核实 2,602,046 字节：标准 `.pyc` 56 个，其中旧路径源码缺失的孤儿缓存 51 个；
另有 9 个 `*.pyc.<随机数>` 写入残留。不能称 65 个文件全部是孤儿字节码。

源码仍存在的缓存可在导入时重建；旧路径源码已迁移或移除的缓存不会在原位置自动重建。
`python -m compileall -q bandscope plugins scripts tests start.py` 仅为当前源码预热，不恢复旧路径缓存。

处理建议：在应用及解释器相关任务停止后，可将这些文件作为低风险缓存候选；与发现 8 去重。

**8. 源码目录孤儿 `.pyc` — 共 128 个 / 2.50 MiB，已含根部 51 个**

本次排除 `.git/`、`.venv/`、`.local/`、`dist/`、`build/`、`PyQt-SiliconUI-main/`，
按缓存文件名对应的源码路径复算到 128 个标准孤儿 `.pyc`，共 2,618,121 字节。
这是可访问源码区域的统计，不是完整工作区扫描。

| 分布 | 数量 |
| --- | ---: |
| 根 `__pycache__/` | 51 |
| `tests/` | 54 |
| `scripts/` | 12 |
| `plugins/` | 10 |
| `bandscope/` | 1 |

原报“2.48 MB 之外另有 128 个”存在重复计数；根外新增仅 **77 个 / 1.05 MiB**。
发现 7 的根缓存整体加上根外孤儿，去重合计 **3,697,993 字节（3.53 MiB）**。

多数旧路径对应目录迁移，但不能据缓存名断定所有功能均已迁移：
`_verify_settled_grab.py`、`probe_frame_ranges.py` 的新位置在 `scripts/diagnostics/`；
`scripts/validation/verify_band_overlay.py` 当前不存在，保存在能带重构存档提交中。
`surface_overlay` 及 `band_reconstruct` 的相关源码与配套材料也见发现 14。

处理建议：缓存可作为低风险候选；不要把字节码当可靠源码备份。发现 14 的 10 个文件已计入此项。

**9. `.local/outputs/` — 原报 113.91 MB / 597 文件，非完整可释放量**

混合存放生成结果、历史对照、未完成验收证据、旧缓存和临时运行目录；部分子目录拒绝访问。
`docs/architecture/migration-map.md` 记录 `legacy-caches/`；
`docs/handoffs/e-axis-consistency-acceptance.md` 引用截图、JSON 及 `curve_arrays.npz`；
`docs/archive/ui/ui-beautification-2026-10-07.md` 引用修复前截图，不能由当前源码直接恢复。

重跑现版轴标题验收的示例为：
`python scripts/validation/verify_axis_titles.py .local/data/WSe2_step.npz`。
需要输入数据及图形环境，重跑也不保证复现历史结果。

处理建议：逐项核实引用、源码版本、原始数据、依赖与重建条件；
保留仍被交接引用或无法等价恢复的证据。**不按 2026-09-27 或其他日期划线删除。**
发现 13 已包含于本目录，不再次累计容量；风险中，待分类。

**10. `.local/logs/` — 原报 9.07 MB / 119 文件**

除 smoke 日志外，还含历史回归、环境故障、发布及验收记录。
`docs/archive/time-preview/time-preview-consistency.md`、
`docs/archive/crop/interactive-crop-box.md`、
`docs/archive/local-enhancement/always-enabled.md` 等均引用本机日志。

被 `.gitignore` 忽略只表示不提交；重跑生成的是新的执行记录，不能恢复原时刻的通过、失败及环境证据。
本轮测试也新增了日志，因此原数值不能当当前完整容量。

处理建议：保留引用中的证据；未引用且用途确认结束的普通运行日志另行评估，不能整体定为低风险垃圾。

**11. 三个发布预演目录 — 合计 0.16 MiB / 25 文件**

`.local/release_rehearsal/`、`.local/rehearsal_1_12_3/`、`.local/release_verify_1_12_3/`
本次核实合计 170,530 字节，包含历史插件包、目录与签名等材料。
本地有 `v1.12.3` 标签，后续发布交接引用其线上摘要；本次未联网核验发布状态。

后续版本存在不能证明这些材料过期或无保留价值。
处理建议：确认旧目录聚合、摘要比对及发布追溯是否仍需要它们；当前预演流程不能复原历史发布件。风险中，待确认。

### C 类：历史插件构建及发布材料（保留需求待确认）

**12. `release/` — 0.11 MiB / 9 文件**

本次核实 112,347 字节，已忽略、未跟踪，包含以下历史版本及其摘要／签名：

| 插件包 | 与当前检出版本的关系 |
| --- | --- |
| `BandScope-flat_band_opacity-1.0.0.bsplugin` | 当前源码版本为 1.2.2 |
| `BandScope-flat_band_opacity-1.2.0.bsplugin` | 当前源码版本为 1.2.2，目录另有签名件 |
| `BandScope-band_reconstruct-1.0.0.bsplugin` | 当前主分支未包含插件源码；存在存档分支 |
| `BandScope-band_reconstruct-1.1.0.bsplugin` | 同上，尚未核对包与存档的版本对应 |

版本落后或当前源码不在主分支，不能直接判为“失效”。旧包可能用于受支持宿主、故障追溯或恢复。
同一插件 id/version 的已发布内容不可替换，见 `docs/development/plugins.md`。

`python scripts/release/build_plugin.py flat_band_opacity --output-dir release`
只构建当前源码包与摘要，不签名，也不恢复旧版本的字节。
签名由 `scripts/release/sign_plugin.py` 另行完成；发布文档记载本地受控私钥副本及 CI Secrets 的使用方式。
`.local/tools/secret-venv` 是工具环境，不能写成密钥来源。本次未读取私钥或 Secrets、未验签或验证旧包安装兼容性。

处理建议：先确认版本来源、完整备份、签名及保留用途；风险中，不能按旧版本号直接删除。

**13. `.local/outputs/plugin_build/` — 4 文件 / 54,880 字节**

包含 `band_reconstruct-1.0.0` 与 `flat_band_opacity-1.0.1` 的包及摘要，已计入发现 9。
其中 `band_reconstruct-1.0.0` 与 `release/` 同版本包的 SHA-256 **不同**，不能当重复副本删除。

当前脚本默认输出到 `release/`，构建此输出位置须显式指定
`--output-dir .local/outputs/plugin_build`；`band_reconstruct` 缺少当前源码，不能直接套用重建命令。

处理建议：核对包内差异、来源及恢复需要后再分类；风险中，待确认。

### D 类：源码残留、包标记与待确认占位

**14. `plugins/band_reconstruct/` — 仅 10 个字节码缓存**

当前目录没有 `plugin.json`、`__init__.py` 或 `entry.py` 源码，只剩 `__pycache__/`。
这些缓存已计入发现 8，不另算空间。

`git log --all` 找到存档提交 **`35aaaeb`**，标题为
“WIP: 能带重构插件（阶段0-3，已验收）——从 main 回滚前存档”；
本地 `band-reconstruct-wip` 与本地远端跟踪引用 `origin/band-reconstruct-wip` 均包含它。
提交树保存插件源码、宿主叠加/API 配套改动、测试、计划、阶段交接及截图。
当前主分支文档检索无命中不能推导为“移除未留下记录”；本次未联网确认远端现状。

处理建议：此处缓存可清理；源码恢复以存档提交为依据，并核对宿主配套。
`.bsplugin` 只包含打包的插件部分，不能代替宿主改动、测试与验收记录；
字节码反编译不保证可靠恢复，因此不能据此降低历史包的保留要求。

**15. `scripts/diagnostics/audit_dead_code.py` — 0 字节，已跟踪**

确为空文件，当前未检索到调用引用。可能是占位，但无法据文件名判断来源或创建失败。
处理建议：用途待确认；体积为零，不列空间清理候选。删除会改变版本控制内容。

**16. `tests/plugins/edc_waterfall/__init__.py` — 0 字节，已跟踪**

**保留，非垃圾。** 空文件仍使目录成为常规 Python 包，支持当前
`unittest discover -s tests -t .` 的发现方式。
其他测试包的 `__init__.py` 只有文档字符串，是否有正文不影响包标记作用；与有无 `setup.py` 无关。

**17. `.dsh/skills/dead-code-slimming/SKILL.md` — 0 字节，已跟踪**

工具配置占位，用途待确认；不能因“非项目功能源码”判为低风险可删。
处理建议：确认维护者意图后再决定补全或移除；不列空间清理候选。

**18. 根级 `bandscope-plugin-alr0ttwr/`、`bandscope-plugin-zh6fqihw/` — 内容未知**

本次列举及 `git status` 均出现拒绝访问，不能沿用原报“两空目录”结论。
`bandscope/extensions/plugin_dialog.py` 的真实插件下载流程也使用
`tempfile.mkdtemp(prefix="bandscope-plugin-")`，目录名不能唯一证明来源是测试；
它们可能包含下载包或其他材料。

处理建议：先在可读权限环境核对来源、内容、占用与清理失败原因；暂不列清理候选。
Git 不跟踪空目录这一规则不能解释当前访问错误。

### E 类：保留资料

**19. `docs/images/main-3d.png` — 50,498 字节，已跟踪**

排除本报告自引用后，在 README、docs、scripts、bandscope、plugins、.github 的文本检索中未发现引用。
这仅说明当前检索范围没有直接引用，不能证明无用途；原始截图来源与历史展示价值未确认。

处理建议：保留。收益不足以支撑删除原始资料。

**20. `docs/archive/` — 历史记录及附件**

`docs/archive/README.md` 明确保留原始内容与附件、不覆盖历史验收证据。
本次改写前目录统计为 **97 个文件 / 31 个 Markdown / 20,285,447 字节（19.35 MiB）**，包含本报告；
原报“28 个 Markdown、约 4 MB”不是当前统计。

处理建议：保留，不按“记录密度”或文件日期列为垃圾。
现有归档状态不代表相关视觉验收、冻结包验收或待办均已完成；当前状态以交接索引为准。

## 级联影响与后续建议

1. **Git 状态不能代替删除审查。** 忽略／未跟踪只说明版本控制状态，不证明内容可再生；
   发现 15、16、17、19 是已跟踪文件，发现 9、10、12、13 含不由 Git 保存的历史材料。
   容量汇总应先按路径去重，明确单位、排除项、访问错误与保留条件。
2. **临时目录应定位来源与清理问题。** 不采用原报的宽泛 `/tmp*/` 忽略建议。
   临时根可能受环境或运行器配置影响；验收运行可将 `TMP/TEMP` 指向专属 `.local/` 子目录，
   先处理权限或清理失败。新增忽略规则不能修复残留，也会掩盖误放文件。
3. **目录迁移引用需按实际职责核对。** 旧脚本有些移至 `scripts/diagnostics/`，有些留在存档分支，
   不能统一写为均已迁至 release/validation。本次未删除配置、脚本或资源，不作“删除后无级联影响”的保证。
4. **数据保留。** `.local/data/` 本次可读文件总计 726,046,583 字节（692.41 MiB），
   包含默认验证数据 `WSe2_step.npz` 及参考 PDF；不纳入清理量。
5. **三个同名增强模块不列冗余候选。**
   `bandscope/core/local_enhancement.py` 负责区域模型、投影和坐标切片；
   `bandscope/processing/local_enhancement.py` 负责统计与颜色／透明度数值映射；
   `bandscope/rendering/local_enhancement.py` 负责 2D 图层、VTK 通道及图例接入。
   processing 依赖 core，rendering 依赖两者，应用集成与导出共用这些接口，分层明确。
6. **后续若另行清理：**先逐项确认构建快照和诊断环境的保留需求，再分类混合输出及历史包；
   如需方便主分支查阅能带重构记录，可补存档分支与提交定位说明，无需重建已有阶段文档。
   本报告不直接授权任何清理操作。

## 本次修订与验证记录（2026-10-14）

- 修改范围：仅本报告；交付物为修订后的判断、证据口径与保留条件。本轮新增回归日志放在 `.local/`，不提交。
- 收尾：确认路径、非链接且目录为空后，仅移除本轮首次测试生成的 `bandscope-tests-ba0yfop1/`；两个既有插件临时目录未处理。
- 完成：文件／目录只读核对、指定目录容量统计、Git 跟踪与存档检查、构建文件及插件包差异核对、引用和模块分层检查。
- 全量回归命令：`.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`。
  首次沙箱运行在测试包初始化时出现 QtCore DLL 加载失败，并出现临时目录清理 `PermissionError`，
  未运行完整套件；日志为 `.local/logs/slimming-audit-review-2026-10-14-unittest.log`。
  正常权限环境中将 `TMP/TEMP` 指向专属 `.local/outputs/slimming-audit-review-temp-2026-10-14/` 后复核，
  **1213 项通过，退出码 0**；日志为 `.local/logs/slimming-audit-review-2026-10-14-unittest-retry.log`。
- 文档检查：修订差异、空白错误、文件引用及最终 Git 状态核对。
- 未执行：全工作区完整容量扫描、构建文件全量哈希、历史构建重建、旧包验签与安装兼容性验证、
  真实窗口／人工视觉验收、任何既有文件清理。原因：本任务为报告审阅；上述结论均未写成已通过。
