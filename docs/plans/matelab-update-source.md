# MatElab 更新源接入（可切换更新源）

- 日期：2026-10-16
- 状态：已实现并通过自动化回归；v1.14.0 已对真实服务器完成发布与下载校验（2026-10-16）；界面人工验收未执行。
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
3. **切换入口**：QSettings 新增 `updates/source`（`github`/`matelab`，默认 `github`）；版本按钮右键菜单加「更新源」子项切换；选 MatElab 且无凭证时弹出登录对话框。`UpdateController` 按当前源构造客户端，弹窗文案与失败提示按源区分。
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
- 真实发布验证（2026-10-16，v1.14.0）：发布脚本链路对真实服务器跑通——两个安装包经分片接口上传，服务器组装后 sha256 与 CI 边车值一致（CPU `227d1efd…`，NVIDIA `0557c6d6…`）；发布记录「BandScope v1.14.0」（uid `bandscope-release-1.14.0`）建于「BandScope发布」记录本（id 5514），含发布说明 / manifest / 安装包三模块。客户端只读验证：从 1.13.0 检查更新，两个 flavor 均发现 1.14.0，文件名 / 大小 / SHA-256 与 CI 发布值完全一致。CPU 安装包经客户端 `download_installer` 完整下载并校验通过（145 MiB，sha256 一致）；NVIDIA 安装包做头 / 中 / 尾三段 4 MiB 区间抽检，与 CI 版逐字节一致。
- 过程中修复：分片上传在文件大小恰为分片整数倍时末片判定错误（漏发整包 hash、返回 `None`），已修复并补回归测试；发布改用「创建空记录 + 数据更新」接口搭建记录，不再需要预先建模板；文件项文件名用 `#hash{filename,sha256}` 引用改写保证客户端可识别。
- 未执行的检查：界面视觉与登录对话框人工验收、非 frozen 运行行为、下载后安装启动未执行，不能视为通过（执行环境无 MatElab 账号密码，发布与验证用浏览器会话 token 注入完成）。
- 下一步：在做界面人工验收（两源切换、登录失败提示、断网降级）后归档本计划。
