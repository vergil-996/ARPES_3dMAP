"""Serializable state for polyline momentum-path slices."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


PATH_SLICE_PAGE_KIND = "path_slice"
PATH_SOURCE_KEY = "path_source"
PATH_NODES_KEY = "path_nodes"
PATH_SEGMENTS_KEY = "path_segments"
PATH_SHOW_GUIDES_KEY = "path_show_guides"
PATH_SHOW_LABELS_KEY = "path_show_labels"
PATH_SELECTED_KEY = "path_selected_segments"
PATH_VIEWPORT_KEY = "path_viewport"
PATH_DRAFT_KEY = "_path_draft"


@dataclass
class PathNode:
    x: float
    y: float
    label: str

    def __post_init__(self):
        self.x = float(self.x)
        self.y = float(self.y)
        self.label = str(self.label or "").strip() or "P"
        if not np.isfinite([self.x, self.y]).all():
            raise ValueError("路径节点坐标必须是有限数值。")

    def to_mapping(self) -> Dict[str, Any]:
        return {"x": self.x, "y": self.y, "label": self.label}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], index: int = 0) -> "PathNode":
        if not isinstance(value, Mapping):
            raise ValueError("路径节点格式无效。")
        return cls(value["x"], value["y"], value.get("label") or f"P{index + 1}")


@dataclass
class PathSegmentStyle:
    thickness: float = 0.0
    black: float = 0.0
    gray: float = 50.0
    white: float = 100.0
    intensity_map: str = "线性"

    def __post_init__(self):
        self.thickness = max(0.0, float(self.thickness))
        self.black = float(np.clip(float(self.black), 0.0, 100.0))
        self.gray = float(np.clip(float(self.gray), 0.0, 100.0))
        self.white = float(np.clip(float(self.white), 0.0, 100.0))
        self.intensity_map = str(self.intensity_map or "线性")

    @property
    def levels(self) -> Tuple[float, float, float]:
        return self.black, self.gray, self.white

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "thickness": self.thickness,
            "black": self.black,
            "gray": self.gray,
            "white": self.white,
            "intensity_map": self.intensity_map,
        }

    @classmethod
    def from_mapping(cls, value: Optional[Mapping[str, Any]]) -> "PathSegmentStyle":
        value = value if isinstance(value, Mapping) else {}
        levels = value.get("levels") or (0.0, 50.0, 100.0)
        return cls(
            value.get("thickness", 0.0),
            value.get("black", levels[0]),
            value.get("gray", levels[1]),
            value.get("white", levels[2]),
            value.get("intensity_map", value.get("opacity_mode", "线性")),
        )


@dataclass
class PathSliceModel:
    nodes: List[PathNode] = field(default_factory=list)
    segments: List[PathSegmentStyle] = field(default_factory=list)
    show_guides: bool = True
    show_labels: bool = True
    selected: List[int] = field(default_factory=lambda: [0])
    viewport: Optional[List[float]] = None

    @classmethod
    def from_params(cls, params: Mapping[str, Any]) -> "PathSliceModel":
        nodes = [
            PathNode.from_mapping(value, index)
            for index, value in enumerate(params.get(PATH_NODES_KEY) or ())
        ]
        count = max(0, len(nodes) - 1)
        raw_segments = list(params.get(PATH_SEGMENTS_KEY) or ())
        segments = [
            PathSegmentStyle.from_mapping(raw_segments[index] if index < len(raw_segments) else None)
            for index in range(count)
        ]
        selected = sorted({
            int(value) for value in params.get(PATH_SELECTED_KEY, [0] if count else [])
            if isinstance(value, (int, float)) and 0 <= int(value) < count
        })
        viewport = params.get(PATH_VIEWPORT_KEY)
        if not (
            isinstance(viewport, (list, tuple))
            and len(viewport) == 2
            and np.isfinite(viewport).all()
        ):
            viewport = None
        else:
            viewport = [float(viewport[0]), float(viewport[1])]
        return cls(
            nodes=nodes,
            segments=segments,
            show_guides=bool(params.get(PATH_SHOW_GUIDES_KEY, True)),
            show_labels=bool(params.get(PATH_SHOW_LABELS_KEY, True)),
            selected=selected,
            viewport=viewport,
        )

    def write_params(self, params: Dict[str, Any]) -> None:
        params[PATH_NODES_KEY] = [node.to_mapping() for node in self.nodes]
        params[PATH_SEGMENTS_KEY] = [segment.to_mapping() for segment in self.segments]
        params[PATH_SHOW_GUIDES_KEY] = bool(self.show_guides)
        params[PATH_SHOW_LABELS_KEY] = bool(self.show_labels)
        params[PATH_SELECTED_KEY] = list(self.selected)
        if self.viewport is None:
            params.pop(PATH_VIEWPORT_KEY, None)
        else:
            params[PATH_VIEWPORT_KEY] = list(self.viewport)

    @property
    def segment_count(self) -> int:
        return max(0, len(self.nodes) - 1)

    def validate(self) -> None:
        if len(self.nodes) < 2:
            raise ValueError("至少需要两个路径节点。")
        if len(self.segments) != self.segment_count:
            raise ValueError("路径分段参数不完整。")
        for start, end in zip(self.nodes, self.nodes[1:]):
            if np.hypot(end.x - start.x, end.y - start.y) <= 1e-12:
                raise ValueError("相邻路径节点必须位于不同位置。")

    def path_name(self) -> str:
        return "–".join(node.label for node in self.nodes)

    def segment_name(self, index: int) -> str:
        return f"{self.nodes[index].label}–{self.nodes[index + 1].label}"

    def select(self, index: int, *, additive: bool = False) -> None:
        if not 0 <= int(index) < self.segment_count:
            return
        index = int(index)
        if not additive:
            self.selected = [index]
        elif index in self.selected:
            self.selected.remove(index)
        else:
            self.selected.append(index)
            self.selected.sort()

    def update_selected(self, **changes) -> None:
        for index in self.selected:
            if not 0 <= index < len(self.segments):
                continue
            segment = self.segments[index]
            for key, value in changes.items():
                if key == "levels":
                    segment.black, segment.gray, segment.white = (float(v) for v in value)
                elif key in {"thickness", "black", "gray", "white", "intensity_map"}:
                    setattr(segment, key, value)
            segment.__post_init__()


def draft_nodes(params: Mapping[str, Any]) -> List[PathNode]:
    return [
        PathNode.from_mapping(value, index)
        for index, value in enumerate(params.get(PATH_DRAFT_KEY) or ())
    ]


def write_draft(params: Dict[str, Any], nodes: Iterable[PathNode]) -> None:
    params[PATH_DRAFT_KEY] = [node.to_mapping() for node in nodes]


def common_segment_values(
    segments: Sequence[PathSegmentStyle], selected: Sequence[int]
) -> Dict[str, Any]:
    chosen = [segments[index] for index in selected if 0 <= index < len(segments)]
    if not chosen:
        return {}
    result: Dict[str, Any] = {}
    for key in ("thickness", "black", "gray", "white", "intensity_map"):
        value = getattr(chosen[0], key)
        result[key] = value if all(getattr(item, key) == value for item in chosen[1:]) else None
    return result
