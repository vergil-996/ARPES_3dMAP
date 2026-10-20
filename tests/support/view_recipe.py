"""Synthetic recipe inputs and a real control/workspace host without OpenGL."""
import copy
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pyvista as pv
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import QCoreApplication, QEvent

from bandscope.app.refactored_app import My3DAnalyzer
from bandscope.core.analyzer_core import AnalyzerCore
from bandscope.core.data_scope import DataScopeDescriptor
from bandscope.core.view_recipe import FORMAT, ProvenanceStore, plain, state_digest
from bandscope.ui.result_workspace import AnalysisPageSpec


def synthetic_npz(path, *, seed=0, descending=False):
    data = np.random.default_rng(seed).random((6, 7, 8, 3), dtype=np.float32)
    np.savez(path, sample=data, kx=np.linspace(-1, 1, 6), ky=np.linspace(-2, 2, 7),
             E=np.linspace(1, -1, 8) if descending else np.linspace(-1, 1, 8),
             time=np.array([-1.0, 0.0, 2.0]), axis_mapping_version=1,
             axis_order=np.array(["X", "Y", "E", "T"]))
    return data


def recipe_document(path, *, kind="home", params=None):
    core = AnalyzerCore()
    success, info = core.load_npz(str(path))
    if not success:
        raise AssertionError(info)
    source = core.source_identity.descriptor(core.source_layout)
    control = {"image": {"slider_time": {"minimum": 0, "maximum": 2, "value": 1},
                          "switch_flip": False, "switch_axes": True, "rotation_angle": 0.0},
               "data_process": {"s_t_low": {"value": 0}, "s_t_up": {"value": 2}},
               "render": {}, "denoise_detail": {}}
    home = AnalysisPageSpec("home", "原始视图", "home", "test", closeable=False,
                            params={"control_state": copy.deepcopy(control)})
    pages = [home]
    if kind != "home":
        pages.append(AnalysisPageSpec("target", "目标视图", kind, "test", source_page_id="home",
                      params={"control_state": copy.deepcopy(control), **(params or {})}))
    elif params:
        home.params.update(params)
    store = ProvenanceStore()
    records = []
    for page in pages:
        record = asdict(page)
        key = store.add(source, page.page_kind, page.params)
        record["recipe_id"] = key
        records.append(record)
    d = plain({"format": FORMAT, "schema_version": 1, "primary_source_id": source["source_id"],
               "sources": store.sources, "recipes": store.nodes, "pages": records,
               "home_page_id": "home", "target_page_id": pages[-1].page_id,
               "scopes": {"full": asdict(DataScopeDescriptor.full(core.raw_data.shape))},
               "environment": {"denoise_methods": [], "denoise_controls": {}},
               "view_state": {}, "publication": {}, "results": {}})
    d["state_digest"] = state_digest(d)
    return core, d


class _Plotter(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.camera = pv.Camera()
        self.camera.position = (400, 300, 500)
        self.camera.focal_point = (100, 100, 100)
        self.camera.up = (0, 0, 1)
        self.iren = SimpleNamespace(add_observer=lambda *a: 1, remove_observer=lambda *a: None)
        self.interactor = self
        self.renderer = Mock()
        self.renderer.get_default_cam_pos.return_value = ((1, 1, 1), (0, 0, 0), (0, 0, 1))
        self.renderer.GetVolumes.return_value.GetNumberOfItems.return_value = 0
        self.ren_win = SimpleNamespace(ReportCapabilities=lambda: "")
        self.box_widgets = []
    def clear_box_widgets(self):
        self.box_widgets.clear()
    @property
    def camera_position(self):
        return self.camera.position, self.camera.focal_point, self.camera.up
    @camera_position.setter
    def camera_position(self, position):
        self.camera.position, self.camera.focal_point, self.camera.up = position
    def render(self):
        pass
    def set_background(self, *a, **kw):
        pass
    def reset_camera(self, *a, **kw):
        pass
    def clear(self, *a, **kw):
        pass
    def add_axes(self, *a, **kw):
        pass
    def close(self):
        return super().close()


def control_window():
    """Keep real controls and loading; replace only GPU drawing/network updates."""
    def refresh(window):
        if window.core.raw_data is not None:
            window.current_render_context = window._compute_render_context(window.left_workspace.current_spec())
        window._render_exact_ready = True
    def request(window, *args, **kwargs):
        if not window._syncing_controls:
            refresh(window)
    with patch("bandscope.app.refactored_app.QtInteractor", _Plotter), \
         patch("bandscope.app.refactored_app.UpdateController"), \
         patch.object(My3DAnalyzer, "_reset_3d_camera_to_default"), \
         patch.object(My3DAnalyzer, "_sync_gpu_display_backend"), \
         patch.object(My3DAnalyzer, "request_refresh", request), \
         patch.object(My3DAnalyzer, "global_refresh", refresh):
        window = My3DAnalyzer()
    # Persist replacement for subsequent data loads without holding patch state.
    window.global_refresh = lambda: refresh(window)
    window.request_refresh = lambda *a, **kw: request(window, *a, **kw)
    window._reset_3d_camera_to_default = lambda: None
    close = window.close
    def dispose():
        # Drain queued layout callbacks while their real controls still exist.
        QCoreApplication.processEvents()
        close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    window.close = dispose
    return window
