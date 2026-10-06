# 构建与发布

在仓库根目录运行命令。私有维护记录与签名材料留在 `.local/maintainer/` 或外部安全位置，不纳入 Git。

## 发布前检查

```powershell
python scripts/release/check_release_version.py v1.12.2
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
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=1.12.2 /DBuildFlavor=CPU packaging\windows\BandScope.iss
```

NVIDIA 环境安装 `requirements-gpu-cu13.txt`，使用 `packaging/pyinstaller/ARPES_3dMAP_gpu.spec` 和 `/DBuildFlavor=NVIDIA`。CPU 与 NVIDIA 依赖建议使用独立虚拟环境。

PyInstaller 产物在 `dist/`，安装器与插件包在 `release/`。图标来源为 `assets/app.ico`。spec 以自身位置解析源码与资源，不依赖构建时的当前目录；安装器相对配置文件解析 `dist` 和 `release`。

验证构建可通过 `--workpath .local/build-validation --distpath .local/dist-validation` 使用独立输出；标准安装器默认仍读取根目录 `dist`。

## CI 与正式发布

- 普通提交和 PR 运行共享测试工作流；版本标签 `vX.Y.Z` 触发发布工作流。
- 发布先检查标签与源码版本一致，再运行完整测试，构建 CPU / NVIDIA 安装包和独立插件包。
- 插件任务按顺序执行：打包 → 校验签名密钥与内置公钥匹配 → 签名插件包 → 聚合上一版目录并生成 `plugins-index.json` → 签名目录。任一环节失败都不发布。构建脚本会打印本次针对的宿主版本与支持接口集合，兼容声明只覆盖真正验证过的组合；只改声明上限不算验证过。
- Windows 安装器签名脚本位于 `scripts/release/sign_windows.ps1`；所需 Secrets 沿用 `WINDOWS_SIGNING_PFX_BASE64`、`WINDOWS_SIGNING_PFX_PASSWORD`，时间戳变量为 `WINDOWS_TIMESTAMP_URL`。
- GitHub Release 包含两个安装器、插件包及对应 `.sha256`、`.bsplugin.sig`，以及目录 `plugins-index.json` 与 `.sig`。发布动作仍由维护者推送标签触发，本次整理不创建或推送标签。
- 历史目录用 `--merge` 带进来：同一 id/version 一旦发布就不能换成不同内容，脚本会直接拒绝；有签名 sidecar 的上一版目录会先验签再聚合。
