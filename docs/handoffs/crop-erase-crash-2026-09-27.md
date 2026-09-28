# 裁剪后裁空闪退：定位与修复（2026-09-27）

## 任务目标

用户报告「先用裁剪、再用裁空会闪退」，并要求用真实拖动裁剪框的冒烟测试复现。默认裁剪框
（不拖动）不触发。

## 认领范围与共享文件

- 认领：`bandscope/rendering/render_core.py`、`bandscope/exporting/publication_renderers.py`、
  `tests/rendering/test_volume_render_session.py`、`scripts/diagnostics/`。
- 未改动主窗口、插件 API、依赖、打包配置与 CI。

## 结论：崩溃不是 Python 异常，是驱动层访问违例

事件日志（`Application Error`）显示用户那次闪退是
`BandScope_NVIDIA.exe` → `nvoglv64.dll` → `0xc0000005`，即硬崩溃，没有 traceback。
本轮在 `.venv` 里用真实窗口 + 真实鼠标事件复现了同一条路径，`faulthandler` 给出的栈：

```
crop_integration.on_cut → result_workspace.add_page → global_refresh
→ _render_active_page → VolumeRenderSession.render → plotter.render()   ← 访问违例
```

触发链（两点同时成立才崩）：

1. **先裁剪**：裁剪页不再是完整数据，`data_bounds` 下界非零（拖动保留下段时，
   例：`(0,199,0,199,62,499)`）。`VolumeRenderSession._build_scene` 把这个范围直接
   写成 `ImageData.extent`，于是网格带**非零起始索引**。
2. **后裁空**：裁空把选中体素写成 NaN。有 NaN 时渲染走 `has_mask` 分支：切
   `mapper="gpu"` 并 `configure_volume_mask` → `SetMaskInput`（掩膜结构从网格复制，
   同样是非零起始索引）。

「二值掩膜 + 非零起始索引的网格」在本机 NVIDIA 驱动上越界访问。默认裁剪框不触发是因为
它下界为 0，而且整页被裁空后走空态、根本不渲染体积。

## 已改内容

- `render_core.build_volume_grid(...)`：新增网格构造函数。**掩膜路径**改用零基 extent，
  ROI 起点写进 `origin`（`origin + index*spacing` 与非零 extent 表示逐个采样点等价，
  已用数值断言核对 dimensions / bounds / points）。**无掩膜路径保持原有非零 extent**，
  不重新引入旧注释里记录过的整体 origin 偏移问题。
- `configure_volume_mask`：补文档说明网格必须是零基。
- `publication_renderers.render_3d`：导出侧同一模式（`extent=data_bounds` + 掩膜 gpu mapper）
  会踩同一个崩溃，改为复用 `build_volume_grid`。
- `tests/rendering/test_volume_render_session.py`：新增 `MaskedVolumeGridTests`，固定两条
  不变量——有掩膜时 extent 必须从 0 开始且世界坐标不变；无掩膜时保持绝对 extent 表示。
- `scripts/diagnostics/repro_crop_erase.py`：真实窗口 + 真实鼠标事件的复现/验收脚本。
  3D 用 VTK 交互盒拖面，2D 用画布矩形框选；`--mode default` 是不拖动的对照组。
- `scripts/diagnostics/probe_vtk_mask_extent.py`：把问题缩到纯 VTK 的秒级复现。

## 验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 崩溃复现（修复前） | `repro_crop_erase.py <npz> --page home --mode drag --keep upper --shrink 0.2` | 连跑 2 次都段错误（139），栈同上 |
| 修复后同一条流程 | 同上 | 退出码 0，裁空页正常渲染，体积世界坐标与裁剪页一致 |
| 对照组（默认框） | `--mode default` | 通过，未复现 |
| 二维页拖动 | `--page axis2d --mode drag` | 通过 |
| 纯 VTK 最小复现 | `probe_vtk_mask_extent.py {seq_small,seq_app,seq_app_nomask,seq_app_zerobased,seq_app_noclip}` | 只有掩膜 + 非零 extent 且数据够大时段错误，零基变体通过 |
| 视觉核对 | `.local/outputs/crop_erase/home-drag-{crop,erase}-page-gl.png` | 裁剪页与裁空页体积位置一致，裁空区透明；窗口 `grab()` 抓不到 GL 区域（全黑），必须用 VTK 截图 |
| 全量回归 | `python -m unittest discover -s tests -t . -v` | 519 tests OK（`.local/logs/full-suite-after-fix.log`） |

## 剩余事项

- **打包版未重新构建**：用户跑的是 `D:\bandscope\BandScope\BandScope_NVIDIA.exe`，本轮只验证
  了 `.venv` 源码运行。要让用户拿到修复，需要重建 NVIDIA 包并再走一次上面的拖动冒烟；
  没有执行这一步。
- 本轮只对 kx–ky 二维页、3D 原始视图两种页面做了真实拖动验收；时间积分、瀑布图、曲线类
  页面没有单独重跑（有自动化测试覆盖，但没有真实拖动验收）。
- 代码阅读时发现、**本轮未验证也未修改**的两点，留给后续确认：
  1. 对「裁剪页」再做一次裁剪时，`on_cut` 的 `volume_roi` 分支用相对当前 compact ROI 的
     `data_bounds` 注册新 ROI scope，坐标基准可能不对；
  2. `configure_volume_mask` 写入的掩膜值是 1/0，而 VTK 文档写的是二值掩膜用 255/0；
     现有测试显示空缺区域确实透明，所以按未确认处理。
