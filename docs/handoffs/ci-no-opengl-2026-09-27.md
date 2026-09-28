# CI 首次运行失败：runner 没有 OpenGL（2026-09-27）

## 任务目标

`.github/workflows/tests.yml`（本次目录整理同时新增）在 GitHub 上第一次运行就失败
（run `36311644786`，commit `d4119f7`）。定位原因，并让这条流水线在没有 GPU 的
runner 上能把测试跑完。

## 认领范围与共享文件

- 认领：`tests/support/environment.py`、`tests/exporting/test_export_effect.py`、
  `tests/rendering/test_alpha_channel.py`、`tests/integration/test_erase_regions.py`、
  本交接。
- **未改动** `.github/workflows/`、依赖列表、打包配置、主窗口与插件 API。本轮的修复
  不需要改工作流：探针让受影响的用例自己跳过。

## 结论：不是断言失败，是渲染时整个进程被访问违例打死

CI 日志的最后四行是 VTK 的报错，之后**没有 traceback、没有 `Ran N tests`、没有
FAILED 摘要**，步骤直接结束：

```
failed to get wglChoosePixelFormatARB      (vtkWin32OpenGLRenderWindow.cxx:656)
failed to get valid pixel format.          (vtkWin32OpenGLRenderWindow.cxx:731)
Failed to initialize OpenGL functions!     (vtkOpenGLRenderWindow.cxx:958)
osmesa.dll not found ... install the OSMesa library   (vtkOSOpenGLRenderWindow.cxx:135)
```

链条：

1. runner 是 `windows-2025-vs2026`，没有显卡驱动级的 OpenGL，Win32 后端拿不到像素格式。
2. `requirements.txt` 写的是 `vtk>=9.2` / `pyvista>=0.43`，CI 装到 **vtk 9.7.0 +
   pyvista 0.49.0**。VTK 9.7 的 `vtkOpenGLRenderWindow::New()` 会依次探测
   Win32 → X → EGL → OSMesa 后端；PyPI wheel 里没有 EGL，OSMesa 分支靠
   `LoadLibraryA("osmesa.dll")`，runner 上没有这个库 → 交回一个根本无法渲染的
   render window。
3. 死的第一条是 `tests/exporting/test_export_effect.py::test_exported_peak_follows_the_requested_energy`
   ——按 discover 顺序（core → exporting → …）这是全套件第一个真正调用 VTK 渲染的用例，
   `setUp` 里就 `render_3d()` → `pv.Plotter(off_screen=True)` + `screenshot()`
   （`publication_renderers.py:1233`、`:1286`）。之后的 500 多条一条都没跑到。

本机复现（`.local/venv-ci-probe`，装 CI 同版本 vtk 9.7.0/pyvista 0.49.0，用
`VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow` 复刻 runner 的终态）：
`Plotter()` 建得出来、`add_mesh` 也没事，**死在 `screenshot()`**，退出码
`0xC0000005`。反证：同样的 9.7.0/0.49.0 在有 GPU 的本机跑该模块 6 条全过 ——
不是版本或代码回归，纯环境限制。

## 已改内容

- `tests/support/environment.py`：新增 `opengl_available()` 与 `requires_opengl`。
  探测在**子进程**里真渲染一帧体数据（`ImageData` + `add_volume` + `screenshot`），
  崩溃、超时或没打标记都算没有 OpenGL，结果缓存一次。子进程是必需的：这类崩溃
  Python 侧 `try/except` 抓不到。
- 三个渲染模块挂 `@requires_opengl`，只跳真正渲染的类，同文件里的数值/模型用例照跑：
  `ExportEffectTests`（整体 6 条）、`RenderChannelTests`（9 条）、`EraseVolumeTests`（1 条）。

## 验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 无 GL 复现（改前） | `VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow .local/venv-ci-probe/Scripts/python -m unittest tests.exporting.test_export_effect -v` | 访问违例 `0xC0000005`，无摘要（与 CI 日志同形） |
| 三个模块（无 GL，改后） | 同环境跑三个模块 | `Ran 35 tests ... OK (skipped=16)`，退出码 0 |
| 三个模块（有 GL） | `.venv/Scripts/python -m unittest <三个模块>` | `Ran 35 tests ... OK`，没有跳过，用例真跑 |
| 全量（无 GL，改后） | `VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow .local/venv-ci-probe/Scripts/python -m unittest discover -s tests -t . -v` | `Ran 519 tests ... OK (skipped=17)`（16 渲染 + 1 既有 CuPy 跳过），连跑 4 次 |
| 全量（有 GL） | `.venv/Scripts/python -m unittest discover -s tests -t . -v` | `Ran 519 tests ... OK` |
| 无 GL 逐模块扫描（改前） | `.local/outputs/gl_sweep.py` | 50 个模块：3 个整进程崩溃（就是上面三个），47 个通过 |

**未执行**：真正的 CI 运行（需要 push）。所以「剩下 500+ 条在 runner 上是否通过」
目前仍未验证 —— 上一个 run 死在第一条渲染用例，这是本次修复之后才能看到的。

## 剩余事项

- **CI 不覆盖渲染路径**：3 个模块 16 条用例在没有 GL 的环境会跳过。要让 CI 真渲染，
  需要带 GL 或带 osmesa 的 runner，本轮没有解决。
- 两条走不通的路子（记录以免重复尝试）：Mesa 的 per-app `opengl32.dll` 放在
  `python.exe` 旁边对 VTK wheel **不生效**（仍加载 NVIDIA），塞进 `vtkmodules/`
  直接以 87 崩溃；mesa-dist-win 26.2.2 的 msvc/mingw 包里**都没有 `osmesa.dll`**，
  而 VTK 9.7 wheel 唯一会用的兜底就是它。
- **版本漂移**：本机 `.venv` 是 vtk 9.6.0/pyvista 0.47.1，CI 与安装包是 9.7.0/0.49.0。
  本轮不钉版本，但掩膜闪退那类驱动相关修复在 9.6 上验收、发布出去的是 9.7。
- **一次未复现的失败**：无 GL 全量跑中出现过 1 个 failure（当时未记录用例名），之后
  全量重跑 8 次、单模块重跑 12 次（另加 4 个 CPU 满载进程）都未复现。最可疑的是
  `tests/rendering/test_refresh_pipeline.py` ——它用 20ms / 40ms 定时窗口 + 1.5s 轮询
  断言合并行为，机器负载高时容易抖，但本轮**没有证实**。CI 上如果再红，先看这条。
- 复现环境 `.local/venv-ci-probe/`（约 150MB，不提交）保留：它是独立 venv，靠一个
  `.pth` 复用 `.venv` 的 site-packages，只把 vtk/pyvista 换成 CI 版本。不需要时
  `rm -rf .local/venv-ci-probe`。
