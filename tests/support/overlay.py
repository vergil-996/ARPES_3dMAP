# -*- coding: utf-8 -*-
"""能带面叠加层的测试替身：记录 add_mesh / remove_actor，不建真实场景。

不需要 OpenGL：几何本身是纯函数（见 ``tests/rendering/test_surface_overlay.py``），
这里只替掉 pyvista 的 plotter 与 actor，供渲染层与集成用例共用。
"""
from __future__ import annotations


class FakeActor:
    """最小 actor：只记可见性、朝向与原点。"""

    def __init__(self, mesh, options):
        self.mesh = mesh
        self.options = options
        self.visible = True
        self.orientation = (0.0, 0.0, 0.0)
        self.origin = (0.0, 0.0, 0.0)

    def SetVisibility(self, value):  # noqa: N802 - VTK 风格命名
        self.visible = bool(value)

    def SetOrientation(self, *values):  # noqa: N802
        self.orientation = tuple(float(value) for value in values)

    def SetOrigin(self, *values):  # noqa: N802
        self.origin = tuple(float(value) for value in values)


class FakePlotter:
    """记录 actor 变化的替身 plotter。"""

    def __init__(self):
        self.actors = {}
        self.removed = []
        self.render_count = 0

    def add_mesh(self, mesh, *, name=None, **kwargs):
        actor = FakeActor(mesh, kwargs)
        self.actors[name] = actor
        return actor

    def remove_actor(self, name, render=True):
        self.removed.append(name)
        self.actors.pop(name, None)

    def render(self):
        self.render_count += 1


__all__ = ["FakeActor", "FakePlotter"]
