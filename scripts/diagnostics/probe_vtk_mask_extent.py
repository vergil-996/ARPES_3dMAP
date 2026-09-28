# -*- coding: utf-8 -*-
"""把「裁剪后裁空」的崩溃缩成纯 VTK 场景，用于定位与后续回归。

app 里出事的顺序是：先是裁剪页（无 NaN → smart mapper，网格带非零 extent），
再是裁空页（有 NaN → gpu mapper + SetMaskInput，网格同样是那个非零 extent）。
这里按同样顺序在同一个 plotter 里重建两次体积。

每个工况单独一个进程跑，崩溃不会掩盖其它工况：
    .venv/Scripts/python.exe scripts/diagnostics/probe_vtk_mask_extent.py <case>

工况与 2026-09-27 在本机（NVIDIA 32.0.15.9579）实测结果：
- seq_small         60x60x80，掩膜 + 非零 extent          通过
- seq_app           200x200x500 裁到 200x200x438 后同上    **段错误**（退出码 139）
- seq_app_nomask    同上但裁空页没有 NaN（不挂掩膜）        通过
- seq_app_zerobased 同上但掩膜页用零基 extent + origin     通过（就是修复后的表示）
- seq_app_noclip    同上但去掉切面                          段错误

结论：崩溃由「二值掩膜 + 非零起始索引的网格」造成，与切面无关；数据量小时
越界读落在已映射内存里，所以小数据不复现。掩膜体一律用零基 extent，
ROI 起点写进 origin（`render_core.build_volume_grid`）。
"""
import os
import sys

import numpy as np
import pyvista as pv

SMALL = (60, 60, 80)
APP = (200, 200, 500)
Z_CROP = 62  # 保留下段：extent 下界非零


def make_volume(shape, masked):
    data = np.linspace(0.0, 1.0, int(np.prod(shape)), dtype=np.float32).reshape(shape)
    if masked:
        data[shape[0] // 3: 2 * shape[0] // 3, :, shape[2] // 2:] = np.nan
    return data


def clip_planes(bounds):
    """按 app 的写法构造 ROI 切面（渲染坐标）。"""
    import vtk

    planes = vtk.vtkPlaneCollection()
    specs = [
        ((bounds[0], 0, 0), (1, 0, 0)),
        ((bounds[1], 0, 0), (-1, 0, 0)),
        ((0, bounds[2], 0), (0, 1, 0)),
        ((0, bounds[3], 0), (0, -1, 0)),
        ((0, 0, bounds[4]), (0, 0, 1)),
        ((0, 0, bounds[5]), (0, 0, -1)),
    ]
    for origin, normal in specs:
        plane = vtk.vtkPlane()
        plane.SetOrigin(origin)
        plane.SetNormal(normal)
        planes.AddItem(plane)
    return planes


def add_scene(plotter, data, bounds, full_shape, *, masked, zero_based, clip, small):
    spacing = tuple(200.0 / (n - 1) for n in full_shape)
    if zero_based and masked:
        extent = tuple(value for size in data.shape[:3] for value in (0, int(size) - 1))
        origin = tuple(float(bounds[2 * axis]) * spacing[axis] for axis in range(3))
    else:
        extent = tuple(int(v) for v in bounds)
        origin = (0.0, 0.0, 0.0)
    grid = pv.ImageData()
    grid.spacing = spacing
    grid.extent = extent
    grid.origin = origin
    buffer = np.asfortranarray(np.nan_to_num(data) if masked else data)
    grid.point_data["values"] = buffer.ravel(order="F")

    volume = plotter.add_volume(
        grid, scalars="values", cmap="magma", opacity=[0.0, 0.2, 0.6, 1.0],
        clim=(0.0, 1.0), show_scalar_bar=False, name="main_vol",
        mapper="gpu" if masked else "smart", render=False,
    )
    if clip:
        render_bounds = (0.0, 200.0, 0.0, 200.0,
                         float(bounds[4]) * spacing[2], 200.0)
        volume.mapper.SetClippingPlanes(clip_planes(render_bounds))
    if masked:
        mask = pv.ImageData()
        mask.CopyStructure(grid)
        mask.point_data["mask"] = np.isfinite(data).astype(np.uint8).ravel(order="F")
        volume.mapper.SetMaskTypeToBinary()
        volume.mapper.SetMaskInput(mask)
    return volume


def render_case(case):
    small = case == "seq_small"
    shape, full_shape = (SMALL, SMALL) if small else (APP, APP)
    bounds = (0, shape[0] - 1, 0, shape[1] - 1, Z_CROP, shape[2] - 1)
    crop_shape = (shape[0], shape[1], shape[2] - Z_CROP)
    crop = make_volume(crop_shape, masked=False)  # 裁剪页：无缺失
    erased = make_volume(crop_shape, masked=case != "seq_app_nomask")
    zero_based = case == "seq_app_zerobased"
    clip = case != "seq_app_noclip"

    plotter = pv.Plotter(off_screen=True, window_size=(500, 500))
    print(f"[{case}] 裁剪页 (smart, extent={bounds})", flush=True)
    add_scene(plotter, crop, bounds, full_shape, masked=False, zero_based=False,
              clip=clip, small=small)
    plotter.render()
    plotter.screenshot(return_img=True)  # 读回帧缓冲，强制 GPU 收尾
    print(f"[{case}] 裁剪页渲染通过", flush=True)

    plotter.remove_actor("main_vol", render=False)
    print(f"[{case}] 裁空页 (gpu + mask, zero_based={zero_based})", flush=True)
    add_scene(plotter, erased, bounds, full_shape, masked=case != "seq_app_nomask",
              zero_based=zero_based, clip=clip, small=small)
    plotter.render()
    print(f"[{case}] 裁空页 render 通过", flush=True)
    plotter.screenshot(return_img=True)
    print(f"[{case}] 裁空页读回通过", flush=True)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                       ".local", "outputs", f"probe_{case}.png")
    plotter.screenshot(out)
    print(f"[{case}] PASS", flush=True)
    plotter.close()
    return 0


if __name__ == "__main__":
    sys.exit(render_case(sys.argv[1]))
