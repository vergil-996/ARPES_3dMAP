# 构建与发布

在仓库根目录运行命令。私有维护记录与签名材料留在 `.local/maintainer/` 或外部安全位置，不纳入 Git。

## 发布前检查

```powershell
python scripts/release/check_release_version.py v1.17.1
python -m unittest discover -s tests -t . -v
python scripts/validation/verify_plugin_release_flow.py
python scripts/release/build_plugin.py flat_band_opacity --output-dir release
```

版本号示例应与 `bandscope/app_metadata.py` 一致。人工视觉验收见 [测试指南](testing.md)，不要将自动化通过等同于全部界面验收完成。

## 插件签名密钥

官方插件包与插件目录都用 Ed25519 签名，客户端只用宿主内置公钥验签。

1. 生成密钥对（只做一次；私钥不要提交，放 `.local/maintainer/` 或发布环境 Secret）：

   ```powershell
   python scripts/release/generate_plugin_key.py --key-id bandscope-official-2026 ^
       --output .local/maintainer/plugin-signing-key.pem
   ```

2. 把脚本打印的公钥条目填进 `bandscope/extensions/trust.py` 的 `TRUSTED_PLUGIN_KEYS`，随宿主一起发布。
3. 在仓库 Secrets 里配置 `PLUGIN_SIGNING_KEY`（Base64 的 32 字节私钥种子，或 PKCS#8 PEM）与 `PLUGIN_SIGNING_KEY_ID`。
4. 验证：`python scripts/release/sign_plugin.py --check-only`。这一步会同时确认“用来签名的私钥”与“仓库内置的公钥”是同一对；对不上就失败，不会先发一个客户端必然拒收的包。

**当前状态**：内置公钥 `bandscope-official-2026` 已随 v1.12.2 发布，仓库 Secrets（`PLUGIN_SIGNING_KEY`、`PLUGIN_SIGNING_KEY_ID`）已配置，私钥的受控副本在 `.local/maintainer/plugin-signing-key.pem`（不提交）。发布工作流仍会在「Check the plugin signing key」一步核对私钥与内置公钥是同一对，对不上就失败——这是刻意的门禁（正式发布必须签名），不是可以跳过的检查。

密钥轮换：新公钥要随宿主更新部署后才能签新包；旧公钥按明确的支持期保留在 `TRUSTED_PLUGIN_KEYS` 里，目录不能自行添加可信公钥。私钥丢失后无法再签出客户端认可的包，只能换新密钥并等宿主更新到位。

## Windows 构建

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm packaging/pyinstaller/ARPES_3dMAP.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=1.17.1 /DBuildFlavor=CPU packaging\windows\BandScope.iss
```

NVIDIA 环境安装 `requirements-gpu-cu13.txt`，使用 `packaging/pyinstaller/ARPES_3dMAP_gpu.spec` 和 `/DBuildFlavor=NVIDIA`。CPU 与 NVIDIA 依赖建议使用独立虚拟环境。

PyInstaller 产物在 `dist/`，安装器与插件包在 `release/`。图标来源为 `assets/app.ico`。spec 以自身位置解析源码与资源，不依赖构建时的当前目录；安装器相对配置文件解析 `dist` 和 `release`。

验证构建可通过 `--workpath .local/build-validation --distpath .local/dist-validation` 使用独立输出；标准安装器默认仍读取根目录 `dist`。

**本地 `release/` 清理（2026-10-20 约定）**：发布结束后只保留历次上传状态/审计记录 `.matelab-publish-*.json`；安装包、`.sha256`、`.bsplugin`、`.sig`、`plugins-index.json`、`release-notes-*.txt` 等其余产物都可以删除。正式产物在 GitHub Release，CI 可重新下载，不要用 `release/` 充当长期归档；该目录本身也不提交。

## 发布环境与代理切换

GitHub 与 MatElab 的网络路径相反，发布流程必须按顺序等待维护者切换代理，不能假定一次配置能同时完成两步。

- **GitHub（代理开启）**：本机通过代理访问 GitHub；系统代理写在
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`（实测 `127.0.0.1:7897`），
  但 `git` 不读取系统代理，推送分支与标签要显式传：

  ```powershell
  git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin main
  git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push origin vX.Y.Z
  ```

  未带代理时实测报 `Failed to connect to github.com port 443`。
- **MatElab（代理关闭）**：`eln.iphy.ac.cn:61262` 是所内直连，代理开启时连不上。
  GitHub Release 与安装包校验完成后必须停下，**等待维护者关闭代理**，再执行发布脚本；
  不能在代理仍开启时反复重试，也不要自行切换维护者的代理。
- **收尾提交（代理再次开启）**：MatElab 校验完成后通常还有计划/交接文档要提交；
  推送需要维护者重新开启代理。代理仍关闭时保留本地提交并明确告知「待补推」，
  不得写成已推送——v1.14.4 发布时就是这种状态。
- **状态检查**：`gh` CLI 在本机未安装；CI 进度与 Release 资产可用网页
  （`/actions/runs/<id>`、`/releases/expanded_assets/vX.Y.Z`）检查。
  匿名 GitHub API 常被共享出口 IP 限流，不要依赖轮询 API 判断构建结果。
