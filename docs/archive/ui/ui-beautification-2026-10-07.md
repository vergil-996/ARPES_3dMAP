# 界面美化收尾计划（弹窗 / 表格 / 菜单 / 滚动条）（2026-10-07 完成）

## 状态与交接

- 日期：2026-10-07。
- 状态：**已实施、已回归、已通过真实窗口视觉复核**。落地的就是编写本计划时工作区里
  已有的那份未提交参考改动——接手时选择的是「复审并验证」，没有另起第二份实现。
  改动仍未提交，见文末「验收记录」。
- 注意：工作区里的参考改动与本计划逐处对照过，无需再 `git checkout -- bandscope/` 重做。

- 负责范围：仅界面样式（QSS / 颜色令牌），不改任何交互逻辑、信号连接与布局结构。
- 共享文件：`bandscope/ui/theme.py`（设计令牌唯一来源）、`bandscope/app/startup.py`
  （全局样式）、`bandscope/app/refactored_app.py`（主窗口）。如涉及与其他任务并行，
  这三个文件的改动需向集成任务交代。
- 接手前阅读根目录 `AGENTS.md`、`docs/architecture/repository-map.md`、
  `docs/development/testing.md`。
- 验收标准：`python -m unittest discover -s tests -t . -v` 全部通过（当前基线 981 项）；
  `scripts/validation/smoke_test.py .local/data/NiHITP_calibrated_2.npz` PASS；
  第 5 节的视觉复核截图确认四处界面观感达标。

## 背景：审计发现

以 v1.12.2 实际运行截图为据（审计截图在 `.local/outputs/beauty_audit/`，
复现脚本见第 5 节）。主窗口、渲染控制页、导出对话框状态良好，问题集中在
最近几个版本新加的界面：

| # | 位置 | 问题 | 优先级 |
|---|---|---|---|
| 1 | `bandscope/ui/settings_popups.py` `_NonModalPopup` | 独立顶层窗口未上底色：内容是深色卡片，窗体是系统默认浅色，分组标题白字糊成一片（见 `beauty_audit/popup_denoise.png` 修复前） | P0 |
| 2 | `bandscope/extensions/plugin_dialog.py` | 状态列用 `Qt.red` / `Qt.yellow`（3 处），深色主题下刺眼，违反主题语义色约定 | P0 |
| 3 | 全局（`bandscope/app/startup.py` `APP_FEEDBACK_STYLE`） | 无 QScrollBar 样式：插件管理表格、导出预览 QScrollArea、页面树等内容一多就冒出 Windows 亮色滚动条 | P0 |
| 4 | `bandscope/ui/result_workspace.py` `PageTitleLabel.contextMenuEvent` | 右键菜单完全没上样式（默认浅色菜单） | P0 |
| 5 | `bandscope/extensions/plugin_dialog.py` | QTableWidget 未定义选中态：选中行是系统默认蓝色，与品牌粉主题冲突 | P1 |
| 6 | `bandscope/app/startup.py` `APP_FEEDBACK_STYLE` | QMessageBox/QProgressDialog 所有按钮都是粉底主色，「取消」与「确定」无主次 | P1 |
| 7 | `refactored_app.py` / `page_tree.py` 右键菜单 | 两处复制同样的两行简陋样式（无圆角/边框/内边距/禁用态/分隔线），应统一收进 theme | P1 |
| 8 | `bandscope/extensions/plugin_dialog.py` | QTabWidget 选中态只有文字变白，视觉太弱 | P2 |

已排除的非问题（不要重复"修复"）：

- `publication_dialog.py` / `publication_renderers.py` 的 `#000000`、`#FFFFFF` 等是
  出版导出物的白底画布配色，与界面主题无关。
- `crop_controls.py` 的 `#171923` / `#ffffff` 是剪刀光标的描边对比色，刻意为之。
- QMessageBox 在开发脚本里呈浅色是因为脚本没应用 `APP_FEEDBACK_STYLE`；生产启动
  （`startup.main()`）会全局应用，不是缺陷。

## 设计令牌约束

所有颜色必须取自 `bandscope/ui/theme.py`，禁止新增硬编码 `#RRGGBB`。本计划用到的令牌：

- 背景：`BG_0` 窗口底 → `BG_4` 悬停/轨道；边框 `BORDER` / `BORDER_STRONG` / `BORDER_HEX`
- 文本：`TEXT_1` 主文本、`TEXT_2` 次级、`TEXT_3` 禁用/弱化
- 语义：`ACCENT` / `ACCENT_SOFT` / `ACCENT_DIM` / `ACCENT_HOVER` / `ACCENT_ON`，
  `DANGER`（错误/不可逆）、`WARNING`（待处理/警示）
