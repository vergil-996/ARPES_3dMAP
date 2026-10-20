# MatElab 更新源接入（可切换更新源）

- 日期：2026-10-16
- 状态：已实现并通过自动化回归；v1.14.0（2026-10-16）至 v1.15.0（2026-10-20）均已对真实服务器完成发布与下载校验。v1.16.0 的 GitHub Release 已完成并校验，MatElab 发布等待代理关闭；界面人工验收未执行。
- 负责范围：主程序更新模块与发布工具，由实施任务负责集成。
- 共享文件：`bandscope/updates/update_service.py`、`bandscope/updates/update_controller.py`、`bandscope/updates/net.py`、`bandscope/app/refactored_app.py`（版本按钮接线）、`bandscope/extensions/catalog.py`（仅确认耦合面，第一阶段不改行为）。
- 交付物：更新源抽象与 MatElab 源实现、切换入口与登录对话框、发布脚本、自动化测试、文档更新；完成后将本计划连同材料归档并更新计划索引。

## 目标

在现有 GitHub Release 更新源之外，新增 MatElab（物质科学电子实验室，实验记录本）更新源，用户可在软件内自主切换两个更新源。MatElab 源面向所内已有 MatElab 账号的用户；无账号用户继续使用 GitHub 源。

## MatElab API 依据

已登录官方帮助文档（`https://matelab.iphy.ac.cn/eln/#/doc`，「实验记录本 → 数据 API 接口」）实测读取，API 基址均为 `https://eln.iphy.ac.cn:61262`：

| 用途 | 接口 | 要点 |
| --- | --- | --- |
| 登录 | `POST /tokens` | 表单提交 username/password，返回 access token（约 60 分钟）与 refresh token（24 小时） |
| 刷新 | `POST /tokens/tokens_refresh` | 提交 refresh_token 换新 token，旧 refresh 失效 |
| 记录本列表 | `POST /eln_api/elns` | 返回记录本名称、sn、server 等 |
| 记录列表 | `POST /eln_api/items` | 按记录本名列出记录（id、标题、关键词、修改时间） |
| 数据导出 | `POST /eln_api/export` | 按关键词或 uids 取记录全文；文件字段为 `{hash, filename, size}`（sha256） |
| 文件下载 | `GET https://{记录本服务器}/eln_url/eln?token=…&eln=…&i=…&h=…&s=…&f=…` | 由导出数据拼接 URL |
| 文件上传 | `POST /eln_api/upload` | 分片上传（单片 <10M，上限 4GB），支持 sha256 完整性校验，返回 id/url/size/hash |

调用约定：API 请求带 `Authorization: Bearer {token}`；token 过期时接口返回 `'refresh'` 错误码。所有接口均无匿名访问途径，用户必须在 BandScope 内登录一次 MatElab 账号。

## 设计要点

1. **更新源抽象**：新增 `bandscope/updates/sources.py`，定义与 Qt 无关的 `UpdateSource` 协议（`check_for_update(current_version, flavor)`、`download_installer(asset, progress)`），复用现有 `ReleaseInfo`/`ReleaseAsset`/`UpdateCheckResult`。现有 `GitHubReleaseClient` 适配为 GitHub 源，行为不变。
2. **MatElab 源**（`bandscope/updates/matelab_source.py`）：
   - 发布约定：专用记录本（如「BandScope 发布」），每个 Release 一条记录，含发布说明（富文本）、按 `installer_asset_name()` 命名的安装包文件字段、JSON manifest 字段（版本号、各 flavor 的 sha256/size）。
   - 检查更新：登录 → `items`/`export` 拉取发布记录 → 按 semver 取最新 → 组装 `ReleaseInfo`（sha256 取上传时服务器返回的 hash）。
   - 下载：拼接 `eln_url` 下载 URL，仍走 `net.save_stream` 的大小 + SHA-256 校验与 2 GiB 上限。
   - Token 生命周期：内存缓存 access token；收到 `'refresh'` 自动刷新；refresh 过期重新登录。凭证首次使用时弹窗询问，存 QSettings（基本混淆并在界面明示），token 不写盘。
   - 主机白名单：`net.py` 的 `ensure_https_download_url` 增加 `allowed_hosts` 参数（默认仍为 GitHub 集合）；MatElab 源放行 `eln.iphy.ac.cn` 及记录 `server` 字段指明的服务器主机。
