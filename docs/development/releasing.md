# 构建与发布

在仓库根目录运行命令。私有维护记录与签名材料留在 `.local/maintainer/` 或外部安全位置，不纳入 Git。

## 发布前检查

```powershell
python scripts/release/check_release_version.py v1.9.0
python -m unittest discover -s tests -t . -v
python scripts/release/build_plugin.py flat_band_opacity --output-dir release
```

版本号示例应与 `bandscope/app_metadata.py` 一致。人工视觉验收见 [测试指南](testing.md)，不要将自动化通过等同于全部界面验收完成。

## Windows 构建

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm packaging/pyinstaller/ARPES_3dMAP.spec
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=1.9.0 /DBuildFlavor=CPU packaging\windows\BandScope.iss
```

NVIDIA 环境安装 `requirements-gpu-cu13.txt`，使用 `packaging/pyinstaller/ARPES_3dMAP_gpu.spec` 和 `/DBuildFlavor=NVIDIA`。CPU 与 NVIDIA 依赖建议使用独立虚拟环境。

PyInstaller 产物在 `dist/`，安装器与插件包在 `release/`。图标来源为 `assets/app.ico`。spec 以自身位置解析源码与资源，不依赖构建时的当前目录；安装器相对配置文件解析 `dist` 和 `release`。

验证构建可通过 `--workpath .local/build-validation --distpath .local/dist-validation` 使用独立输出；标准安装器默认仍读取根目录 `dist`。

## CI 与正式发布

- 普通提交和 PR 运行共享测试工作流；版本标签 `vX.Y.Z` 触发发布工作流。
- 发布先检查标签与源码版本一致，再运行完整测试，构建 CPU / NVIDIA 安装包和独立插件包。
- 签名脚本位于 `scripts/release/sign_windows.ps1`；所需 Secrets 沿用 `WINDOWS_SIGNING_PFX_BASE64`、`WINDOWS_SIGNING_PFX_PASSWORD`，时间戳变量为 `WINDOWS_TIMESTAMP_URL`。
- GitHub Release 包含两个安装器、插件包及对应 `.sha256`。发布动作仍由维护者推送标签触发，本次整理不创建或推送标签。
