# -*- coding: utf-8 -*-
"""3D 轴标题改名失效的现场探针：不改行为，只把关键路径写进日志。

用法（仓库根目录，真实窗口）::

    .venv\\Scripts\\python.exe scripts/diagnostics/probe_3d_axis_titles.py [npz路径]

启动后在窗口里照平时的操作复现一次（主页 3D → 右键「轴标题…」→ 改名 → 确定），
然后关掉窗口。日志在 .local/logs/3d_axis_probe.log，记录了：

1. 弹窗拿到的 key 与当时状态里的文字、确定后状态里的文字；
2. 每次 3D 渲染解析出来的三条轴名；
3. ``_update_axes`` 的判定（坐标签名、轴名签名、这次是重画还是跳过）；
4. ``render_axes`` 收到的轴名、抛出的异常、场景里 CubeAxesActor 的数量变化；
5. 每次刷新请求的 cause / quality / page_id，以及被丢掉的原因。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from bandscope.app.qt_bootstrap import configure_qt_high_dpi, configure_qt_plugin_path

configure_qt_high_dpi()
configure_qt_plugin_path()

LOG_PATH = os.path.join(ROOT, ".local", "logs", "3d_axis_probe.log")
_START = time.time()


def plog(message):
    line = f"[{time.time() - _START:8.2f}s] {message}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _preview(value, limit=120):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _wrap(cls, name, describe):
    original = getattr(cls, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            plog(describe(args, kwargs, result))
        except Exception as exc:  # 探针自身的问题不能影响主流程
            plog(f"[probe] {name} 记录失败: {exc!r}")
        return result

    wrapper.__name__ = getattr(original, "__name__", name)
    wrapper.__doc__ = getattr(original, "__doc__", None)
    setattr(cls, name, wrapper)


def _cube_axes(window):
    plotter = getattr(window, "plotter", None)
    if plotter is None:
        return []
    try:
        actors = list(plotter.renderer.actors.values())
    except Exception:
        return []
    return [actor for actor in actors if type(actor).__name__ == "CubeAxesActor"]


def _describe_actor(actor):
    try:
        return (
            str(actor.GetXTitle()),
            str(actor.GetYTitle()),
            str(actor.GetZTitle()),
        )
    except Exception as exc:
        return f"<读取失败 {exc!r}>"


def install_probes():
    from bandscope.app import refactored_app as app_module
    from bandscope.rendering import render_core

    analyzer = app_module.My3DAnalyzer

    # 1) 弹窗入口与结果
    original_edit = analyzer._edit_three_d_axis_titles

    def edit_probe(self):
        spec = self._current_spec()
        key = self._three_d_axis_title_key()
        state = self._axis_titles()
        plog(
            f"[edit] 打开弹窗 page={getattr(spec, 'page_id', None)!r} key={_preview(key)} "
            f"state={_preview(state.texts(key) if key else None)}"
        )
        result = original_edit(self)
        plog(
            f"[edit] 弹窗结束 page={getattr(spec, 'page_id', None)!r} "
            f"state={_preview(state.texts(key) if key else None)} "
            f"resolved={_preview(self._three_d_axis_titles(spec))}"
        )
        return result

    analyzer._edit_three_d_axis_titles = edit_probe

    # 2) 渲染时解析出的轴名
    original_titles = analyzer._three_d_axis_titles

    def titles_probe(self, spec=None):
        titles = original_titles(self, spec)
        target = spec if spec is not None else self._current_spec()
        key = self._three_d_axis_title_key(target)
        plog(
            f"[resolve] page={getattr(target, 'page_id', None)!r} key={_preview(key)} "
            f"titles={_preview(titles)}"
        )
        return titles

    analyzer._three_d_axis_titles = titles_probe

    # 3) 改名后的刷新请求
    original_changed = analyzer._on_axis_title_changed

    def changed_probe(self):
        plog("[refresh] _on_axis_title_changed → OVERLAY/EXACT")
        return original_changed(self)

    analyzer._on_axis_title_changed = changed_probe

    original_dispatch = analyzer._dispatch_refresh_request

    def dispatch_probe(self, request):
        context = self.__dict__.get("current_render_context")
        plog(
            f"[dispatch] cause={getattr(request.cause, 'name', request.cause)} "
            f"quality={getattr(request.quality, 'value', request.quality)} "
            f"page_id={request.page_id!r} current_page={self._active_refresh_page_id()!r} "
            f"view={(context or {}).get('view') if isinstance(context, dict) else None!r}"
        )
        return original_dispatch(self, request)

    analyzer._dispatch_refresh_request = dispatch_probe

    # 4) _update_axes 的判定
    session = render_core.VolumeRenderSession
    original_update_axes = session._update_axes

    def update_axes_probe(self, show_axes, coords, axis_titles=None):
        before = self._axes_signature
        coord_keys = sorted(coords.keys()) if hasattr(coords, "keys") else type(coords).__name__
        plog(
            f"[update_axes] show={show_axes} coords_keys={coord_keys} "
            f"titles={_preview(axis_titles)} prev={_preview(before)}"
        )
        result = original_update_axes(self, show_axes, coords, axis_titles)
        plog(f"[update_axes] → 新签名={_preview(self._axes_signature)}")
        return result

    session._update_axes = update_axes_probe

    # 5) render_axes：收到的轴名、异常、场景里的坐标轴数量
    original_render_axes = render_core.VisualEngine.render_axes

    def render_axes_probe(plotter, data_shape, coords, axis_titles=None):
        head = f"[render_axes] titles={_preview(axis_titles)} coords="
        if hasattr(coords, "keys"):
            pieces = []
            for key in ("X", "Y", "E"):
                values = coords.get(key)
                try:
                    size = len(values)
                    edges = (float(values[0]), float(values[-1])) if size else None
                except Exception as exc:
                    size, edges = f"<{exc!r}>", None
                pieces.append(f"{key}:n={size},ends={edges}")
            head += " ".join(pieces)
        else:
            head += type(coords).__name__
        plog(head)
        try:
            result = original_render_axes(plotter, data_shape, coords, axis_titles=axis_titles)
        except Exception as exc:
            plog(f"[render_axes] 抛出 {exc!r}")
            raise
        try:
            actors = [
                actor for actor in plotter.renderer.actors.values()
                if type(actor).__name__ == "CubeAxesActor"
            ]
            plog(
                f"[render_axes] 场景里坐标轴数量={len(actors)} "
                f"{[_describe_actor(actor) for actor in actors]}"
            )
        except Exception as exc:
            plog(f"[render_axes] 数量统计失败 {exc!r}")
        return result

    render_core.VisualEngine.render_axes = staticmethod(render_axes_probe)

    plog("[probe] 已装载探针")


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", nargs="?", help="可选：启动后自动加载的 npz")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="探针自检：自动等场景、自动改名、自动退出（不给用户用）",
    )
    args = parser.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as handle:
        handle.write(f"# 3D 轴标题探针 {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)

    import bandscope.app.refactored_app as refactored_app

    install_probes()

    window = refactored_app.My3DAnalyzer()
    window.show()
    window.resize(1500, 950)

    if args.data:
        plog(f"[probe] 自动加载 {args.data}")
        window.load_data(os.path.abspath(args.data))

    if args.selftest:
        _run_selftest(app, window)
    else:
        plog(
            "[probe] 请复现：主页 3D → 右键「轴标题…」→ 改名 → 确定；"
            "确认现象后关闭窗口，日志在 .local/logs/3d_axis_probe.log"
        )
    return app.exec_()


def _run_selftest(app, window):
    """自检用：等 3D 场景就绪 → 自动改名 → 等一会 → 退出。"""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    def answer():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            plog("[selftest] 没等到弹窗")
            return
        for axis, value in (("X", "PROBE-X"), ("Y", "PROBE-Y"), ("E", "PROBE-E")):
            dialog.edits[axis].setText(value)
        dialog.accept()

    def kick(attempt=0):
        ready = (
            (window.current_render_context or {}).get("view") == "3d"
            and _cube_axes(window)
        )
        if not ready and attempt < 60:
            QTimer.singleShot(2000, lambda: kick(attempt + 1))
            return
        if not window.timeline_bar.switch_axes.isChecked():
            window.timeline_bar.switch_axes.setChecked(True)
            QTimer.singleShot(4000, lambda: kick(attempt))
            return
        plog("[selftest] 场景就绪，自动改名")
        QTimer.singleShot(200, answer)
        window._edit_three_d_axis_titles()
        QTimer.singleShot(6000, app.quit)

    QTimer.singleShot(3000, kick)


if __name__ == "__main__":
    sys.exit(main())