3. **切换入口**：QSettings 新增 `updates/source`（`github`/`matelab`，默认 `github`）；画布与版本按钮右键菜单统一使用「更新设置…」，可切换更新源、查看已保存账号、更换或清除 MatElab 账号。对话框只维护草稿，保存后统一生效；取消保留原设置。选 MatElab 但未设置账号时，首次手动检查更新会提示填写；切到 GitHub 保留 MatElab 账号。`UpdateController` 按当前源构造客户端，弹窗文案与失败提示按源区分；检查或下载期间禁止修改源与账号。
4. **插件目录**：第一阶段 `CatalogClient` 仍走 GitHub，取不到时沿用现有「无法确认」降级；第二阶段再评估把目录镜像到 MatElab 记录。
5. **发布工具**：新增 `scripts/release/publish_matelab.py`，登录 → 分片上传安装包 → 创建/更新发布记录（manifest + 说明）。

## 实施顺序

1. `net.py` 白名单参数化 + 更新源抽象（纯重构，现有测试须全绿）。
2. MatElab 源客户端与单元测试。
3. 控制器与右键菜单接入切换、登录对话框。
4. 发布脚本，用一个测试版本端到端验证。
5. 文档与计划归档。

## 验证与验收标准

- 新增 `tests/updates/test_matelab_source.py`，用假 opener 覆盖登录/刷新、记录解析、semver 选择、下载 URL 拼接、白名单拒绝；源切换与控制器逻辑沿用现有测试模式。
- 自动化测试使用合成数据与隔离设置，不依赖真实 MatElab 账号或网络；真实验证脚本放 `scripts/validation/`，凭证放 `.local/`（不提交）。
- 集成前运行完整回归：`python -m unittest discover -s tests -t . -v`。
- 人工验收：两源切换、MatElab 登录失败提示、断网降级、非 frozen 运行行为、真实下载校验通过。
- 交付前检查 `git status --short`，记录改动、验证结果及未执行检查的原因。

## 待实施期确认的点

- 记录 `server` 字段为课题组服务器时下载 host 的确定方式（先在主服务器验证）。
- NVIDIA 包体积是否低于 4GB 上传上限。
- 非 frozen 运行时 MatElab 源点击「更新」的行为（建议打开对应记录页）。

## 当前验收结果与下一步

