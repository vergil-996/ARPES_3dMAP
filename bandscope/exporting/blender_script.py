"""BandScope Blender background builder; runs with Blender's own Python."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Matrix, Vector
import numpy as np

try:
    import openvdb
except ImportError:
    import pyopenvdb as openvdb

PROGRESS_PREFIX = "BANDSCOPE_PROGRESS "
CAPABILITY_PREFIX = "BANDSCOPE_CAPABILITY "
ERROR_PREFIX = "BANDSCOPE_ERROR "
DATA_COLLECTION = "BandScope Data"
CAMERA_COLLECTION = "BandScope Camera"
ROOT_NAME = "BandScope Root"
VOLUME_NAME = "BandScope Volume"
CAMERA_NAME = "BandScope Camera"
BOX_LIGHT_BASE_WATTS = 60.0

# 八材质节点工厂需要的 Blender 节点与输入；任一缺失都明确报错，不静默退回平面发光。
MATERIAL_NODE_INPUTS = {
    "ShaderNodeBsdfTransparent": ("Color",),
    "ShaderNodeEmission": ("Color", "Strength"),
    "ShaderNodeBsdfPrincipled": ("Base Color", "Roughness"),
    "ShaderNodeBsdfTranslucent": ("Color", "Normal"),
    "ShaderNodeBsdfGlossy": ("Color", "Roughness"),
    "ShaderNodeBsdfGlass": ("Color", "Roughness", "IOR"),
    "ShaderNodeFresnel": ("IOR", "Normal"),
    "ShaderNodeLayerWeight": ("Blend", "Normal"),
    "ShaderNodeMixShader": ("Factor",),
    "ShaderNodeAddShader": (),
    "ShaderNodeVolumeAbsorption": ("Color", "Density"),
}
BOX_TINT_WHITE = (1.0, 1.0, 1.0)


def progress(percent, stage):
    print(PROGRESS_PREFIX + json.dumps({"percent": int(percent), "stage": str(stage)}, ensure_ascii=False), flush=True)


def capabilities():
    material = bpy.data.materials.new("BandScope capability probe")
    light_object = None
    light_data = None
    try:
        material.use_nodes = True
        node = material.node_tree.nodes.new("ShaderNodeVolumeCoefficients")
        required = {"Absorption Coefficients", "Scatter Coefficients", "Emission Coefficients"}
        missing = sorted(required.difference(node.inputs.keys()))
        if missing:
            raise RuntimeError("Volume Coefficients 缺少输入：" + ", ".join(missing))
        for bl_idname, inputs in MATERIAL_NODE_INPUTS.items():
            try:
                probe = material.node_tree.nodes.new(bl_idname)
            except RuntimeError as exc:
                raise RuntimeError(f"当前 Blender 缺少材质节点 {bl_idname}：{exc}") from exc
            absent = [name for name in inputs if name not in probe.inputs]
            if absent:
                raise RuntimeError(f"{bl_idname} 缺少输入：{', '.join(absent)}")
        volume = bpy.data.volumes.new("BandScope capability probe")
        try:
            space_items = {item.identifier for item in volume.render.bl_rna.properties["space"].enum_items}
            if "OBJECT" not in space_items:
                raise RuntimeError("当前 Blender 不支持对象空间体积密度。")
        finally:
            bpy.data.volumes.remove(volume)
        for grid_type in (openvdb.FloatGrid, openvdb.Vec3SGrid):
            grid = grid_type()
            if not hasattr(grid, "copyFromArray") or not hasattr(grid, "transform"):
                raise RuntimeError("OpenVDB 缺少 copyFromArray 或坐标变换能力。")
        light_data = bpy.data.lights.new("BandScope light probe", type="AREA")
        light_object = bpy.data.objects.new("BandScope light probe", light_data)
        bpy.context.scene.collection.objects.link(light_object)
        linking = getattr(light_object, "light_linking", None)
        receiver = getattr(linking, "receiver_collection", None)
        light_linking = linking is not None and hasattr(linking, "receiver_collection")
        return {
            "blender_version": bpy.app.version_string,
            "openvdb_module": openvdb.__name__,
            "numpy_version": np.__version__,
            "volume_coefficients": True,
            "object_space_density": True,
            "cycles": hasattr(bpy.context.scene, "cycles"),
            "box_materials": True,
            "material_nodes": sorted(MATERIAL_NODE_INPUTS),
            "light_linking": bool(light_linking),
        }
    finally:
        if light_object is not None:
            bpy.data.objects.remove(light_object, do_unlink=True)
        if light_data is not None:
            bpy.data.lights.remove(light_data)
        bpy.data.materials.remove(material)


def collection(name, parent):
    result = bpy.data.collections.new(name)
    parent.children.link(result)
    return result


def move_to_collection(obj, target):
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    target.objects.link(obj)


def emission_material(name, color):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*color, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def math_node(nodes, links, operation, left=None, right=None, name=None):
    node = nodes.new("ShaderNodeMath")
    node.operation = operation
    if name:
        node.name = name
        node.label = name
    if left is not None:
        if hasattr(left, "node"):
            links.new(left, node.inputs[0])
        else:
            node.inputs[0].default_value = left
    if right is not None:
        if hasattr(right, "node"):
            links.new(right, node.inputs[1])
        else:
            node.inputs[1].default_value = right
    return node.outputs[0]


def build_material(volume_bounds):
    material = bpy.data.materials.new("BandScope · absorption + emission")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (1100, 100)
    coefficients = nodes.new("ShaderNodeVolumeCoefficients")
    coefficients.location = (850, 100)
    coefficients.label = "No scattering: absorption + emission"
    coefficients.inputs["Scatter Coefficients"].default_value = (0.0, 0.0, 0.0)
    coefficients.inputs["Anisotropy"].default_value = 0.0
    links.new(coefficients.outputs["Volume"], output.inputs["Volume"])

    info = nodes.new("ShaderNodeVolumeInfo")
    info.location = (-1000, 350)
    info.label = "OpenVDB density + linear color"
    multiplier = nodes.new("ShaderNodeValue")
    multiplier.name = "Density multiplier"
    multiplier.label = "Density multiplier"
    multiplier.outputs[0].default_value = 1.0
    multiplier.location = (-1000, 100)
    density = math_node(nodes, links, "MULTIPLY", info.outputs["Density"], multiplier.outputs[0], "VDB density × multiplier")

    coordinates = nodes.new("ShaderNodeTexCoord")
    coordinates.location = (-1400, -350)
    coordinates.label = "Object-space scientific clipping"
    separate = nodes.new("ShaderNodeSeparateXYZ")
    separate.location = (-1200, -350)
    links.new(coordinates.outputs["Object"], separate.inputs[0])
    masks = []
    for axis, socket in enumerate(("X", "Y", "Z")):
        low = math_node(nodes, links, "GREATER_THAN", separate.outputs[socket], float(volume_bounds[axis * 2]), f"{axis} > data min")
        high = math_node(nodes, links, "LESS_THAN", separate.outputs[socket], float(volume_bounds[axis * 2 + 1]), f"{axis} < data max")
        masks.append(math_node(nodes, links, "MULTIPLY", low, high, f"{axis} scientific bounds"))
    mask = math_node(nodes, links, "MULTIPLY", masks[0], masks[1], "XY bounds")
    mask = math_node(nodes, links, "MULTIPLY", mask, masks[2], "Exact data and crop bounds")
    density = math_node(nodes, links, "MULTIPLY", density, mask, "Clipped absorption density")

    adjust = nodes.new("ShaderNodeMixRGB")
    adjust.name = "Color adjustment"
    adjust.label = "Color adjustment · linear RGB multiply"
    adjust.blend_type = "MULTIPLY"
    adjust.inputs[0].default_value = 1.0
    adjust.inputs[2].default_value = (1.0, 1.0, 1.0, 1.0)
    adjust.location = (-650, 500)
    links.new(info.outputs["Color"], adjust.inputs[1])

    absorption = nodes.new("ShaderNodeVectorMath")
    absorption.operation = "SCALE"
    absorption.location = (350, 50)
    absorption.label = "σ = -log(1-α) / unit distance"
    absorption.inputs[0].default_value = (1.0, 1.0, 1.0)
    links.new(density, absorption.inputs["Scale"])
    emission = nodes.new("ShaderNodeVectorMath")
    emission.operation = "SCALE"
    emission.location = (350, 350)
    emission.label = "Emission = σ × linear color"
    links.new(adjust.outputs[0], emission.inputs[0])
    links.new(density, emission.inputs["Scale"])
    links.new(absorption.outputs["Vector"], coefficients.inputs["Absorption Coefficients"])
    links.new(emission.outputs["Vector"], coefficients.inputs["Emission Coefficients"])

    for index, node in enumerate(node for node in nodes if node.bl_idname == "ShaderNodeMath"):
        node.location = (-650 + (index % 4) * 210, -100 - (index // 4) * 190)
    return material


def create_camera(metadata, target, *, name=None, offset=None, activate=True):
    settings = metadata["camera"]
    name = name or metadata.get("composition", {}).get("camera", CAMERA_NAME)
    data = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, data)
    target.objects.link(obj)
    basis = Matrix(settings["rotation_basis_columns"]).transposed().to_4x4()
    translation = Vector(settings["position"])
    if offset is not None:
        translation += Vector(offset)
    basis.translation = translation
    obj.matrix_world = basis
    data.type = settings["projection"]
    data.sensor_fit = "VERTICAL"
    data.sensor_height = float(settings["sensor_height_mm"])
    data.sensor_width = 36.0
    data.lens = float(settings["lens_mm"])
    data.ortho_scale = float(settings["ortho_scale"])
    data.clip_start = float(settings["clip_start"])
    data.clip_end = float(settings["clip_end"])
    data.passepartout_alpha = 1.0
    scene = bpy.context.scene
    if activate:
        scene.camera = obj
    scene.render.resolution_x, scene.render.resolution_y = metadata["resolution"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    return obj


def curve_arrow(name, start, end, material, target, parent):
    start, end = Vector(start), Vector(end)
    direction = (end - start).normalized()
    helper = Vector((0, 0, 1)) if abs(direction.z) < 0.9 else Vector((0, 1, 0))
    side = direction.cross(helper).normalized()
    other = direction.cross(side).normalized()
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = 0.004
    curve.bevel_resolution = 2
    segments = [(start, end)]
    for axis in (side, other):
        for sign in (-1.0, 1.0):
            segments.append((end, end - direction * 0.07 + sign * axis * 0.035))
    for points in segments:
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for point, value in zip(spline.points, points):
            point.co = (*value, 1.0)
    obj = bpy.data.objects.new(name, curve)
    target.objects.link(obj)
    obj.parent = parent
    curve.materials.append(material)
    return obj


def curve_box(name, bounds, material, target, parent):
    """Single editable curve object with the 12 edges of an axis-aligned box."""
    low = (float(bounds[0]), float(bounds[2]), float(bounds[4]))
    high = (float(bounds[1]), float(bounds[3]), float(bounds[5]))
    corners = [
        (x, y, z)
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]
    edges = ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
             (2, 6), (4, 5), (4, 6), (3, 7), (5, 7), (6, 7))
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = 0.003
    curve.bevel_resolution = 1
    curve.use_fill_caps = True
    for start_index, end_index in edges:
        start, end = corners[start_index], corners[end_index]
        if max(abs(end[axis] - start[axis]) for axis in range(3)) < 1.0e-9:
            continue
        spline = curve.splines.new("POLY")
        spline.points.add(1)
        spline.points[0].co = (*start, 1.0)
        spline.points[1].co = (*end, 1.0)
    obj = bpy.data.objects.new(name, curve)
    target.objects.link(obj)
    obj.parent = parent
    curve.materials.append(material)
    return obj


def text_object(name, body, location, size, material, target, parent, camera):
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = str(body)
    curve.align_x = "CENTER"
    curve.size = size
    curve.extrude = 0.0
    obj = bpy.data.objects.new(name, curve)
    target.objects.link(obj)
    obj.parent = parent
    obj.location = location
    obj.rotation_euler = camera.rotation_euler
    curve.materials.append(material)
    return obj


def create_references(metadata, target, parent, camera):
    suffix = metadata.get("composition", {}).get("name_suffix", "")
    reference = collection("Coordinate reference · editable" + suffix, target)
    materials = (
        emission_material("Axis X" + suffix, (0.65, 0.08, 0.08)),
        emission_material("Axis Y" + suffix, (0.08, 0.45, 0.12)),
        emission_material("Axis E" + suffix, (0.10, 0.25, 0.75)),
    )
    origin = Vector((-1.0, -1.0, -1.0))
    for axis, key in enumerate(("X", "Y", "E")):
        direction = Vector((0, 0, 0))
        direction[axis] = 1.0
        end = origin + direction * 2.0
        curve_arrow(f"{key} axis{suffix}", origin, end, materials[axis], reference, parent)
        coordinates = metadata["coordinates"][key]["values"]
        title = metadata["axis_titles"][axis] or key
        text_object(f"{key} axis name{suffix}", title, origin + direction * 1.0 + Vector((0, 0, 0.10)), 0.075,
                    materials[axis], reference, parent, camera)
        for role, point, value in (("min", origin, coordinates[0]), ("max", end, coordinates[-1])):
            text_object(f"{key} {role}{suffix}", f"{value:.6g}", point + Vector((0, 0, 0.055)), 0.05,
                        materials[axis], reference, parent, camera)
    for obj in reference.objects:
        obj.location += Vector(metadata["layout"]["coordinates"])
    return reference


def curve_line(name, points, radius, material, target, parent, *, cyclic=False):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = float(radius)
    curve.bevel_resolution = 2
    curve.use_fill_caps = True
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, value in zip(spline.points, points):
        point.co = (*value, 1.0)
    spline.use_cyclic_u = bool(cyclic)
    obj = bpy.data.objects.new(name, curve)
    target.objects.link(obj)
    obj.parent = parent
    curve.materials.append(material)
    return obj


def mesh_polygon(name, points, faces, material, target, parent):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    target.objects.link(obj)
    obj.parent = parent
    mesh.materials.append(material)
    return obj


def create_axis_indicator(metadata, target, parent, camera):
    settings = metadata["axis_indicator"]
    indicator = collection(settings["collection"], target)
    indicator.hide_render = False
    indicator.hide_viewport = False
    placement = settings["placement"]
    material = emission_material("Orientation marker · monochrome ink", settings["ink_color"])
    origin = Vector(settings["origin_world"])
    for axis in settings["axes"]:
        shaft = curve_line(axis["object"], (origin, Vector(axis["shaft_end_world"])),
                           placement["line_radius"], material, indicator, parent)
        head = mesh_polygon(axis["head_object"], axis["head_vertices_world"], axis["head_faces"],
                            material, indicator, parent)
        shaft["bandscope_orientation_role"] = axis["role"]
        shaft["bandscope_orientation_kind"] = axis["kind"]
        head["bandscope_orientation_role"] = axis["role"]
        for part in axis["label_parts"]:
            if not part["body"]:
                continue
            label = text_object(part["object"], part["body"], part["world"],
                                placement["text_size"] * float(part["size_factor"]),
                                material, indicator, parent, camera)
            label.data.extrude = 0.008
            label.data.align_y = "CENTER"
            label.data.shear = float(part["shear"])
    return indicator


def validate_axis_indicator(settings, parent_name):
    indicator = bpy.data.collections.get(settings["collection"])
    if indicator is None:
        raise RuntimeError("重新打开工程后未找到三轴方向标 Collection。")
    if indicator.hide_render or indicator.hide_viewport:
        raise RuntimeError("三轴方向标 Collection 默认被隐藏。")
    placement = settings["placement"]
    origin = Vector(settings["origin_world"])
    records = []

    def check_object(obj, expected_type):
        if obj is None or obj.type != expected_type:
            raise RuntimeError("三轴方向标的曲线、立体箭头或文字对象不完整。")
        if obj.parent is None or obj.parent.name != parent_name or indicator not in obj.users_collection:
            raise RuntimeError("三轴方向标对象的父对象或 Collection 不正确。")
        if obj.hide_render or obj.hide_viewport:
            raise RuntimeError("三轴方向标对象默认被隐藏。")
        emission = next(
            (node for node in obj.data.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeEmission"),
            None,
        )
        if emission is None or not np.allclose(
                emission.inputs["Color"].default_value[:3], settings["ink_color"], rtol=0.0, atol=1.0e-6):
            raise RuntimeError("三轴方向标不是图片导出使用的单色样式。")

    for axis in settings["axes"]:
        shaft = bpy.data.objects.get(axis["object"])
        head = bpy.data.objects.get(axis["head_object"])
        check_object(shaft, "CURVE")
        check_object(head, "MESH")
        if axis["kind"] == "arrow":
            if len(shaft.data.splines) != 1 or len(shaft.data.splines[0].points) != 2:
                raise RuntimeError(f"三轴方向标 {axis['role']} 的箭头杆不完整。")
            points = shaft.data.splines[0].points
            if ((Vector(points[0].co[:3]) - origin).length > 1.0e-6
                    or (Vector(points[1].co[:3]) - Vector(axis["shaft_end_world"])).length > 1.0e-6):
                raise RuntimeError(f"三轴方向标 {axis['role']} 的箭头杆几何不正确。")
            expected_vertices = axis["head_vertices_world"]
            if (len(head.data.vertices) != len(expected_vertices)
                    or len(head.data.polygons) != len(axis["head_faces"])
                    or any((vertex.co - Vector(expected)).length > 1.0e-6
                           for vertex, expected in zip(head.data.vertices, expected_vertices))
                    or np.linalg.matrix_rank(np.asarray(expected_vertices) - expected_vertices[0]) != 3):
                raise RuntimeError("方向标必须使用封闭立体圆锥箭头。")
        else:
            raise RuntimeError("方向标必须使用真实三维箭头。")
        label_objects = []
        for part in axis["label_parts"]:
            if not part["body"]:
                continue
            label = bpy.data.objects.get(part["object"])
            check_object(label, "FONT")
            if (label.data.body != part["body"] or (label.location - Vector(part["world"])).length > 1.0e-6
                    or abs(label.data.size - placement["text_size"] * float(part["size_factor"])) > 1.0e-6
                    or abs(label.data.shear - float(part["shear"])) > 1.0e-6):
                raise RuntimeError(f"三轴方向标 {axis['role']} 的短轴名不正确。")
            label_objects.append(label.name)
        records.append({
            "key": axis["key"],
            "role": axis["role"],
            "kind": axis["kind"],
            "object": shaft.name,
            "head_object": head.name,
            "label_objects": label_objects,
            "label": axis["label"],
            "visible": True,
        })
    if ({record["key"] for record in records} != {"X", "Y", "E"}
            or {record["role"] for record in records} != {"horizontal", "vertical", "depth"}):
        raise RuntimeError("三轴方向标必须完整包含水平、竖直和纵深角色。")
    directions = [np.asarray(axis["tip_world"]) - np.asarray(settings["origin_world"])
                  for axis in settings["axes"]]
    if not np.allclose(np.asarray(directions) @ np.asarray(directions).T, np.eye(3), atol=1e-6):
        raise RuntimeError("三轴方向标的真实方向不正交。")
    return {
        "collection": indicator.name,
        "style": settings["style"],
        "ink_color": list(settings["ink_color"]),
        "origin_world": list(origin),
        "axes": records,
        "all_present": True,
        "visible_by_default": True,
        "geometry_validated": True,
        "collection_hide_render": bool(indicator.hide_render),
        "collection_hide_viewport": bool(indicator.hide_viewport),
    }


def create_guide_frame(scene, target, parent):
    """Create the selected auxiliary box, independent of the host axis display switch."""
    settings = scene["guide_frame"]
    frame_collection = collection(settings["collection"], target)
    # 辅助线默认只在视口中显示；需要出现在最终渲染时，在 Outliner 中打开该 Collection
    # 的渲染开关即可。
    frame_collection.hide_render = True
    material = emission_material("Guide frame", (0.35, 0.35, 0.35))
    frame = curve_box(settings["object"], settings["bounds"], material, frame_collection, parent)
    frame.location = Vector(scene["layout"]["guide_frame"])
    frame["bandscope_guide_frame"] = True
    frame["bounds"] = [float(value) for value in settings["bounds"]]
    frame["axis_frame_bounds"] = [float(value) for value in settings["axis_frame_bounds"]]
    frame["margin"] = float(settings["margin"])
    frame["purpose"] = "Detached auxiliary frame; local dimensions enclose the original data bounds."
    return frame


def _new_box_material(name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.clear()
    if hasattr(material, "surface_render_method"):
        # Cycles 忽略该 Eevee 设置；保留用于在 Eevee 中直接查看导出材质。
        material.surface_render_method = "DITHERED"
    return material


def _mix_shader(nodes, links, first, second, factor, name=None, label=None):
    mix = nodes.new("ShaderNodeMixShader")
    if name:
        mix.name = name
    if label:
        mix.label = label
    if hasattr(factor, "node"):
        links.new(factor, mix.inputs[0])
    else:
        mix.inputs[0].default_value = float(factor)
    links.new(first, mix.inputs[1])
    links.new(second, mix.inputs[2])
    return mix


def _add_shader(nodes, links, first, second):
    node = nodes.new("ShaderNodeAddShader")
    links.new(first, node.inputs[0])
    links.new(second, node.inputs[1])
    return node


def _emission_shader(nodes, color, strength):
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*color, 1.0)
    emission.inputs["Strength"].default_value = float(strength)
    return emission


def _tinted(color, strength):
    strength = max(0.0, min(1.0, float(strength)))
    return tuple(
        1.0 - strength + strength * float(value) for value in tuple(color)[:3]
    )


def _principled_shader(nodes, color, parameters):
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Metallic"].default_value = 0.0
    shader.inputs["Roughness"].default_value = float(parameters["roughness"])
    if "Transmission Weight" in shader.inputs:
        shader.inputs["Transmission Weight"].default_value = 0.0
    elif "Transmission" in shader.inputs:
        shader.inputs["Transmission"].default_value = 0.0
    for name in ("Specular IOR Level", "Specular"):
        if name in shader.inputs:
            shader.inputs[name].default_value = float(parameters["specular"])
            break
    return shader


def _compensated_shader(nodes, links, branch, color, parameters):
    """自发光补偿只进入非透明分支内部；补偿为 0 时不创建 Emission 节点。"""
    compensation = float(parameters.get("emission_compensation", 0.0))
    if compensation <= 0.0:
        return branch
    emission = _emission_shader(nodes, color, compensation)
    emission.name = "BandScope emission compensation"
    emission.label = "Emission compensation (inside opaque branch)"
    return _add_shader(nodes, links, branch, emission.outputs[0]).outputs[0]


def create_box_material(settings, color):
    """按材质 ID 分派节点工厂；八块薄板各自独立材质和节点树。"""
    material_id = str(settings["material_id"])
    parameters = settings["parameters"]
    material = _new_box_material(settings["material"])
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    color = tuple(float(value) for value in tuple(color)[:3])
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (650, 100)
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (-650, 260)
    transparent.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)

    if material_id == "flat_emission":
        emission = _emission_shader(nodes, color, float(parameters["emission_strength"]))
        emission.location = (-650, -60)
        mix = _mix_shader(
            nodes, links, transparent.outputs[0], emission.outputs[0],
            float(parameters["opacity"]), "BandScope opacity",
            f"Opacity = {float(parameters['opacity']):.2f}; single surface; no refraction",
        )
        mix.location = (0, 120)
        links.new(mix.outputs[0], output.inputs["Surface"])

    elif material_id in {"matte", "satin"}:
        shader = _principled_shader(nodes, color, parameters)
        shader.name = "BandScope Principled surface"
        shader.label = f"{material_id} · Transmission 0"
        shader.location = (-650, -60)
        branch = _compensated_shader(nodes, links, shader.outputs[0], color, parameters)
        mix = _mix_shader(
            nodes, links, transparent.outputs[0], branch,
            float(parameters["opacity"]), "BandScope opacity",
            f"Opacity = {float(parameters['opacity']):.2f}; transmission 0",
        )
        mix.location = (0, 120)
        links.new(mix.outputs[0], output.inputs["Surface"])

    elif material_id == "holographic":
        emission = _emission_shader(nodes, color, float(parameters["emission_strength"]))
        emission.location = (-650, -60)
        front = _mix_shader(
            nodes, links, transparent.outputs[0], emission.outputs[0],
            float(parameters["opacity"]), "BandScope opacity",
            f"Front opacity = {float(parameters['opacity']):.2f}",
        )
        front.location = (-250, 260)
        edge = _mix_shader(
            nodes, links, transparent.outputs[0], emission.outputs[0],
            float(parameters["edge_opacity"]), "BandScope edge opacity",
            f"Grazing opacity = {float(parameters['edge_opacity']):.2f}",
        )
        edge.location = (-250, 40)
        fresnel = nodes.new("ShaderNodeFresnel")
        fresnel.location = (-650, -320)
        fresnel.name = "BandScope fresnel"
        fresnel.inputs["IOR"].default_value = float(parameters["ior"])
        geometry = nodes.new("ShaderNodeNewGeometry")
        geometry.location = (-650, -520)
        geometry.name = "BandScope geometry"
        # 封闭薄板的背面在 Cycles 中会按原始法线参与 Fresnel，导致整个背面像掠射角
        # 一样发光。用 Backfacing 把背面权重压回 0，只让正面与真实边缘发光。
        front_facing = math_node(
            nodes, links, "SUBTRACT", 1.0, geometry.outputs["Backfacing"],
            "BandScope front facing",
        )
        weight = math_node(
            nodes, links, "POWER", fresnel.outputs[0], float(parameters["fresnel_power"]),
            "BandScope fresnel falloff",
        )
        weight = math_node(
            nodes, links, "MULTIPLY", weight, front_facing,
            "BandScope holographic weight",
        )
        final = _mix_shader(
            nodes, links, front.outputs[0], edge.outputs[0], weight,
            "BandScope holographic mix", "Fresnel front-to-grazing blend; no refraction",
        )
        final.location = (250, 140)
        links.new(final.outputs[0], output.inputs["Surface"])

    elif material_id == "data_translucent":
        translucent = nodes.new("ShaderNodeBsdfTranslucent")
        translucent.location = (-650, -60)
        translucent.inputs["Color"].default_value = (
            *_tinted(color, parameters["tint_strength"]), 1.0
        )
        mix = _mix_shader(
            nodes, links, transparent.outputs[0], translucent.outputs[0],
            float(parameters["translucency"]), "BandScope translucency",
            "Diffuse transmission from the emitting data volume; the plate does not emit",
        )
        mix.location = (0, 120)
        links.new(mix.outputs[0], output.inputs["Surface"])

    elif material_id == "transmission_filter":
        tint = _tinted(color, parameters["filter_strength"])
        transparent.name = "BandScope transmission filter"
        transparent.label = (
            f"Tint = {tuple(round(value, 3) for value in tint)}; "
            "both surfaces tint transmitted light; no refraction"
        )
        transparent.inputs["Color"].default_value = (*tint, 1.0)
        links.new(transparent.outputs[0], output.inputs["Surface"])

    elif material_id == "reflection_glass":
        glossy = nodes.new("ShaderNodeBsdfGlossy")
        glossy.name = "BandScope Glossy reflection"
        glossy.location = (-650, -60)
        glossy.inputs["Color"].default_value = (*color, 1.0)
        glossy.inputs["Roughness"].default_value = float(parameters["roughness"])
        branch = _compensated_shader(nodes, links, glossy.outputs[0], color, parameters)
        fresnel = nodes.new("ShaderNodeFresnel")
        fresnel.name = "BandScope fresnel"
        fresnel.location = (-650, -360)
        fresnel.inputs["IOR"].default_value = float(parameters["ior"])
        weight = math_node(
            nodes, links, "MULTIPLY", fresnel.outputs[0],
            float(parameters["reflection_strength"]), "BandScope reflection weight",
        )
        weight = math_node(nodes, links, "MINIMUM", weight, 1.0, "BandScope reflection clamp")
        mix = _mix_shader(
            nodes, links, transparent.outputs[0], branch, weight,
            "BandScope reflection mix", "Fresnel reflection only; no refraction",
        )
        mix.location = (0, 120)
        links.new(mix.outputs[0], output.inputs["Surface"])

    elif material_id == "optical_glass":
        glass = nodes.new("ShaderNodeBsdfGlass")
        glass.name = "BandScope Glass"
        glass.location = (-650, -60)
        glass.inputs["Color"].default_value = (*color, 1.0)
        glass.inputs["Roughness"].default_value = float(parameters["roughness"])
        glass.inputs["IOR"].default_value = float(parameters["ior"])
        surface = _compensated_shader(nodes, links, glass.outputs[0], color, parameters)
        links.new(surface, output.inputs["Surface"])
        absorption = nodes.new("ShaderNodeVolumeAbsorption")
        absorption.name = "BandScope Volume Absorption"
        absorption.label = "Thickness absorption between the two closed interfaces"
        absorption.location = (-650, -420)
        absorption.inputs["Color"].default_value = (*color, 1.0)
        absorption.inputs["Density"].default_value = float(parameters["absorption_density"])
        links.new(absorption.outputs[0], output.inputs["Volume"])

    else:
        raise RuntimeError(f"未知薄板材质 ID：{material_id}")

    material["bandscope_material_id"] = material_id
    material["bandscope_material_label"] = str(settings.get("material_label", material_id))
    material["bandscope_parameters"] = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
    return material


def _box_light_layout(scene, settings):
    camera = scene["camera"]
    right, up, backward = (Vector(camera["rotation_basis_columns"][index]) for index in range(3))
    center = Vector(settings["center"])
    size = Vector(settings["size"])
    distance = max(1.6, max(size.length, 0.2) * 1.25)
    position = center + backward * distance + up * (distance * 0.25)
    area = max(size.x, size.y, 0.2) * 0.9
    energy = BOX_LIGHT_BASE_WATTS * distance * distance
    return position, float(area), float(energy), float(distance)


def create_box_presets(scene, target, parent):
    objects = []
    for settings in scene.get("box_presets") or ():
        center = Vector(settings["center"])
        half = Vector(settings["size"]) * 0.5
        points = [
            tuple(center + Vector((x * half.x, y * half.y, z * half.z)))
            for x, y, z in ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))
        ]
        faces = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
        material = create_box_material(settings, settings["color"])
        obj = mesh_polygon(settings["object"], points, faces, material, target, parent)
        obj["bandscope_box_preset"] = settings["preset"]
        obj["bandscope_material_id"] = settings["material_id"]
        obj["bandscope_opacity"] = float(settings["opacity"]) if settings.get("opacity") is not None else -1.0
        obj["bandscope_thickness"] = float(settings["thickness"])
        obj["bandscope_box_layout"] = str(settings.get("layout", "separated_right"))
        objects.append(obj)
    return objects


def create_box_lights(scene, target, parent):
    """为需要外部照明的薄板创建中性 Area 灯，并用 Cycles 灯光链接限制接收者。"""
    lighting = scene.get("box_lighting") or {}
    if not lighting.get("enabled"):
        return []
    boxes = {settings["preset"]: settings for settings in scene.get("box_presets") or ()}
    receiver = collection(lighting["receiver_collection"], target)
    light_collection = collection(lighting["collection"], target)
    objects = []
    for record in lighting.get("lights") or ():
        settings = boxes.get(record["preset"])
        if settings is None:
            raise RuntimeError(f"灯光记录引用了未创建的薄板 {record['preset']}。")
        box_object = bpy.data.objects.get(settings["object"])
        if box_object is None:
            raise RuntimeError(f"灯光找不到对应薄板对象 {settings['object']}。")
        # 只把需要外部照明的薄板放进接收者 Collection；数据体和其它薄板不被直接照亮。
        move_to_collection(box_object, receiver)
        position, area, energy, distance = _box_light_layout(scene, settings)
        data = bpy.data.lights.new(record["object"], type="AREA")
        data.shape = "SQUARE"
        data.size = area
        data.energy = energy
        obj = bpy.data.objects.new(record["object"], data)
        light_collection.objects.link(obj)
        obj.parent = parent
        obj.location = position
        obj.rotation_euler = (
            Vector(settings["center"]) - position
        ).to_track_quat("-Z", "Y").to_euler()
        obj.light_linking.receiver_collection = receiver
        obj["bandscope_box_light"] = record["preset"]
        obj["bandscope_receiver_collection"] = receiver.name
        obj["bandscope_light_energy"] = energy
        obj["bandscope_light_distance"] = distance
        obj["bandscope_material_id"] = settings["material_id"]
        objects.append(obj)
    return objects


def _upstream_nodes(socket, seen=None):
    if seen is None:
        seen = set()
    found = []
    for link in socket.links:
        node = link.from_node
        if node.name in seen:
            continue
        seen.add(node.name)
        found.append(node)
        for input_socket in node.inputs:
            if input_socket.type == "SHADER":
                found.extend(_upstream_nodes(input_socket, seen))
    return found


def _upstream_types(socket):
    return {node.bl_idname for node in _upstream_nodes(socket)}


def _upstream_any(socket, seen=None):
    """沿所有输入（含数值）向上遍历；用于检查混在数学节点里的 Backfacing 等驱动。"""
    if seen is None:
        seen = set()
    found = []
    for link in socket.links:
        node = link.from_node
        if node.name in seen:
            continue
        seen.add(node.name)
        found.append(node)
        for input_socket in node.inputs:
            found.extend(_upstream_any(input_socket, seen))
    return found


def _failure(settings, message):
    raise RuntimeError(f"长方体预设 {settings['preset']}（{settings['material_id']}）：{message}")


def _required_mix(settings, nodes, name):
    mix = nodes.get(name)
    if mix is None or mix.bl_idname != "ShaderNodeMixShader":
        _failure(settings, f"缺少透明混合节点 {name}")
    return mix


def _check_factor(settings, mix, expected, label):
    actual = float(mix.inputs[0].default_value)
    if abs(actual - float(expected)) > 1e-6:
        _failure(settings, f"{label} {actual:.6f} 与配置 {float(expected):.6f} 不一致")


def _emission_record(settings, nodes, back_nodes=()):
    emission_nodes = [node for node in nodes if node.bl_idname == "ShaderNodeEmission"]
    back_names = {node.name for node in back_nodes}
    inside = any(node.name in back_names for node in emission_nodes)
    return {
        "node_count": len(emission_nodes),
        "effective": bool(emission_nodes),
        "inside_transparent_branch": bool(inside),
        "strengths": [float(node.inputs["Strength"].default_value) for node in emission_nodes],
    }


def validate_box_material(settings, material):
    material_id = settings["material_id"]
    parameters = settings["parameters"]
    if material.get("bandscope_material_id") != material_id:
        _failure(settings, "材质数据块记录的材质 ID 与配置不一致")
    nodes = list(material.node_tree.nodes)
    if not any(node.bl_idname == "ShaderNodeOutputMaterial" for node in nodes):
        _failure(settings, "材质缺少输出节点")
    record = {
        "material_id": material_id,
        "material_label": material.get("bandscope_material_label", ""),
        "node_types": sorted({node.bl_idname for node in nodes}),
        "parameters": dict(parameters),
    }
    output = next(node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial")

    if material_id == "flat_emission":
        mix = _required_mix(settings, material.node_tree.nodes, "BandScope opacity")
        _check_factor(settings, mix, parameters["opacity"], "单表面不透明度")
        front, back = _upstream_nodes(mix.inputs[1]), _upstream_nodes(mix.inputs[2])
        if "ShaderNodeBsdfTransparent" not in {node.bl_idname for node in front}:
            _failure(settings, "透明分支不是 Transparent BSDF")
        back_types = {node.bl_idname for node in back}
        if "ShaderNodeEmission" not in back_types:
            _failure(settings, "自发光不在非透明分支内")
        emission = next(node for node in back if node.bl_idname == "ShaderNodeEmission")
        if abs(float(emission.inputs["Strength"].default_value) - float(parameters["emission_strength"])) > 1e-6:
            _failure(settings, "发光强度与配置不一致")
        record["opacity"] = float(parameters["opacity"])
        record["emission"] = _emission_record(settings, nodes, back)

    elif material_id in {"matte", "satin"}:
        mix = _required_mix(settings, material.node_tree.nodes, "BandScope opacity")
        _check_factor(settings, mix, parameters["opacity"], "混合不透明度")
        front, back = _upstream_nodes(mix.inputs[1]), _upstream_nodes(mix.inputs[2])
        if "ShaderNodeBsdfTransparent" not in {node.bl_idname for node in front}:
            _failure(settings, "透明分支不是 Transparent BSDF")
        principled = next((node for node in back if node.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        if principled is None:
            _failure(settings, "非透明分支缺少 Principled BSDF")
        if abs(float(principled.inputs["Roughness"].default_value) - float(parameters["roughness"])) > 1e-6:
            _failure(settings, "Principled 粗糙度与配置不一致")
        transmission = principled.inputs.get("Transmission Weight") or principled.inputs.get("Transmission")
        if transmission is None or abs(float(transmission.default_value)) > 1e-9:
            _failure(settings, "Principled 透射不为 0")
        compensation = float(parameters["emission_compensation"])
        emission = _emission_record(settings, nodes, back)
        if compensation <= 0.0 and emission["node_count"]:
            _failure(settings, "补偿为 0 却存在薄板自发光节点")
        if compensation > 0.0 and (not emission["inside_transparent_branch"]
                                   or any(abs(value - compensation) > 1e-6 for value in emission["strengths"])):
            _failure(settings, "自发光补偿没有放在非透明分支内或强度不符")
        record["opacity"] = float(parameters["opacity"])
        record["roughness"] = float(parameters["roughness"])
        record["emission"] = emission

    elif material_id == "holographic":
        front = _required_mix(settings, material.node_tree.nodes, "BandScope opacity")
        edge = _required_mix(settings, material.node_tree.nodes, "BandScope edge opacity")
        final = _required_mix(settings, material.node_tree.nodes, "BandScope holographic mix")
        _check_factor(settings, front, parameters["opacity"], "正面不透明度")
        _check_factor(settings, edge, parameters["edge_opacity"], "掠射角不透明度")
        for name, mix in (("正面", front), ("掠射角", edge)):
            types = _upstream_types(mix.inputs[2])
            if "ShaderNodeEmission" not in types:
                _failure(settings, f"{name}分支缺少发光")
            if "ShaderNodeBsdfTransparent" not in _upstream_types(mix.inputs[1]):
                _failure(settings, f"{name}分支缺少 Transparent")
        links = final.inputs[0].links
        if not links:
            _failure(settings, "全息混合没有 Fresnel 权重")
        weight = links[0].from_node
        if weight.name != "BandScope holographic weight" or weight.operation != "MULTIPLY":
            _failure(settings, "全息混合权重没有按 Backfacing 屏蔽背面")
        power = next(
            (node for node in _upstream_nodes(weight.inputs[0])
             if node.bl_idname == "ShaderNodeMath" and node.operation == "POWER"),
            None,
        )
        if power is None:
            _failure(settings, "全息混合权重不是 Fresnel 幂函数")
        if abs(float(power.inputs[1].default_value) - float(parameters["fresnel_power"])) > 1e-6:
            _failure(settings, "Fresnel 幂与配置不一致")
        if "ShaderNodeNewGeometry" not in {node.bl_idname for node in _upstream_any(weight.inputs[1])}:
            _failure(settings, "全息权重缺少 Front/Backfacing 屏蔽")
        fresnel = next((node for node in nodes if node.bl_idname == "ShaderNodeFresnel"), None)
        if fresnel is None or abs(float(fresnel.inputs["IOR"].default_value) - float(parameters["ior"])) > 1e-6:
            _failure(settings, "Fresnel 节点缺失或 IOR 与配置不一致")
        if any(node.bl_idname == "ShaderNodeAddShader" for node in nodes):
            _failure(settings, "全息材质不得用 Add Shader 在最终混合之后叠加发光")
        record["opacity"] = float(parameters["opacity"])
        record["edge_opacity"] = float(parameters["edge_opacity"])
        record["emission"] = _emission_record(settings, nodes, _upstream_nodes(final.inputs[2]))

    elif material_id == "data_translucent":
        mix = _required_mix(settings, material.node_tree.nodes, "BandScope translucency")
        _check_factor(settings, mix, parameters["translucency"], "漫透射比例")
        if "ShaderNodeBsdfTranslucent" not in _upstream_types(mix.inputs[2]):
            _failure(settings, "非透明分支不是 Translucent BSDF")
        if "ShaderNodeBsdfTransparent" not in _upstream_types(mix.inputs[1]):
            _failure(settings, "透明分支不是 Transparent BSDF")
        emission = _emission_record(settings, nodes)
        if emission["node_count"]:
            _failure(settings, "数据漫透射不得存在薄板自发光")
        record["translucency"] = float(parameters["translucency"])
        record["emission"] = emission

    elif material_id == "transmission_filter":
        transparent = next((node for node in nodes if node.bl_idname == "ShaderNodeBsdfTransparent"), None)
        if transparent is None:
            _failure(settings, "缺少 Transparent BSDF")
        if transparent.name != "BandScope transmission filter":
            _failure(settings, "透射窗节点命名不符合契约")
        surface = output.inputs["Surface"].links
        if not surface or surface[0].from_node != transparent:
            _failure(settings, "透射窗不是直接由 Transparent 输出")
        expected = _tinted(tuple(settings["color"]), parameters["filter_strength"])
        actual = tuple(float(value) for value in transparent.inputs["Color"].default_value[:3])
        if any(abs(actual[index] - expected[index]) > 1e-6 for index in range(3)):
            _failure(settings, "滤色强度没有写入 Transparent 颜色")
        emission = _emission_record(settings, nodes)
        if emission["node_count"]:
            _failure(settings, "透射滤色不得存在薄板自发光")
        record["filter_strength"] = float(parameters["filter_strength"])
        record["filter_color"] = list(actual)
        record["emission"] = emission

    elif material_id == "reflection_glass":
        mix = _required_mix(settings, material.node_tree.nodes, "BandScope reflection mix")
        front, back = _upstream_nodes(mix.inputs[1]), _upstream_nodes(mix.inputs[2])
        if "ShaderNodeBsdfTransparent" not in {node.bl_idname for node in front}:
            _failure(settings, "透明分支不是 Transparent BSDF")
        glossy = next(
            (node for node in back
             if node.bl_idname in {"ShaderNodeBsdfGlossy", "ShaderNodeBsdfAnisotropic"}),
            None,
        )
        if glossy is None:
            _failure(settings, "反射分支缺少 Glossy BSDF")
        if abs(float(glossy.inputs["Roughness"].default_value) - float(parameters["roughness"])) > 1e-6:
            _failure(settings, "反射粗糙度与配置不一致")
        fresnel = next((node for node in nodes if node.bl_idname == "ShaderNodeFresnel"), None)
        if fresnel is None or abs(float(fresnel.inputs["IOR"].default_value) - float(parameters["ior"])) > 1e-6:
            _failure(settings, "Fresnel 节点缺失或 IOR 与配置不一致")
        weight = material.node_tree.nodes.get("BandScope reflection weight")
        if weight is None or abs(float(weight.inputs[1].default_value) - float(parameters["reflection_strength"])) > 1e-6:
            _failure(settings, "反射强度与配置不一致")
        compensation = float(parameters["emission_compensation"])
        emission = _emission_record(settings, nodes, back)
        if compensation <= 0.0 and emission["node_count"]:
            _failure(settings, "补偿为 0 却存在薄板自发光节点")
        if compensation > 0.0 and not emission["inside_transparent_branch"]:
            _failure(settings, "自发光补偿没有放在非透明分支内")
        record["roughness"] = float(parameters["roughness"])
        record["ior"] = float(parameters["ior"])
        record["emission"] = emission

    elif material_id == "optical_glass":
        glass = material.node_tree.nodes.get("BandScope Glass")
        if glass is None or glass.bl_idname != "ShaderNodeBsdfGlass":
            _failure(settings, "缺少 Glass BSDF")
        if abs(float(glass.inputs["IOR"].default_value) - float(parameters["ior"])) > 1e-6:
            _failure(settings, "Glass IOR 与配置不一致")
        if abs(float(glass.inputs["Roughness"].default_value) - float(parameters["roughness"])) > 1e-6:
            _failure(settings, "Glass 粗糙度与配置不一致")
        if "ShaderNodeBsdfGlass" not in _upstream_types(output.inputs["Surface"]):
            _failure(settings, "表面输出没有经过 Glass BSDF")
        volume_links = output.inputs["Volume"].links
        if not volume_links or volume_links[0].from_node.bl_idname != "ShaderNodeVolumeAbsorption":
            _failure(settings, "缺少厚度吸收 Volume Absorption")
        absorption = volume_links[0].from_node
        if abs(float(absorption.inputs["Density"].default_value) - float(parameters["absorption_density"])) > 1e-6:
            _failure(settings, "吸收密度与配置不一致")
        compensation = float(parameters["emission_compensation"])
        emission = _emission_record(settings, nodes)
        if compensation <= 0.0 and emission["node_count"]:
            _failure(settings, "补偿为 0 却存在薄板自发光节点")
        record["ior"] = float(parameters["ior"])
        record["roughness"] = float(parameters["roughness"])
        record["absorption_density"] = float(parameters["absorption_density"])
        record["emission"] = emission

    else:
        _failure(settings, f"未知材质 ID {material_id}")

    return record


def _mesh_center_size(obj):
    coordinates = [vertex.co for vertex in obj.data.vertices]
    low = Vector(tuple(min(value[axis] for value in coordinates) for axis in range(3)))
    high = Vector(tuple(max(value[axis] for value in coordinates) for axis in range(3)))
    return (low + high) * 0.5, high - low


def validate_box_presets(scene, parent, presets):
    records = []
    for settings in scene.get("box_presets") or ():
        if not _box_enabled(settings, presets):
            continue
        obj = bpy.data.objects.get(settings["object"])
        if obj is None or obj.type != "MESH" or obj.parent != parent:
            raise RuntimeError(f"长方体预设 {settings['preset']} 缺失或父对象不正确。")
        if len(obj.data.vertices) != 8 or len(obj.data.polygons) != 6:
            raise RuntimeError(f"长方体预设 {settings['preset']} 不是完整六面网格。")
        if len(obj.data.materials) != 1 or obj.data.materials[0].name != settings["material"]:
            raise RuntimeError(f"长方体预设 {settings['preset']} 没有独立材质。")
        material = obj.data.materials[0]
        material_record = validate_box_material(settings, material)
        if obj.get("bandscope_box_layout") != settings.get("layout", "separated_right"):
            _failure(settings, "对象记录的布局与配置不一致")
        center, size = _mesh_center_size(obj)
        if (center - Vector(settings["center"])).length > 1e-6:
            _failure(settings, "薄板中心与布局配置不一致")
        if (size - Vector(settings["size"])).length > 1e-6:
            _failure(settings, "薄板尺寸与布局配置不一致")
        if abs(size.z - float(settings["thickness"])) > 1e-6:
            _failure(settings, "薄板厚度被改变")
        data_bounds = np.asarray(scene["blender_grid"]["material_clip_bounds"], dtype=float).reshape(3, 2)
        low = np.asarray(tuple(center), dtype=float) - np.asarray(tuple(size), dtype=float) * 0.5
        high = np.asarray(tuple(center), dtype=float) + np.asarray(tuple(size), dtype=float) * 0.5
        intersects = bool(
            np.all(low <= data_bounds[:, 1] + 1e-9) and np.all(high >= data_bounds[:, 0] - 1e-9)
        )
        if settings.get("layout") == "data_aligned" and settings.get("intersects_data") and not intersects:
            _failure(settings, "同轴布局报告的相交关系与实际几何不一致")
        record = {
            "preset": settings["preset"], "object": obj.name, "material": material.name,
            "mesh": obj.data.name, "independent_material": True, "full_cuboid": True,
            "opacity": material_record.get("opacity"),
            "layout": settings.get("layout", "separated_right"),
            "center": [float(value) for value in center],
            "size": [float(value) for value in size],
            "thickness": float(size.z),
            "intersects_data": intersects,
            "requires_light": bool(settings.get("requires_light")),
            "light": None,
        }
        record.update({
            key: value for key, value in material_record.items()
            if key not in {"opacity", "parameters", "material_label"}
        })
        records.append(record)
    return records


def validate_box_lights(scene, parent, presets, box_records):
    lighting = scene.get("box_lighting") or {}
    boxes = {settings["preset"]: settings for settings in scene.get("box_presets") or ()}
    enabled_lit = [
        settings["preset"] for settings in scene.get("box_presets") or ()
        if _box_enabled(settings, presets) and settings.get("requires_light")
    ]
    data_collection = bpy.data.collections.get((scene.get("composition") or {}).get("data_collection", ""))
    if data_collection is None:
        raise RuntimeError(f"条目 {scene.get('entry_id')} 的数据 Collection 缺失。")
    records = []
    if not enabled_lit:
        leftover = [obj for obj in data_collection.all_objects if obj.type == "LIGHT"]
        if leftover:
            raise RuntimeError(f"条目 {scene.get('entry_id')} 不需要灯光却创建了 LIGHT 对象。")
        if bpy.data.collections.get(lighting.get("collection", "")) is not None:
            raise RuntimeError(f"条目 {scene.get('entry_id')} 不需要灯光却创建了灯组 Collection。")
        return records
    if not lighting.get("enabled"):
        raise RuntimeError(f"条目 {scene.get('entry_id')} 需要辅助光但配置未启用。")
    light_collection = bpy.data.collections.get(lighting["collection"])
    receiver = bpy.data.collections.get(lighting["receiver_collection"])
    if light_collection is None or receiver is None:
        raise RuntimeError(f"条目 {scene.get('entry_id')} 的灯组或接收者 Collection 缺失。")
    if receiver.name not in {child.name for child in data_collection.children}:
        raise RuntimeError(f"条目 {scene.get('entry_id')} 的接收者 Collection 不在数据 Collection 内。")
    expected_boxes = {record["object"] for record in box_records if record["preset"] in enabled_lit}
    actual_boxes = {obj.name for obj in receiver.objects}
    if actual_boxes != expected_boxes:
        raise RuntimeError(
            f"条目 {scene.get('entry_id')} 的灯光接收者与需要照明的薄板不一致："
            f"{sorted(actual_boxes)} != {sorted(expected_boxes)}"
        )
    expected_lights = {
        record["object"]: record for record in (lighting.get("lights") or ())
        if record.get("preset") in enabled_lit
    }
    actual_lights = {obj.name for obj in light_collection.objects}
    if actual_lights != set(expected_lights):
        raise RuntimeError(f"条目 {scene.get('entry_id')} 的灯组对象与配置不一致。")
    for name, config in expected_lights.items():
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "LIGHT" or obj.parent != parent:
            raise RuntimeError(f"条目 {scene.get('entry_id')} 的辅助灯 {name} 缺失或父对象错误。")
        if obj.data.type != "AREA" or float(obj.data.energy) <= 0.0 or float(obj.data.size) <= 0.0:
            raise RuntimeError(f"条目 {scene.get('entry_id')} 的辅助灯 {name} 不是有效 Area 灯。")
        if obj.light_linking.receiver_collection != receiver:
            raise RuntimeError(f"条目 {scene.get('entry_id')} 的辅助灯 {name} 没有链接到接收者 Collection。")
        if obj.get("bandscope_box_light") != config["preset"]:
            raise RuntimeError(f"条目 {scene.get('entry_id')} 的辅助灯 {name} 绑定薄板错误。")
        records.append({
            "object": obj.name, "preset": config["preset"],
            "type": obj.data.type, "energy": float(obj.data.energy), "size": float(obj.data.size),
            "receiver_collection": receiver.name,
            "receiver_objects": sorted(actual_boxes),
            "parent": parent.name,
        })
        for box in box_records:
            if box["preset"] == config["preset"]:
                box["light"] = obj.name
    return records


def write_vdb(scene, arrays, vdb_path):
    density = openvdb.FloatGrid()
    density.name = "density"
    density.gridClass = openvdb.GridClass.FOG_VOLUME
    color = openvdb.Vec3SGrid()
    color.name = "color"
    matrix = [[0.0] * 4 for _ in range(4)]
    for axis in range(3):
        matrix[axis][axis] = float(scene["blender_grid"]["spacing"][axis])
    matrix[3] = [*scene["blender_grid"]["origin"], 1.0]
    transform = openvdb.createLinearTransform(matrix)
    density.transform = transform
    color.transform = transform
    values = np.ascontiguousarray(arrays["density"], dtype=np.float32)
    colors = np.ascontiguousarray(arrays["display_color"], dtype=np.float32)
    if values.ndim != 3 or colors.shape != values.shape + (3,):
        raise RuntimeError("NPZ 密度或颜色数组形状无效。")
    density.copyFromArray(values)
    color.copyFromArray(colors)
    openvdb.write(str(vdb_path), grids=[density, color], metadata={
        "creator": "BandScope",
        "bandscope_schema": int(scene["schema_version"]),
    })
    return values.shape


def configure_scene(samples=32):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = max(32, int(samples))
    scene.cycles.use_denoising = True
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.display_settings.display_device = "sRGB"
    return scene


def frame_data_viewports(data_collection, volume):
    """Fit all component bounds without relying on a live viewport redraw."""
    bpy.context.view_layer.update()
    objects = (list(data_collection.all_objects)
               if hasattr(data_collection, "all_objects") else list(data_collection))
    points = [obj.matrix_world @ Vector(corner) for obj in objects
              if obj.type not in {"EMPTY", "CAMERA", "LIGHT"} for corner in obj.bound_box]
    if not points:
        raise RuntimeError("没有可用于视口取景的体数据或预设对象。")
    low = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    center = (low + high) * 0.5
    radius = max((point - center).length for point in points)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            region = next((region for region in area.regions if region.type == "WINDOW"), None)
            width = region.width if region and region.width > 1 else max(1, area.width)
            height = region.height if region and region.height > 1 else max(1, area.height)
            aspect = width / height
            # Conservative 32 mm sensor fit; use both dimensions so portrait and
            # wide viewports include text and the marker above the data volume.
            tan_y = 16.0 / max(space.lens, 1.0) * min(1.0, 1.0 / aspect)
            tan_x = tan_y * aspect
            view = space.region_3d
            rotation = view.view_rotation.to_matrix().transposed()
            local_points = [rotation @ (point - center) for point in points]
            distance = max(max(point.z + abs(point.x) / tan_x,
                               point.z + abs(point.y) / tan_y) for point in local_points)
            view.view_perspective = "PERSP"
            view.view_location = center
            view.view_distance = max(1.0, distance * 1.15)
            space.clip_end = max(space.clip_end, view.view_distance + radius * 2.0)
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == volume)
    bpy.context.view_layer.objects.active = volume


def _build_legacy_single_scene(job):
    scene = json.loads(Path(job["metadata_file"]).read_text(encoding="utf-8"))
    arrays_path = Path(job["arrays_file"])
    output_blend = Path(job["output_blend"])
    vdb_path = Path(job["vdb_path"])
    with np.load(arrays_path, allow_pickle=False) as arrays:
        progress(35, "写入 OpenVDB 密度和颜色网格")
        shape = write_vdb(scene, arrays, vdb_path)
    progress(55, "创建体材质与坐标参考")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    configure_scene()
    data_collection = collection(DATA_COLLECTION, bpy.context.scene.collection)
    camera_collection = collection(CAMERA_COLLECTION, bpy.context.scene.collection)
    root = bpy.data.objects.new(ROOT_NAME, None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.2
    data_collection.objects.link(root)
    root["bandscope_schema"] = int(scene["schema_version"])
    root["snapshot_id"] = scene["source"]["snapshot_id"]
    root["captured_at"] = scene["source"]["captured_at"]
    root["scene_metadata"] = json.dumps(scene, ensure_ascii=False)
    camera = create_camera(scene, camera_collection)
    material = build_material(scene["blender_grid"]["material_clip_bounds"])
    volume_data = bpy.data.volumes.new("BandScope VDB")
    volume_data.filepath = str(vdb_path)
    volume_data.render.space = "OBJECT"
    volume_data.render.precision = "FULL"
    volume_data.render.clipping = 0.0
    volume_data.materials.append(material)
    volume = bpy.data.objects.new(VOLUME_NAME, volume_data)
    data_collection.objects.link(volume)
    volume.parent = root
    volume["scientific_shape"] = list(shape)
    volume["unit_distance_blender"] = float(scene["opacity"]["unit_distance_blender"])
    if not volume_data.grids.load():
        raise RuntimeError("Blender 无法重新载入刚写入的 OpenVDB 文件。")
    grid_names = {grid.name for grid in volume_data.grids}
    if not {"density", "color"}.issubset(grid_names):
        raise RuntimeError(f"OpenVDB 网格不完整：{sorted(grid_names)}")
    create_references(scene, data_collection, root, camera)
    create_axis_indicator(scene, data_collection, root, camera)
    # 辅助盒框保留可包容原数据的局部尺寸，平移到旁边供用户独立排版。
    create_guide_frame(scene, data_collection, root)
    readme = bpy.data.texts.new("START HERE · BandScope")
    readme.write(Path(job["readme_file"]).read_text(encoding="utf-8"))
    frame_data_viewports(data_collection, volume)
    progress(80, "保存 Blender 工程")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    volume_data.filepath = f"//{job['assets_dir_name']}/volume.vdb"
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    relative_path = volume_data.filepath
    progress(90, "重新打开工程并校验资产")
    bpy.ops.wm.open_mainfile(filepath=str(output_blend))
    reopened = bpy.data.objects.get(VOLUME_NAME)
    if reopened is None or reopened.type != "VOLUME":
        raise RuntimeError("重新打开工程后未找到体数据对象。")
    if not reopened.data.filepath.startswith("//"):
        raise RuntimeError("工程中的 VDB 路径不是相对路径。")
    resolved = Path(bpy.path.abspath(reopened.data.filepath)).resolve()
    if resolved != vdb_path.resolve() or not resolved.is_file():
        raise RuntimeError(f"工程相对路径未解析到配套 VDB：{resolved}")
    if not reopened.data.grids.load():
        raise RuntimeError("重新打开工程后无法载入 VDB。")
    camera = bpy.context.scene.camera
    guide_settings = scene["guide_frame"]
    guide = bpy.data.objects.get(guide_settings["object"])
    if guide is None or guide.type != "CURVE":
        raise RuntimeError("重新打开工程后未找到辅助盒框对象。")
    guide_bounds = [float(value) for value in guide["bounds"]]
    axis_bounds = [float(value) for value in guide["axis_frame_bounds"]]
    data_bounds = [float(value) for value in scene["blender_grid"]["material_clip_bounds"]]
    larger = all(
        guide_bounds[index] < axis_bounds[index]
        if index % 2 == 0 else guide_bounds[index] > axis_bounds[index]
        for index in range(6)
    )
    encloses = all(
        guide_bounds[axis * 2] <= data_bounds[axis * 2]
        and guide_bounds[axis * 2 + 1] >= data_bounds[axis * 2 + 1]
        for axis in range(3)
    )
    if not larger or not encloses:
        raise RuntimeError("辅助盒框未通过校验：必须大于坐标轴框并包住数据体。")
    guide_collection = next(
        (owner for owner in guide.users_collection if owner.name == guide_settings["collection"]),
        None,
    )
    if guide_collection is None:
        raise RuntimeError("辅助盒框不在预期的 Collection 中。")
    if (guide.location - Vector(scene["layout"]["guide_frame"])).length > 1e-6:
        raise RuntimeError("辅助盒框未与数据体分离。")
    references = bpy.data.collections["Coordinate reference · editable"]
    if any((obj.location - Vector(scene["layout"]["coordinates"])).length > 1e-6
           for obj in references.objects if obj.type == "CURVE"):
        raise RuntimeError("坐标轴未与数据体分离。")
    indicator_report = validate_axis_indicator(scene["axis_indicator"], ROOT_NAME)
    report = {
        "schema_version": scene["schema_version"],
        "blender_version": bpy.app.version_string,
        "blend_reopened": True,
        "volume_filepath": reopened.data.filepath,
        "resolved_vdb": str(resolved),
        "grid_names": [grid.name for grid in reopened.data.grids],
        "grid_shape": list(shape),
        "data_collection": DATA_COLLECTION,
        "camera_collection": CAMERA_COLLECTION,
        "parent": ROOT_NAME,
        "camera": {
            "projection": camera.data.type,
            "sensor_fit": camera.data.sensor_fit,
            "ortho_scale": camera.data.ortho_scale,
            "lens": camera.data.lens,
            "position": list(camera.location),
            "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
        },
        "render_engine": bpy.context.scene.render.engine,
        "transparent_background": bpy.context.scene.render.film_transparent,
        "view_transform": bpy.context.scene.view_settings.view_transform,
        "guide_frame": {
            "object": guide.name,
            "collection": guide_settings["collection"],
            "parent": guide.parent.name if guide.parent else None,
            "bounds": guide_bounds,
            "axis_frame_bounds": axis_bounds,
            "data_bounds": data_bounds,
            "larger_than_axis_frame": bool(larger),
            "encloses_data_bounds": bool(encloses),
            "hide_render": bool(guide.hide_render),
            "collection_hide_render": bool(guide_collection.hide_render),
        },
        "axis_indicator": indicator_report,
        "density_space": reopened.data.render.space,
        "weak_signal_clipping": reopened.data.render.clipping,
    }
    Path(job["report_file"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(96, "工程与资产校验完成")
    return report


def _box_enabled(settings, presets):
    return bool(presets.get(settings.get("preset_group", settings["preset"])))


def _selected_scene(scene, presets):
    selected = dict(scene)
    selected["box_presets"] = [
        box for box in scene.get("box_presets") or () if _box_enabled(box, presets)
    ]
    return selected


def _create_entry(scene, entry_job, project):
    composition = scene["composition"]
    translation = project["layout"]["entries"][scene["entry_id"]]["translation"]
    with np.load(entry_job["arrays_file"], allow_pickle=False) as arrays:
        shape = write_vdb(scene, arrays, entry_job["vdb_path"])
    data_collection = collection(composition["data_collection"], bpy.context.scene.collection)
    camera_collection = collection(composition["camera_collection"], bpy.context.scene.collection)
    root = bpy.data.objects.new(composition["parent"], None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.2
    root.location = Vector(translation)
    root["bandscope_schema"] = int(scene["schema_version"])
    root["bandscope_entry_id"] = scene["entry_id"]
    root["snapshot_id"] = scene["source"]["snapshot_id"]
    root["captured_at"] = scene["source"]["captured_at"]
    root["scene_metadata"] = json.dumps(scene, ensure_ascii=False)
    data_collection.objects.link(root)
    camera = create_camera(
        scene, camera_collection, offset=translation,
        activate=not project["overview_camera"]["enabled"],
    )
    material = build_material(scene["blender_grid"]["material_clip_bounds"])
    material.name = "BandScope · absorption + emission" + composition.get("name_suffix", "")
    volume_data = bpy.data.volumes.new("BandScope VDB" + composition.get("name_suffix", ""))
    volume_data.filepath = str(entry_job["vdb_path"])
    volume_data.render.space = "OBJECT"
    volume_data.render.precision = "FULL"
    volume_data.render.clipping = 0.0
    volume_data.materials.append(material)
    volume = bpy.data.objects.new(composition["volume"], volume_data)
    data_collection.objects.link(volume)
    volume.parent = root
    volume["bandscope_entry_id"] = scene["entry_id"]
    volume["scientific_shape"] = list(shape)
    volume["unit_distance_blender"] = float(scene["opacity"]["unit_distance_blender"])
    if not volume_data.grids.load():
        raise RuntimeError(f"条目 {scene['entry_id']} 无法重新载入 OpenVDB。")
    if not {"density", "color"}.issubset({grid.name for grid in volume_data.grids}):
        raise RuntimeError(f"条目 {scene['entry_id']} 的 OpenVDB 网格不完整。")
    presets = project["presets"]
    if presets.get("coordinates"):
        create_references(scene, data_collection, root, camera)
    if presets.get("orientation"):
        create_axis_indicator(scene, data_collection, root, camera)
    if presets.get("guide_frame"):
        create_guide_frame(scene, data_collection, root)
    selected = _selected_scene(scene, presets)
    boxes = create_box_presets(selected, data_collection, root)
    lights = create_box_lights(selected, data_collection, root)
    return {
        "scene": scene, "root": root, "volume": volume, "camera": camera,
        "data_collection": data_collection, "camera_collection": camera_collection,
        "shape": shape, "boxes": boxes, "lights": lights,
    }


def _create_overview_camera(metadata, target):
    project = metadata["project"]
    first = metadata["entries"][0]
    camera = create_camera(first, target, name=project["overview_camera"]["object"], activate=True)
    bounds = [record["world_bounds"] for record in project["layout"]["entries"].values()]
    low = Vector(tuple(min(record[axis * 2] for record in bounds) for axis in range(3)))
    high = Vector(tuple(max(record[axis * 2 + 1] for record in bounds) for axis in range(3)))
    center = (low + high) * 0.5
    corners = [Vector((x, y, z)) for x in (low.x, high.x)
               for y in (low.y, high.y) for z in (low.z, high.z)]
    settings = first["camera"]
    right, up, backward = (Vector(value) for value in settings["rotation_basis_columns"])
    forward = -backward
    relative = [point - center for point in corners]
    xs = [point.dot(right) for point in relative]
    ys = [point.dot(up) for point in relative]
    zs = [point.dot(forward) for point in relative]
    width, height = first["resolution"]
    aspect = width / max(height, 1)
    radius = max((point - center).length for point in corners)
    if settings["parallel_projection"]:
        vertical = 2.0 * max(abs(value) for value in ys)
        horizontal = 2.0 * max(abs(value) for value in xs)
        camera.data.ortho_scale = max(vertical, horizontal / aspect, 0.1) * 1.15
        distance = max(3.0 * radius, 1.0)
    else:
        tan_y = math.tan(math.radians(float(settings["view_angle_degrees"])) / 2.0)
        tan_x = tan_y * aspect
        distance = max(max(abs(y) / tan_y - z for y, z in zip(ys, zs)),
                       max(abs(x) / tan_x - z for x, z in zip(xs, zs)), 0.1) * 1.15
    camera.matrix_world.translation = center - forward * distance
    camera.data.clip_start = max(1e-6, min(float(camera.data.clip_start), distance - radius))
    camera.data.clip_end = max(float(camera.data.clip_end), distance + 2.0 * radius)
    camera["bandscope_overview"] = True
    return camera


def _validate_guide(scene, root):
    settings = scene["guide_frame"]
    guide = bpy.data.objects.get(settings["object"])
    if guide is None or guide.type != "CURVE" or guide.parent != root:
        raise RuntimeError(f"条目 {scene['entry_id']} 的辅助盒框缺失或父对象错误。")
    guide_bounds = [float(value) for value in guide["bounds"]]
    axis_bounds = [float(value) for value in guide["axis_frame_bounds"]]
    data_bounds = [float(value) for value in scene["blender_grid"]["material_clip_bounds"]]
    larger = all(guide_bounds[index] < axis_bounds[index] if index % 2 == 0
                 else guide_bounds[index] > axis_bounds[index] for index in range(6))
    encloses = all(guide_bounds[axis * 2] <= data_bounds[axis * 2]
                   and guide_bounds[axis * 2 + 1] >= data_bounds[axis * 2 + 1]
                   for axis in range(3))
    if not larger or not encloses:
        raise RuntimeError(f"条目 {scene['entry_id']} 的辅助盒框尺寸不合规。")
    owner = next((item for item in guide.users_collection if item.name == settings["collection"]), None)
    if owner is None or not owner.hide_render:
        raise RuntimeError(f"条目 {scene['entry_id']} 的辅助盒框 Collection 或默认渲染行为错误。")
    if (guide.location - Vector(scene["layout"]["guide_frame"])).length > 1e-6:
        raise RuntimeError(f"条目 {scene['entry_id']} 的辅助盒框局部位移错误。")
    return {
        "object": guide.name, "collection": settings["collection"], "parent": root.name,
        "bounds": guide_bounds, "axis_frame_bounds": axis_bounds, "data_bounds": data_bounds,
        "larger_than_axis_frame": bool(larger), "encloses_data_bounds": bool(encloses),
        "hide_render": bool(guide.hide_render), "collection_hide_render": bool(owner.hide_render),
    }


def _validate_entry(scene, entry_job, project):
    composition = scene["composition"]
    entry_id = scene["entry_id"]
    volume = bpy.data.objects.get(composition["volume"])
    root = bpy.data.objects.get(composition["parent"])
    if volume is None or volume.type != "VOLUME" or root is None or volume.parent != root:
        raise RuntimeError(f"条目 {entry_id} 的体对象或父对象缺失。")
    expected = f"//{project['assets']['directory']}/{scene['assets']['vdb']}"
    if volume.data.filepath.replace("\\", "/") != expected:
        raise RuntimeError(f"条目 {entry_id} 的 VDB 相对路径改向：{volume.data.filepath}")
    resolved = Path(bpy.path.abspath(volume.data.filepath)).resolve()
    expected_resolved = Path(entry_job["vdb_path"]).resolve()
    if resolved != expected_resolved or not resolved.is_file() or not volume.data.grids.load():
        raise RuntimeError(f"条目 {entry_id} 的 VDB 无法从预期资产路径载入：{resolved}")
    translation = Vector(project["layout"]["entries"][entry_id]["translation"])
    if (root.location - translation).length > 1e-6:
        raise RuntimeError(f"条目 {entry_id} 的父对象排布位移被改变。")
    presets = project["presets"]
    guide = _validate_guide(scene, root) if presets.get("guide_frame") else {}
    indicator = (validate_axis_indicator(scene["axis_indicator"], root.name)
                 if presets.get("orientation") else {})
    reference_name = "Coordinate reference · editable" + composition.get("name_suffix", "")
    references = bpy.data.collections.get(reference_name)
    if presets.get("coordinates"):
        if references is None or any(
                (obj.location - Vector(scene["layout"]["coordinates"])).length > 1e-6
                for obj in references.objects if obj.type == "CURVE"):
            raise RuntimeError(f"条目 {entry_id} 的坐标参考缺失或位移错误。")
    elif references is not None:
        raise RuntimeError(f"条目 {entry_id} 未选择坐标参考却存在对象。")
    boxes = validate_box_presets(scene, root, presets)
    lights = validate_box_lights(scene, root, presets, boxes)
    layout = project.get("box_layout") or {}
    if any(box.get("layout") != layout.get("id") for box in boxes):
        raise RuntimeError(f"条目 {entry_id} 的薄板布局与工程配置不一致。")
    camera = bpy.data.objects.get(composition["camera"])
    if camera is None or camera.type != "CAMERA":
        raise RuntimeError(f"条目 {entry_id} 的备用相机缺失。")
    return {
        "entry_id": entry_id,
        "volume_filepath": volume.data.filepath,
        "resolved_vdb": str(resolved),
        "grid_names": [grid.name for grid in volume.data.grids],
        "grid_shape": list(volume["scientific_shape"]),
        "data_collection": composition["data_collection"],
        "camera_collection": composition["camera_collection"],
        "parent": composition["parent"],
        "camera": {
            "projection": camera.data.type, "sensor_fit": camera.data.sensor_fit,
            "ortho_scale": camera.data.ortho_scale, "lens_mm": camera.data.lens,
            "position": list(camera.location),
            "resolution": [bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y],
        },
        "guide_frame": guide,
        "axis_indicator": indicator,
        "coordinates_present": references is not None,
        "box_presets": boxes,
        "box_lights": lights,
        "box_layout": layout.get("id"),
        "density_space": volume.data.render.space,
        "weak_signal_clipping": volume.data.render.clipping,
    }


def _fit_actual_entry_layout(created, metadata, metadata_path):
    """Use evaluated object bounds, including text, before placing adjacent groups."""
    project = metadata["project"]
    previous_max = None
    for record in created:
        root = record["root"]
        collection = record["data_collection"]
        old_translation = float(root.location.x)
        root.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        points = [
            tuple(obj.matrix_world @ Vector(corner))
            for source in collection.all_objects if source.type not in {"EMPTY", "CAMERA", "LIGHT"}
            for obj in (source.evaluated_get(depsgraph),) for corner in obj.bound_box
        ]
        if not points:
            raise RuntimeError(f"条目 {record['scene']['entry_id']} 没有可排布对象。")
        values = np.asarray(points, dtype=np.float64)
        low = values.min(axis=0)
        high = values.max(axis=0)
        translation_x = 0.0 if previous_max is None else previous_max + float(project["layout"]["gap"]) - float(low[0])
        root.location.x = translation_x
        record["camera"].location.x += translation_x - old_translation
        world_low = low.copy()
        world_high = high.copy()
        world_low[0] += translation_x
        world_high[0] += translation_x
        local = np.column_stack((low, high)).ravel().tolist()
        world = np.column_stack((world_low, world_high)).ravel().tolist()
        project["layout"]["entries"][record["scene"]["entry_id"]].update(
            translation=[float(translation_x), 0.0, 0.0],
            local_bounds=local,
            world_bounds=world,
        )
        previous_max = float(world_high[0])
        bpy.context.view_layer.update()
    project["layout"]["bounds_source"] = "evaluated Blender object bounds including text"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def build_scene(job):
    metadata = json.loads(Path(job["metadata_file"]).read_text(encoding="utf-8"))
    project = metadata["project"]
    output_blend = Path(job["output_blend"])
    jobs = {entry["entry_id"]: entry for entry in job["entries"]}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    configure_scene((project.get("render") or {}).get("samples", 32))
    created = []
    total = len(metadata["entries"])
    for index, scene in enumerate(metadata["entries"]):
        progress(30 + int(35 * index / total), f"创建条目 {scene['entry_id']} 的体数据与预设")
        created.append(_create_entry(scene, jobs[scene["entry_id"]], project))
    _fit_actual_entry_layout(created, metadata, Path(job["metadata_file"]))
    if project["overview_camera"]["enabled"]:
        overview_collection = collection("BandScope Overview Camera", bpy.context.scene.collection)
        _create_overview_camera(metadata, overview_collection)
    readme = bpy.data.texts.new("START HERE · BandScope")
    readme.write(Path(job["readme_file"]).read_text(encoding="utf-8"))
    objects = [obj for record in created for obj in record["data_collection"].all_objects]
    frame_data_viewports(objects, created[0]["volume"])
    for record in created:
        scene = record["scene"]
        record["volume"].data.filepath = (
            f"//{project['assets']['directory']}/{scene['assets']['vdb']}"
        )
    progress(80, "保存多页面 Blender 工程")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_blend), check_existing=False)
    progress(90, "重新打开并逐条目校验工程")
    bpy.ops.wm.open_mainfile(filepath=str(output_blend))
    reports = [_validate_entry(scene, jobs[scene["entry_id"]], project)
               for scene in metadata["entries"]]
    material_names = [box["material"] for report in reports for box in report["box_presets"]]
    mesh_names = [box["mesh"] for report in reports for box in report["box_presets"]]
    if len(set(material_names)) != len(material_names) or len(set(mesh_names)) != len(mesh_names):
        raise RuntimeError("不同条目或长方体意外共享网格或材质。")
    camera = bpy.context.scene.camera
    report = {
        "schema_version": metadata["schema_version"],
        "blender_version": bpy.app.version_string,
        "blend_reopened": True,
        "entries": reports,
        "active_camera": camera.name if camera else None,
        "overview_camera": project["overview_camera"],
        "box_layout": project.get("box_layout") or {},
        "box_materials": project.get("box_materials") or {},
        "render_samples": int(bpy.context.scene.cycles.samples),
        "render_engine": bpy.context.scene.render.engine,
        "transparent_background": bpy.context.scene.render.film_transparent,
        "view_transform": bpy.context.scene.view_settings.view_transform,
    }
    Path(job["report_file"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(96, "工程与全部条目资产校验完成")
    return report


def check_project(job, *, write_updates=False):
    source = json.loads(Path(job["source_metadata_file"]).read_text(encoding="utf-8"))
    project = source["project"]
    bpy.ops.wm.open_mainfile(filepath=job["blend_path"])
    for scene in source["entries"]:
        composition = scene["composition"]
        volume = bpy.data.objects.get(composition["volume"])
        root = bpy.data.objects.get(composition["parent"])
        if volume is None or volume.type != "VOLUME" or root is None or volume.parent != root:
            raise RuntimeError(f"条目 {scene['entry_id']} 的体对象被删除或父对象不符。")
        expected = f"//{project['assets']['directory']}/{scene['assets']['vdb']}"
        if volume.data.filepath.replace("\\", "/") != expected:
            raise RuntimeError(
                f"条目 {scene['entry_id']} 的 VDB 路径已改向：{volume.data.filepath}；应为 {expected}"
            )
        resolved = Path(bpy.path.abspath(volume.data.filepath)).resolve()
        if not resolved.is_file() or not volume.data.grids.load():
            raise RuntimeError(f"条目 {scene['entry_id']} 的原 VDB 无法载入：{resolved}")
    staged = []
    if write_updates:
        updated = json.loads(Path(job["metadata_file"]).read_text(encoding="utf-8"))
        scenes = {entry["entry_id"]: entry for entry in updated["entries"]}
        for entry_job in job["entries"]:
            entry_id = entry_job["entry_id"]
            if entry_id not in scenes:
                raise RuntimeError(f"更新配置缺少条目 {entry_id}。")
            with np.load(entry_job["arrays_file"], allow_pickle=False) as arrays:
                shape = write_vdb(scenes[entry_id], arrays, entry_job["vdb_path"])
            probe = bpy.data.volumes.new("BandScope update validation")
            try:
                probe.filepath = str(entry_job["vdb_path"])
                if not probe.grids.load() or not {"density", "color"}.issubset(
                        {grid.name for grid in probe.grids}):
                    raise RuntimeError(f"条目 {entry_id} 的暂存 VDB 校验失败。")
            finally:
                bpy.data.volumes.remove(probe)
            staged.append({"entry_id": entry_id, "shape": list(shape)})
    report = {
        "project_checked": True,
        "blend_untouched": True,
        "vdb_staged": staged,
        "entry_count": len(source["entries"]),
    }
    Path(job["report_file"]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--job")
    args = parser.parse_args(argv)
    try:
        report = capabilities()
        if not report["cycles"]:
            raise RuntimeError("当前 Blender 缺少 Cycles CPU 渲染能力。")
        if not report["light_linking"]:
            raise RuntimeError("当前 Blender 缺少 Cycles 灯光链接（Light Linking）能力。")
        if args.probe:
            print(CAPABILITY_PREFIX + json.dumps(report, ensure_ascii=False), flush=True)
            return 0
        if not args.job:
            parser.error("--job is required unless --probe is used")
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        mode = "check" if args.check else str(job.get("mode", "build"))
        if mode == "check":
            check_project(job)
            progress(100, "Blender 工程只读结构检查完成")
        elif mode == "update":
            check_project(job, write_updates=True)
            progress(100, "Blender 工程检查与暂存 VDB 写出完成；未保存工程")
        else:
            build_scene(job)
            progress(100, "Blender 后台转换完成")
        return 0
    except Exception as exc:
        print(ERROR_PREFIX + str(exc), flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
