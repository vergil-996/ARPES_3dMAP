"""Small deterministic 3D snapshots for Blender export tests."""
import numpy as np

from bandscope.exporting.publication_models import PublicationSnapshot
from bandscope.exporting.publication_renderers import compute_level_info


def make_blender_snapshot(*, volume=None, full_shape=(6, 7, 8), data_bounds=None,
                          levels=(0.0, 50.0, 100.0), locked_range=None,
                          opacity_multiplier=None, enhancement=None,
                          clip_render_bounds=None, viewport_aspect=1.5,
                          camera=None, cmap_name="magma", show_axes=True):
    if volume is None:
        x, y, e = np.indices((3, 4, 5))
        volume = (0.15 + 0.2 * x + 0.08 * y + 0.06 * e).astype(np.float32)
    volume = np.asarray(volume, dtype=np.float32)
    if data_bounds is None:
        data_bounds = (
            1, 1 + volume.shape[0] - 1,
            2, 2 + volume.shape[1] - 1,
            1, 1 + volume.shape[2] - 1,
        )
    coords = {
        "X": np.linspace(-0.8, 0.4, full_shape[0]),
        "Y": np.linspace(1.2, -0.6, full_shape[1]),
        "E": np.linspace(2.0, -1.0, full_shape[2]),
        "delay": np.array([0.0, 100.0]),
    }
    level_info = compute_level_info(volume, levels, locked_range=locked_range)
    if camera is None:
        camera = {
            "position": (-260.0, -350.0, 280.0),
            "focal_point": (100.0, 100.0, 100.0),
            "view_up": (0.0, 0.0, -1.0),
            "parallel_projection": False,
            "parallel_scale": 120.0,
            "view_angle": 35.0,
            "clipping_range": (1.0, 2000.0),
        }
    payload = {
        "volume": volume,
        "data_bounds": tuple(data_bounds),
        "full_shape": tuple(full_shape),
        "spacing": tuple(200.0 / (n - 1) if n > 1 else 1.0 for n in full_shape),
        "clip_render_bounds": clip_render_bounds,
        "include_zero": False,
        "opacity_mode": "linear",
        "opacity_multiplier": opacity_multiplier,
        "show_axes": bool(show_axes),
        "viewport_aspect": float(viewport_aspect),
        "level_info": level_info,
        "intensity_label": "Intensity (a.u.)",
        "axis_titles": ["kx (Å⁻¹)", "ky (Å⁻¹)", "E (eV)"],
        "enhancement": enhancement,
    }
    return PublicationSnapshot(
        snapshot_id="blender-synthetic",
        source_page_id="page-3d",
        source_page_title="非对称 3D",
        page_kind="home",
        view="3d",
        view_family="3d",
        captured_at="2026-11-05T12:00:00",
        cmap_name=cmap_name,
        levels_params=tuple(levels),
        locked_range=locked_range,
        coords=coords,
        coord_sources={"X": "file", "Y": "file", "E": "file", "delay": "file"},
        coord_units={"X": "Å⁻¹", "Y": "Å⁻¹", "E": "eV", "delay": "fs"},
        source_desc="非对称 3D · 帧 0",
        home_frame_index=0,
        source_revision=("page-3d", 1),
        camera=camera,
        payload=payload,
    )


def project_report_boxes(metadata, entry_id):
    """Build the schema-8 box/light section of a fake Blender reopen report."""
    from bandscope.exporting.blender_materials import material_spec

    scene = next(entry for entry in metadata["entries"] if entry["entry_id"] == entry_id)
    presets = metadata["project"]["presets"]
    layout_id = metadata["project"]["box_layout"]["id"]
    boxes, lights = [], []
    for box in scene["box_presets"]:
        key = box["preset"]
        if not presets.get(box.get("preset_group", key)):
            continue
        parameters = box["parameters"]
        spec = material_spec(box["material_id"])
        light_name = None
        if spec.requires_light:
            light_name = f"light-{entry_id}-{key}"
            lights.append({
                "preset": key, "object": light_name, "type": "AREA", "energy": 100.0,
                "size": 2.0, "receiver_collection": scene["box_lighting"]["receiver_collection"],
                "parent": scene["composition"]["parent"],
            })
        boxes.append({
            "preset": key, "object": box["object"], "material": box["material"],
            "mesh": f"mesh-{entry_id}-{key}", "independent_material": True, "full_cuboid": True,
            "material_id": box["material_id"],
            "opacity": parameters.get("opacity"),
            "filter_strength": parameters.get("filter_strength"),
            "layout": layout_id,
            "intersects_data": bool(box.get("intersects_data")),
            "requires_light": bool(spec.requires_light),
            "light": light_name,
            "emission": {
                "effective": bool(
                    spec.emits_by_design
                    or float(parameters.get("emission_compensation", 0.0) or 0.0) > 0.0
                ),
            },
        })
    return boxes, lights