- **MatElab 凭证**：`publish_matelab.py` 依次取命令行 `--username/--password`、
  环境变量 `MATELAB_USER`/`MATELAB_PASSWORD`，最后才交互输入；也可以只读使用应用
  「更新设置」里已保存的账号（registry `updates/matelab_username` /
  `updates/matelab_password_b64`）。任何情况下都不把密码写进仓库、日志或提交。

## CI 与正式发布

- 普通提交和 PR 运行共享测试工作流；版本标签 `vX.Y.Z` 触发发布工作流。
- 发布先检查标签与源码版本一致，再运行完整测试，构建 CPU / NVIDIA 安装包和独立插件包。
- 插件任务按顺序执行：打包 → 校验签名密钥与内置公钥匹配 → 签名插件包 → 聚合上一版目录并生成 `plugins-index.json` → 签名目录。任一环节失败都不发布。构建脚本会打印本次针对的宿主版本与支持接口集合，兼容声明只覆盖真正验证过的组合；只改声明上限不算验证过。
- 打包**必须可复现**：`.bsplugin` 的 zip 条目使用固定时间戳，摘要只由文件内容决定。历史版本沿用源文件 mtime，CI 的 checkout 又把它设成运行时刻，同一份源码每次发布会得到不同摘要，聚合上一版目录时会被「同 id/version 内容必须一致」判成内容被替换而失败——发生在标签推上去之后，只能再发一个补丁版本。插件源码没变时不要顺手改包内文件的编码或行尾，行尾差异同样会改变摘要。
- **本机与线上的摘要口径**（v1.13.0 实测）：`.bsplugin` 的压缩字节取决于打包环境的 zlib/Python 版本。同一份源码，CI（Python 3.12，CRLF checkout）与本机（Python 3.14）打出的包**逐条目内容一致**（CRC、解压尺寸、行尾、条目顺序相同），但压缩后字节不同，sha256 因此不同。跨版本聚合依赖的是 CI 每次重建摘要稳定（例如 `flat_band_opacity 1.2.2` 在 v1.12.5 与 v1.13.0 两次 CI 打包中摘要相同，聚合通过）；本机重建的摘要**不能**与线上目录逐字比对，只能用来核对内容（条目 CRC/大小）与本地回归。
- Windows 安装器签名脚本位于 `scripts/release/sign_windows.ps1`；所需 Secrets 沿用 `WINDOWS_SIGNING_PFX_BASE64`、`WINDOWS_SIGNING_PFX_PASSWORD`，时间戳变量为 `WINDOWS_TIMESTAMP_URL`。
- GitHub Release 包含两个安装器、插件包及对应 `.sha256`、`.bsplugin.sig`，以及目录 `plugins-index.json` 与 `.sig`。发布动作仍由维护者推送标签触发，本次整理不创建或推送标签。
- 历史目录用 `--merge` 带进来：同一 id/version 一旦发布就不能换成不同内容，脚本会直接拒绝；有签名 sidecar 的上一版目录会先验签再聚合。

## MatElab 更新源发布

除 GitHub 外，正式版还可发布到 MatElab（物质科学电子实验室）更新源，供所内用户在软件内切换使用。发布约定与客户端解析逻辑见 `bandscope/updates/matelab_source.py` 顶部 docstring。

**发布前确认系统代理已关闭**（见「发布环境与代理切换」）；代理开启时该地址不可达，脚本会在登录或上传阶段失败。

**完成判定（2026-10-20 约定）**：安装包上传完成、发布记录创建成功后，MatElab 发布即视为结束，不再从服务器下载回来复验。上传前的本地校验仍按第 3 步使用 CI `.sha256` 边车完成；上传脚本会核对服务器回传的整包 hash。GitHub 源仍按客户端逻辑做发布后只读验证。

一次性准备：在 MatElab 上创建发布记录本（默认 `BandScope发布`）。发布脚本通过「创建空记录 + 数据更新」接口直接搭建记录结构（「manifest」「安装包」两个模块），不需要预先建模板。

发布命令：

```powershell
python scripts/release/publish_matelab.py --version 1.17.1 `
    --installer CPU=release/BandScope-1.17.1-Windows-x64-CPU-Setup.exe `
    --installer NVIDIA=release/BandScope-1.17.1-Windows-x64-NVIDIA-Setup.exe `
    --notes release-notes.txt
```

凭证取 `--username/--password`、`MATELAB_USER`/`MATELAB_PASSWORD` 环境变量或交互输入。安装包按官方分片接口上传（单片 8 MiB，带整包 SHA-256），随后以 `#file{name}` 引用创建发布记录；同一版本重复发布会因记录 uid 冲突被拒绝，需先在网页端删除旧记录。

实测注意（2026-10-16 首次实发）：

- 文件项的 `filename` 取上传时 multipart 里的文件名；若传错，可用 `eln_api/update` 的 `modify` 命令以 `#hash{文件名,sha256}` 引用改写，无需重传。
- `delModule` 命令的 `name` 是字符串（官方文档误写为数组）。
- 客户端按记录标题或关键词识别发布记录，标题固定为 `BandScope vX.Y.Z`。

用户侧：在画布或顶部版本按钮上右键，打开「更新设置…」选择 MatElab；同一窗口可设置、更换或清除本机账号，点击「保存」后统一生效，取消保留原设置。未设置账号时，首次手动检查更新会提示填写；切换到 GitHub 会保留已保存的 MatElab 账号。凭证仅存本机（密码基本混淆），token 不落盘；检查或下载更新期间不能修改更新源和账号。插件目录仍固定来自 GitHub Release。