- 圆角：`R_S`=6、`R_M`=10

## 实施任务

### 任务 1：设置弹窗深色底（对应发现 1）

`bandscope/ui/settings_popups.py`：

- 顶部加 `import bandscope.ui.theme as theme`。
- `_NonModalPopup.__init__` 中追加（注意必须先 `WA_StyledBackground`，
  否则纯 QWidget 子类不绘制 QSS 背景）：

```python
self.setAttribute(Qt.WA_StyledBackground, True)
self.setStyleSheet(
    f"_NonModalPopup {{ background-color: {theme.BG_2}; }}"
    f"QLabel {{ color: {theme.TEXT_1}; background: transparent; }}"
)
```

QSS 类选择器对子类（`DenoiseSettingsPopup` / `WaterfallSettingsPopup`）同样生效，
无需逐个改子类。

### 任务 2：theme.py 新增两个样式助手（支撑任务 3、4、6）

在 `nav_tab_qss()` 之后新增：

- `context_menu_qss()`：原生 QMenu 统一样式。`BG_3` 底 + `BORDER_STRONG` 细边框 +
  `R_S` 圆角 + 6px 外边距；`::item` 内边距 `6px 24px 6px 12px`、圆角 `R_S-2`、
  选中 `BG_4`、禁用 `TEXT_3`；`::separator` 高 1px、`BORDER` 色、上下留白。
- `scrollbar_qss()`：原生滚动条深色细条。纵向宽 10px / 横向高 10px、透明底；
  `::handle` 用 `BG_4`、圆角 4px、最小 24px，hover 用 `TEXT_3`；
  `::add-line` / `::sub-line` 归零隐藏箭头按钮；`::add-page` / `::sub-page` 透明。
  SiUI 自绘滚动条不读 QSS，此样式只影响原生 Qt 容器。

### 任务 3：全局样式补滚动条与按钮层级（对应发现 3、6）

`bandscope/app/startup.py`：

- `APP_FEEDBACK_STYLE` 末尾追加：`APP_FEEDBACK_STYLE += theme.scrollbar_qss()`。
- QMessageBox / QProgressDialog 的 `QPushButton` 从全主色改为层级制：
  普通按钮 `BG_4` 底 + `TEXT_1` 字 + `BORDER` 边框，hover 边框升到 `BORDER_STRONG`；
  用 `:default` 伪态给默认按钮上 `ACCENT` 底 + `ACCENT_ON` 字，hover 用 `ACCENT_HOVER`。
  Qt 会把 AcceptRole/Ok 自动设为默认按钮，各对话框已有的 `setDefaultButton` 调用
  不受影响，语义自然正确（主操作高亮、取消灰底）。

### 任务 4：右键菜单统一（对应发现 4、7）

- `bandscope/app/refactored_app.py`：`CONTEXT_MENU_STYLE` 的值改为
  `theme.context_menu_qss()`（调用点不动）。
- `bandscope/ui/page_tree.py` `_on_context_menu`：内联两行 QSS 替换为
  `menu.setStyleSheet(theme.context_menu_qss())`。
- `bandscope/ui/result_workspace.py` `PageTitleLabel.contextMenuEvent`：
  创建 `QMenu` 后补一行 `menu.setStyleSheet(theme.context_menu_qss())`
  （该文件已 `import bandscope.ui.theme as theme`）。

### 任务 5：插件管理对话框（对应发现 2、5、8）

`bandscope/extensions/plugin_dialog.py`：

- 3 处状态色：`Qt.red` → `QColor(theme.DANGER)`（已安装页 `refresh()` 与官方页
  `_render_catalog()` 各一处），`Qt.yellow` → `QColor(theme.WARNING)`（`refresh()` 一处）。
- 对话框 QSS（`__init__` 的 `setStyleSheet`）补表格条目态：

```
QTableWidget::item { padding: 2px 4px; }
QTableWidget::item:hover { background-color: BG_3; }
QTableWidget::item:selected { background-color: ACCENT_SOFT; color: TEXT_1; }
```

- 页签改为与主界面页签呼应的胶囊选中态（`QTabBar::tab` 加 `margin: 2px;
  border-radius: 6px; border: 1px solid transparent;`，`:selected` 用
  `ACCENT_SOFT` 底 + `ACCENT` 字 + `ACCENT_DIM` 边框 + `font-weight: 600`），
  与 `theme.nav_tab_qss()` 同族。

## 验证

1. 单元回归：`python -m unittest discover -s tests -t . -v` —— 不得有新增失败
   （参考实现跑过：981 项全部通过）。
2. 冒烟：`scripts/validation/smoke_test.py .local/data/NiHITP_calibrated_2.npz
   --output-dir .local/outputs/<目录> --size 1550x950` —— PASS 且主窗口无回归。