def project_report_texts(metadata, entry_id):
    """Build the schema-9 text/font/light section of a fake Blender reopen report."""
    scene = next(entry for entry in metadata["entries"] if entry["entry_id"] == entry_id)
    project = metadata["project"]
    if not project["presets"].get("text_presets"):
        return [], []
    records = [{
        "preset": item["preset"], "object": item["object"],
        "data": item["object"] + " data", "material": item["material"],
        "kind": item["kind"], "body": "A", "parent": scene["composition"]["parent"],
        "collection": scene["text_layout"]["collection"],
        "font": item["font"].rsplit("/", 1)[-1],
        "font_filepath": f"//{project['assets']['directory']}/{item['font']}",
        "font_packed": True, "cap_height": scene["text_layout"]["cap_height"],
        "center": list(item["center"]), "row": item["row"], "column": item["column"],
        "fill_mode": item["fill_mode"], "extrude": item["extrude"],
        "bevel_depth": item["bevel_depth"], "material_validated": True,
    } for item in scene["text_presets"]]
    lighting = scene["text_lighting"]
    source = lighting["lights"][0]
    lights = [{
        "object": source["object"], "type": "AREA", "energy": source["energy"],
        "size": source["size"], "parent": scene["composition"]["parent"],
        "receiver_collection": lighting["receiver_collection"], "receiver_count": 10,
    }]
    return records, lights


def project_report_volume(metadata, entry_id):
    scene = next(entry for entry in metadata["entries"] if entry["entry_id"] == entry_id)
    shading = metadata["project"]["volume_shading"]
    parameters = shading["parameters"]
    material = {
        "material": "BandScope volume material " + entry_id,
        "mode": shading["mode"],
        "parameters": {
            name: parameters[name] for name in (
                "density_multiplier", "scattering_amount", "emission_compensation", "anisotropy",
            )
        },
        "color_adjustment": list(parameters["color_adjustment"]),
        "connections": {
            "Absorption Coefficients": "Absorption = σ × (1 − ω)",
            "Scatter Coefficients": "Scatter = σ × ω",
            "Emission Coefficients": "Emission = compensation × σ × color",
        },
        "conserves_extinction": True,
        "zero_density_safe": True,
        "no_scattering": shading["mode"] == "emission",
        "material_validated": True,
    }
    lighting = scene["volume_lighting"]
    lights = [{
        "role": light["role"], "object": light["object"], "type": "AREA",
        "energy": light["energy"], "size": light["size"],
        "color": list(light["color"]), "position": list(light["position"]),
        "target": list(light["target"]), "parent": scene["composition"]["parent"],
        "collection": lighting["collection"],
        "receiver_collection": lighting["receiver_collection"],
        "receiver_objects": [scene["composition"]["volume"]],
        "camera_bound": False,
    } for light in lighting["lights"]]
    return material, lights


def write_fake_blender_project(root, snapshots=None, *, presets=None, name="合成工程",
                               box_settings=None, box_layout=None, volume_mode="emission"):
    """Write schema-11 assets and a header-only blend placeholder for host-side tests."""
    import json
    from pathlib import Path

    from bandscope.exporting.blender_jobs import project_usage_text
    from bandscope.exporting.blender_project import BlenderProjectRequest, make_project_metadata
    from bandscope.exporting.blender_scene import prepare_blender_scene

    root = Path(root)
    snapshots = list(snapshots or [make_blender_snapshot()])
    request = BlenderProjectRequest(
        snapshots, presets, box_settings=box_settings, box_layout=box_layout,
        volume_mode=volume_mode,
    )
    blend = root / f"{name}.blend"
    assets = root / f"{name}.assets"
    assets.mkdir(parents=True)
    items = []
    for entry in request.entries:
        scene = prepare_blender_scene(entry.snapshot)
        metadata = scene.metadata()
        metadata["entry_id"] = entry.entry_id
        directory = assets / "entries" / entry.entry_id
        directory.mkdir(parents=True)
        np.savez_compressed(
            directory / "arrays.npz", intensity=scene.intensity,
            display_color=scene.display_color, alpha=scene.alpha, density=scene.density,
        )
        (directory / "volume.vdb").write_bytes(b"VDB-" + entry.entry_id.encode("utf-8"))
        items.append((entry, metadata))
    metadata = make_project_metadata(items, request, assets.name)
    fonts = metadata["project"]["text_fonts"]
    for relative in fonts["files"]:
        path = assets.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"FONT-" + relative.rsplit("/", 1)[-1].encode("ascii"))
    if fonts.get("license"):
        license_path = assets.joinpath(*fonts["license"].split("/"))
        license_path.parent.mkdir(parents=True, exist_ok=True)
        license_path.write_text("DejaVu font license placeholder for synthetic tests", encoding="utf-8")
    (assets / "scene.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (assets / "README.txt").write_text(project_usage_text(metadata, assets.name), encoding="utf-8")
    blend.write_bytes(b"BLENDER-fake")
    return blend, metadata
