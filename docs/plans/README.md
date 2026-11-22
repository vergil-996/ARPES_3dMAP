# 当前计划

本索引只列尚待设计或实施的工作；按任务选择对应计划，不批量读取本目录。

- [BandScope 按钮图标高 DPI 优化交接计划](fluent-icon-high-dpi.md)（已实施；2012 项全量回归、多倍率像素与单屏实窗验收通过；真实双屏跨屏移动待验；v1.22.4 已发布 GitHub 与 MatElab）
- [BandScope 自动轴分配与加载后重设](axis-assignment.md)（已实施；自动化与 WSe₂ 真实窗口验收通过；v1.22.1 已发布 GitHub 与 MatElab）
- [BandScope 画布工具栏与 Z 轴旋钮改造](canvas-toolbar-rotation-dial.md)（已实施；定向测试与 WSe₂ 真实窗口验收通过；v1.22.1 已发布 GitHub 与 MatElab）
- [色阶调整：累计直方图、三滑块与色带截断](levels-and-colormap-range.md)（已实施；2026-11-21 视觉返工后分段全量回归与 WSe₂ 真实窗口验收通过；v1.22.1 已发布 GitHub 与 MatElab，单进程全量的既有区间崩溃已在该版修复）
- [将色带选择并入“输出色带”](output-colormap-selector.md)（已实施；自动化与 WSe₂ 真实窗口验收通过；v1.22.1 已发布 GitHub 与 MatElab，人工视觉复核未执行）
- [三维不透明度曲线直接编辑](opacity-curve-control-points.md)（已实施；WSe₂ 真实窗口与 CPU/CUDA／出版／Blender 数据验收通过；v1.22.1 已发布 GitHub 与 MatElab，真实 Blender 进程渲染与人工视觉复核未执行）
- [不透明度曲线支持平滑、直线和垂直阶跃](opacity-curve-interpolation.md)（已实施；1988 项全量回归与 WSe₂ 真实窗口 56 项验收通过；真实 Blender 进程渲染及维护者人工视觉复核未执行；v1.22.4 已发布 GitHub 与 MatElab）
- [移除强度归一化卡片，改造独立不透明度曲线](independent-opacity-curve.md)（已实施；WSe₂ 验收待补，全量回归存在其他任务失败）
- [NiHITP 大体数据色阶与独立透明度滑条优化](independent-display-slider-performance.md)（诊断完成，待实施）
- [体渲染显示与质量改进](volume-display-quality.md)：首期已实现独立透明度、归一化、自定义参数和质量策略，复现 schema 2 与 Blender 独立显示（schema 10 起）已贯通，新建工程现为 schema 11；真实对照和开发冻结包验收通过，已随 v1.20.0 发布（2026-11-13），保留维护者人工视觉复核，不包含等值面。
- [Blender 体渲染工程导出](blender-scene-export.md)：当前五项预设新增十种统一大写 A 的小号文本，独立开关默认开启，字体打包、文本灯光链接及 schema 9 已实现，后续 schema 10／11 继续保留；v1.20.0 全量 1669 项测试、真实多页／八材质／WSe₂／窗口及 CPU／NVIDIA 最终冻结包验收通过；多视图共享预设随 v1.21.0 发布（2026-11-16），保留维护者人工视觉复核。
- [Blender 薄层长方体八材质预设](blender-box-materials.md)：八材质已合并为一个“薄层长方体”预设，一次生成八块统一橙色、默认参数的独立薄板；四种自发光／透射材质在右侧，四种受光材质及辅助灯在前侧并上移半层，移到数据体后八层高度交替；材质与布局设置区已移除，已随 v1.19.0 发布（2026-11-10），最新验证状态见主记录，保留维护者人工视觉复核。
- [MatElab 更新源接入](matelab-update-source.md)：已实现，v1.14.0–v1.16.0 均已对真实服务器完成发布；v1.16.0 起按新约定不再做回传下载校验；待界面人工验收。
- [局部增强控件简化与锁定色带整合](enhancement-controls-simplify.md)：已实施并随 v1.14.5 发布（2026-10-19）；2026-10-20 色带复制/粘贴随 v1.15.0 发布；剩余人工视觉与发行包验收见主记录。
- [局部增强交互性能（拖动帧时）优化](enhancement-render-performance.md)：P0、CUDA 映射、2D 复用与 ROI 异步已实施（2026-10-18）；真实窗口 3D 刷新 78.5 → 47.4 ms、空闲 3.9 ms、首次 ROI 去噪不再冻结界面；剩余 CPU 路线与发行验收。

已实施功能的剩余修复、人工验收和发布跟进从 [当前交接](../handoffs/README.md) 接续。EDC 瀑布图、插件管理和平带增强的详细方案由该入口按需链接。

新计划使用清晰主题命名，注明日期与状态，只写当前目标、负责范围（含必要接口约定）、验收标准与下一步。每个事项只维护一份当前状态主记录，索引只给入口。实施完成即移出本索引；有剩余事项时由交接入口指向主记录，全部关闭后连同材料归档到 `docs/archive/`。