3. 视觉复核：`scripts/diagnostics/audit_dialog_beauty.py`（参考实现已新增此脚本，
   若选择还原则需重建；用途是逐个打开插件管理两页、去噪设置弹窗、示例消息框、
   右键菜单与带滚动条的表格并截图）。逐项对照：
   - 去噪/瀑布图弹窗：窗体深色，分组标题清晰可读；
   - 插件管理：选中行主色柔光底、页签主色胶囊、状态列红/黄为 `DANGER`/`WARNING`；
   - 消息框：默认按钮粉底，其余灰底；
   - 右键菜单：深色圆角、禁用项灰化、分隔线可见；
   - 表格滚动条：深色细条，无亮色箭头按钮。

   脚本注意事项（踩过的坑）：① 必须 `app.setStyleSheet(APP_FEEDBACK_STYLE)`，
   否则 QMessageBox 截图不代表生产观感；② 清理插件管理对话框时**只 `close()`
   不 `deleteLater()`**——它的官方目录后台线程可能还在跑，`deleteLater` 会让
   QThread 在运行中被析构，直接崩进程；③ 全程 `QTimer` 链驱动，不要在窗口消息
   调度里手动 `processEvents`。

## 交付物

- 上述 7 个源文件的样式修改（`settings_popups.py`、`theme.py`、`startup.py`、
  `refactored_app.py`、`page_tree.py`、`result_workspace.py`、`plugin_dialog.py`）。
- `scripts/diagnostics/audit_dialog_beauty.py` 视觉审计脚本（可复用）。
- 修复后截图放 `.local/outputs/`（不提交）。
- 完成后归档本计划到 `docs/archive/`，并同步 `docs/plans/README.md` 索引。

## 验收记录（2026-10-07）

- 自动化回归：`python -m unittest discover -s tests -t . -v` → **Ran 981 tests, OK**，
  0 失败 0 错误（落定前后各跑一次，均通过）；`python -m compileall -q bandscope plugins
  scripts tests start.py` 通过。
- 冒烟：`scripts/validation/smoke_test.py .local/data/NiHITP_calibrated_2.npz
  --output-dir .local/outputs/ui_beauty_verify --size 1550x950` → **PASS**（exit 0），
  主窗口、渲染控制页无回归。
- 视觉复核：真实 Windows 平台跑 `scripts/diagnostics/audit_dialog_beauty.py --output-dir
  .local/outputs/ui_beauty_audit_verify`，逐项对照：
  - 去噪弹窗：窗体底色为 `BG_2`（像素采样 `#181822`，与 `theme.BG_2` 一致），分组标题
    由「白字糊在浅底上」变为清晰可读；对照修复前 `.local/outputs/beauty_audit/popup_denoise.png`。
  - 插件管理：选中行为 `ACCENT_SOFT` 柔光底、页签为主色胶囊、状态列 `WARNING` 为
    `#fbbf24`（「待重启卸载」行）、`DANGER` 为 `#f0506e`（`load_error` 行）。
    截图见 `.local/outputs/ui_beauty_audit_verify/_probe_real_platform.png`。
  - 消息框：默认按钮 `ACCENT` + `ACCENT_ON`，其余按钮 `BG_4` 灰底（采样 `#ff7eb6` /
    `#2a2a38` / `#1a0b14`）。
  - 右键菜单：`BG_3` 底、圆角、分隔线可见、禁用项 `TEXT_3`。
  - 表格滚动条：handle 为 `BG_4`、轨道透明、无箭头按钮。
- 两处实现细节（接手时确认，非缺陷）：
  - **必须以真实平台复核表格选中态。** offscreen 平台下 `QTableWidget::item:selected`
    不生效，选中行会渲染成系统蓝 `#628dc4`，单看截图会误判为「发现 5 未修复」；真实平台
    上确为 `ACCENT_SOFT`。
  - `QProgressDialog` 的「取消」被 Qt 置为默认按钮（实测 `isDefault()==True`）。本计划
    任务 3 字面要求 `QMessageBox` / `QProgressDialog` 都用 `:default` 上主色，但那样会让
    取消键变成粉底主操作，与「主操作高亮、取消灰底」的意图相反。实现上**只对
    `QMessageBox` 用 `:default`**，并在 `startup.py` 就地注明原因，避免后续被当成漏改。
  - `table_scrollbar.png` 顶部左右两块白斑来自审计脚本合成表格的 QTableWidget 角按钮，
    不是产品缺陷：插件管理两张表的纵向表头是隐藏的，角按钮宽度为 0（实测渲染该对话框
    浅色像素为 0）。