- 功能实现：已完成（2026-10-16）。更新源抽象（`bandscope/updates/sources.py`）、MatElab 源客户端与分片上传（`bandscope/updates/matelab_source.py`）、控制器源切换与 MatElab 登录对话框（`bandscope/updates/update_controller.py`）、右键菜单「更新源」入口（`bandscope/app/refactored_app.py`）、发布脚本（`scripts/release/publish_matelab.py`）。
- 自动化测试：新增 `tests/updates/test_matelab_source.py`、`tests/updates/test_update_sources.py`；全量回归 1237 项通过（2026-10-16）。
- 连通性验证：已用真实 MatElab 会话做只读验证——记录本列表/记录列表接口可按预期解析，业务错误提示正常（2026-10-16）。
- 真实发布验证（2026-10-16，v1.14.0）：发布脚本链路对真实服务器跑通——两个安装包经分片接口上传，服务器组装后 sha256 与 CI 边车值一致（CPU `227d1efd…`，NVIDIA `0557c6d6…`）；发布记录「BandScope v1.14.0」（uid `bandscope-release-1.14.0`）建于「BandScope发布」记录本（id 5514），含 manifest / 安装包两个模块（2026-10-17 起去掉「发布说明」模块，说明文字并入 manifest 的 notes 字段）。客户端只读验证：从 1.13.0 检查更新，两个 flavor 均发现 1.14.0，文件名 / 大小 / SHA-256 与 CI 发布值完全一致。CPU 安装包经客户端 `download_installer` 完整下载并校验通过（145 MiB，sha256 一致）；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。
- 过程中修复：分片上传在文件大小恰为分片整数倍时末片判定错误（漏发整包 hash、返回 `None`），已修复并补回归测试；发布改用「创建空记录 + 数据更新」接口搭建记录，不再需要预先建模板；文件项文件名用 `#hash{filename,sha256}` 引用改写保证客户端可识别。
- 真实发布验证（2026-10-17，v1.14.1）：发布链路再次跑通——CPU 安装包（145 MiB）直传 134s 完成；NVIDIA 安装包（1143 MiB）慢网环境下用自写分步续传驱动 `scripts/release/publish_matelab_driver.py` 分 10 轮逐片追加（同一 uid 续传，state 存 `release/.matelab-publish-1.14.1.json`），服务器组装后整包 hash 校验通过。发布记录「BandScope v1.14.1」（uid `bandscope-release-1.14.1`）建于「BandScope发布」记录本，含 manifest / 安装包两个模块。客户端只读验证（脚本 `scripts/validation/verify_matelab_release.py`，按 APP_VERSION 参数化）三项全部通过：从 1.14.0 检查更新，两个 flavor 均发现 1.14.1，文件名 / 大小 / SHA-256 与 CI 发布值一致；CPU 安装包经客户端 `download_installer` 完整下载并校验通过（sha256 `f49342454b08bde5…` 与 CI 边车一致）；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。发布前全量回归 1239 项通过（2026-10-17）。
- 更新设置补齐（2026-10-17，随 v1.14.2 发布）：新增 `bandscope/updates/update_settings_dialog.py`，账号编辑与更新源统一由设置窗口提交，替换画布右键的旧「更新源」子菜单，并补上版本按钮右键入口。更换账号不预填密码，空用户名或密码在原窗口提示；保存后重建客户端并清除旧源的更新结果，取消不写设置。设置窗口打开期间不启动自动检查，检查与下载期间暂时禁止编辑。相关用例位于 `tests/updates/test_update_settings_dialog.py`、`tests/updates/test_update_sources.py` 和 `tests/integration/test_update_context_menu.py`。Windows 原生 Qt 平台、125% 缩放下的 GitHub / MatElab / 空账号 / 账号编辑与空值提示截图已复核，未发现文字遮挡（合成账号，不联网；本机材料 `.local/outputs/update-settings/`）。
- 本次验证（2026-10-17）：`.venv/Scripts/python.exe -m unittest discover -s tests -t . -v`，1257 项全部通过；覆盖正在运行与已结束但结果尚未处理的线程、设置期间自动检查阻断、同源换号、清除与取消。真实主窗口验证版本按钮左键检查与右键设置入口、设置弹窗打开与取消均通过（隔离设置与扩展目录，自动联网检查已替换为测试桩）。记录与截图在 `.local/outputs/update-settings/`；未使用真实账号，也未构建或发布新安装包。
- 真实发布验证（2026-10-17，v1.14.2）：CI 出包后先按 `.sha256` 边车校验本地下载（CPU `795e34efd185ab54…`，NVIDIA `b1bffa15a6970e61…`）。CPU 安装包（145 MiB）直传 51s 完成；NVIDIA 安装包（1143 MiB）用分步续传驱动 `scripts/release/publish_matelab_driver.py` 分 3 轮逐片追加（同一 uid 续传），服务器组装后整包 hash 校验通过。驱动已改为按 `APP_VERSION` 推导版本、文件名与状态文件，不再写死上一版。发布记录「BandScope v1.14.2」（uid `bandscope-release-1.14.2`）建于「BandScope发布」记录本，含 manifest / 安装包两个模块。客户端只读验证（`scripts/validation/verify_matelab_release.py`）三项全部通过：从 1.14.1 检查更新，两个 flavor 均发现 1.14.2，文件名 / 大小 / SHA-256 与 CI 发布值一致；CPU 安装包经客户端 `download_installer` 完整下载并校验通过；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。发布前全量回归 1257 项通过、插件发布链路 7 组检查通过（2026-10-17）。
- 真实发布验证（2026-10-17，v1.14.3）：CI 出包后按 `.sha256` 边车校验本地下载（CPU `256f1f299684b31a…`、NVIDIA `26f24ac00c7de258…`）。CPU 安装包（145.1 MiB）直传 82s 完成；NVIDIA 安装包（1143 MiB）本轮一次直传 505s 完成（未触发续传）。发布记录「BandScope v1.14.3」（uid `bandscope-release-1.14.3`）建于「BandScope发布」记录本，含 manifest / 安装包两个模块。客户端只读验证（`scripts/validation/verify_matelab_release.py`）三项全部通过：从 1.14.2 检查更新，两个 flavor 均发现 1.14.3，文件名 / 大小 / SHA-256 与 CI 发布值一致；CPU 安装包经客户端 `download_installer` 完整下载并校验通过；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。发布前全量回归 1266 项通过（2026-10-17）。
- 真实发布验证（2026-10-18，v1.14.4）：GitHub Release 发布后按 `.sha256` 边车校验本地下载（CPU `ad2f87428125a542…`，152,179,142 B；NVIDIA `231c471a7dfd990c…`，1,198,694,982 B）。MatElab 一次直传完成（未触发续传），发布记录「BandScope v1.14.4」（uid `bandscope-release-1.14.4`）建于「BandScope发布」记录本，含 manifest / 安装包两个模块。客户端只读验证（`scripts/validation/verify_matelab_release.py`）三项全部通过：从 1.14.3 检查更新，两个 flavor 均发现 1.14.4，文件名 / 大小 / SHA-256 与 CI 发布值一致；CPU 安装包经客户端 `download_installer` 完整下载并校验通过；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。发布前全量回归 1299 项通过（Matplotlib 3.10.8 与 3.11.2 各跑一遍）。
- 真实发布验证（2026-10-19，v1.14.5）：GitHub Release CI 全部成功后，按 `.sha256` 边车校验本地下载（CPU `3d6e2c607dba2b4b…`，152,209,371 B；NVIDIA `5945b0413de624ff…`，1,198,679,818 B）。MatElab 一次直传完成（CPU 145.2 MiB、NVIDIA 1143.2 MiB，未触发续传），发布记录「BandScope v1.14.5」（uid `bandscope-release-1.14.5`）建于「BandScope发布」记录本，含 manifest / 安装包两个模块。客户端只读验证（`scripts/validation/verify_matelab_release.py`）三项全部通过：从 1.14.4 检查更新，两个 flavor 均发现 1.14.5，文件名 / 大小 / SHA-256 与 CI 发布值一致；CPU 安装包经客户端 `download_installer` 完整下载并校验通过；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。发布前全量回归 1308 项通过、插件发布链路 7 组检查通过（2026-10-19）。
- 真实发布验证（2026-10-20，v1.15.0）：GitHub Release CI 全部成功后，按 `.sha256` 边车校验本地下载（CPU `c4a9021d…`，152,223,400 B；NVIDIA `4feedf17…`，1,198,767,061 B）；插件包 `flat_band_opacity 1.2.2`（CI `df70a756…`）与 `edc_waterfall 1.0.0`（`43ade902…`）验签通过。MatElab 网络本轮多次超时，CPU 用 `publish_matelab_driver.py CPU` 重试一次后 97.6s 完成；NVIDIA 用同一驱动的 `resume-nvidia` 分片续传（状态 `release/.matelab-publish-1.15.0.json`）多轮完成，服务器组装后 hash 与 CI 边车一致。发布记录「BandScope v1.15.0」（uid `bandscope-release-1.15.0`）建于「BandScope发布」记录本，含 manifest / 安装包两个模块。客户端只读验证（`verify_matelab_release.py`，以 `BANDSCOPE_PREVIOUS_VERSION=1.14.5` 从上一版检查）三项全部通过：两个 flavor 均发现 1.15.0，文件名 / 大小 / SHA-256 与 CI 一致；CPU 安装包经客户端完整下载并校验通过；NVIDIA 安装包头 / 中 / 尾三段 4 MiB 区间抽检与 CI 版逐字节一致。发布前全量回归 1323 项通过、插件发布链路 7 组检查通过（2026-10-20）。
- GitHub Release 验证（2026-10-20，v1.16.0）：CI 28m 24s 全部成功；按 `.sha256` 边车校验本地下载（CPU `6690ae4332e5656d…`，152,337,668 B；NVIDIA `b2f9d1da0d164d09…`，1,198,892,647 B）；插件包 `flat_band_opacity 1.2.2`（`df70a756…`）与 `edc_waterfall 1.0.0`（`43ade902…`）Ed25519 验签通过，`plugins-index.json` 验签通过（4 个版本条目）。MatElab 源尚未发布，等待维护者关闭代理；发布前全量回归 1385 项通过、插件发布链路 7 组检查通过（2026-10-20）。
- 未执行的检查：界面视觉与登录对话框人工验收、非 frozen 运行行为、下载后安装启动未执行，不能视为通过。
- 下一步：在做界面人工验收（两源切换、登录失败提示、断网降级）后归档本计划。
