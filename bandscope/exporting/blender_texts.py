# -*- coding: utf-8 -*-
"""Blender 文本预设注册表与随工程携带的开源字体资源。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Dict, Tuple


class BlenderTextError(ValueError):
    pass


TEXT_GROUP_PRESET = "text_presets"
TEXT_COLLECTION_NAME = "Text presets · publication"
TEXT_BODY = "A"
TEXT_CAP_HEIGHT = 0.12
TEXT_COLUMNS = 5
TEXT_COLUMN_SPACING = 0.32
TEXT_ROW_SPACING = 0.28
TEXT_FONT_DIRECTORY = "fonts"
TEXT_FONT_LICENSE = "LICENSE_DEJAVU"


@dataclass(frozen=True)
class BlenderTextStyle:
    style_id: str
    label: str
    font_file: str
    kind: str
    color: Tuple[float, float, float]
    extrude: float = 0.0
    bevel_depth: float = 0.0
    fill_mode: str = "BOTH"
    roughness: float = 0.5
    metallic: float = 0.0
    ior: float = 1.45
    emission_strength: float = 1.0
    glass_weight: float = 0.65

    def settings(self) -> Dict[str, object]:
        return {
            "preset": self.style_id,
            "label": self.label,
            "font": f"{TEXT_FONT_DIRECTORY}/{self.font_file}",
            "kind": self.kind,
            "color": list(self.color),
            "extrude": self.extrude,
            "bevel_depth": self.bevel_depth,
            "fill_mode": self.fill_mode,
            "roughness": self.roughness,
            "metallic": self.metallic,
            "ior": self.ior,
            "emission_strength": self.emission_strength,
            "glass_weight": self.glass_weight,
        }


TEXT_STYLES: Tuple[BlenderTextStyle, ...] = (
    BlenderTextStyle("sans_regular", "无衬线常规", "DejaVuSans.ttf", "flat", (0.02, 0.02, 0.02)),
    BlenderTextStyle("sans_bold", "无衬线粗体", "DejaVuSans-Bold.ttf", "flat", (0.02, 0.02, 0.02)),
    BlenderTextStyle("serif_regular", "衬线常规", "DejaVuSerif.ttf", "flat", (0.02, 0.02, 0.02)),
    BlenderTextStyle("serif_italic", "衬线斜体", "DejaVuSerif-Italic.ttf", "flat", (0.02, 0.02, 0.02)),
    BlenderTextStyle("monospace_regular", "等宽常规", "DejaVuSansMono.ttf", "flat", (0.02, 0.02, 0.02)),
    BlenderTextStyle(
        "extruded_bold", "立体粗体", "DejaVuSans-Bold.ttf", "matte", (0.12, 0.16, 0.22),
        extrude=0.035, bevel_depth=0.003, roughness=0.65,
    ),
    BlenderTextStyle(
        "outline", "空心描边", "DejaVuSans.ttf", "outline", (0.04, 0.04, 0.04),
        bevel_depth=0.012, fill_mode="NONE",
    ),
    BlenderTextStyle(
        "emissive", "发光文字", "DejaVuSans-Bold.ttf", "emission", (0.05, 0.45, 1.0),
        extrude=0.01, bevel_depth=0.002, emission_strength=2.0,
    ),
    BlenderTextStyle(
        "metallic", "金属文字", "DejaVuSans-Bold.ttf", "metallic", (0.75, 0.78, 0.82),
        extrude=0.035, bevel_depth=0.004, roughness=0.24, metallic=1.0,
    ),
    BlenderTextStyle(
        "glass", "玻璃文字", "DejaVuSans-Bold.ttf", "glass", (0.85, 0.95, 1.0),
        extrude=0.04, bevel_depth=0.004, roughness=0.04, ior=1.45,
    ),
)
TEXT_PRESETS = tuple(style.style_id for style in TEXT_STYLES)
TEXT_STYLE_BY_ID = {style.style_id: style for style in TEXT_STYLES}
TEXT_FONT_FILES = tuple(dict.fromkeys(style.font_file for style in TEXT_STYLES))


def text_style(style_id) -> BlenderTextStyle:
    try:
        return TEXT_STYLE_BY_ID[str(style_id)]
    except KeyError as exc:
        raise BlenderTextError(f"未知文本预设：{style_id!r}") from exc


def text_style_summaries() -> Dict[str, Dict[str, object]]:
    return {style.style_id: style.settings() for style in TEXT_STYLES}


def text_font_metadata(enabled=True) -> Dict[str, object]:
    return {
        "directory": TEXT_FONT_DIRECTORY,
        "files": [f"{TEXT_FONT_DIRECTORY}/{name}" for name in TEXT_FONT_FILES] if enabled else [],
        "license": f"{TEXT_FONT_DIRECTORY}/{TEXT_FONT_LICENSE}" if enabled else None,
        "packed_in_blend": bool(enabled),
        "family": "DejaVu",
    }


def stage_text_fonts(assets_path) -> Dict[str, object]:
    """从现有 Matplotlib 数据包复制字体；不在 Blender 进程中依赖宿主 Python 包。"""
    import matplotlib

    source = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    target = Path(assets_path) / TEXT_FONT_DIRECTORY
    target.mkdir(parents=True, exist_ok=True)
    for name in (*TEXT_FONT_FILES, TEXT_FONT_LICENSE):
        source_path = source / name
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise BlenderTextError(
                f"Matplotlib 的 DejaVu 字体资源缺失：{source_path}；请检查运行环境或冻结包资源。"
            )
        shutil.copy2(source_path, target / name)
    return text_font_metadata(True)
