# -*- coding: utf-8 -*-
"""薄层长方体八材质注册表：纯 Python，无 Qt / bpy 导入。

宿主用这里的 ID、中文名称、默认参数和范围做请求校验；``blender_script.py``
用同一份 ID 分派节点工厂。新建导出为每种材质各生成一块橙色薄板，全部使用
默认参数。修改参数默认值时必须同步检查真实 Blender 渲染验收，不能只改数值。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping, Optional, Tuple


class BlenderMaterialError(ValueError):
    """材质 ID、参数或布局不符合注册表契约。"""


@dataclass(frozen=True)
class BoxMaterialSpec:
    material_id: str
    label: str
    summary: str
    limitations: str
    parameters: Mapping[str, float]
    ranges: Mapping[str, Tuple[float, float]]
    controls: Tuple[str, ...]
    requires_light: bool
    prefers_data_aligned: bool
    experimental: bool
    emits_by_design: bool
    recommended_samples: int
    node_contract: Tuple[str, ...]
    opacity_label: str = "单表面不透明度"

    def default_parameters(self) -> Dict[str, float]:
        return {name: float(value) for name, value in self.parameters.items()}

    def supports(self, control: str) -> bool:
        return control in self.controls

    def parameter_range(self, name: str) -> Tuple[float, float]:
        try:
            return self.ranges[name]
        except KeyError as exc:
            raise BlenderMaterialError(f"材质 {self.material_id} 没有参数 {name!r}。") from exc


# 统一参数范围；各材质只公开自己实际使用或有明确含义的参数。
_PARAMETER_RANGES: Dict[str, Tuple[float, float]] = {
    "opacity": (0.0, 1.0),
    "emission_compensation": (0.0, 2.0),
    "filter_strength": (0.0, 1.0),
    "emission_strength": (0.0, 5.0),
    "edge_opacity": (0.0, 1.0),
    "fresnel_power": (0.5, 8.0),
    "ior": (1.0, 2.5),
    "roughness": (0.0, 1.0),
    "reflection_strength": (0.0, 2.0),
    "absorption_density": (0.0, 50.0),
    "translucency": (0.0, 1.0),
    "tint_strength": (0.0, 1.0),
    "specular": (0.0, 1.0),
}

BOX_CONTROL_LABELS = {
    "opacity": "不透明度",
    "emission_compensation": "自发光补偿",
    "filter_strength": "滤色强度",
}
BOX_CONTROL_RANGES = {
    "opacity": (0.0, 1.0),
    "emission_compensation": (0.0, 2.0),
    "filter_strength": (0.0, 1.0),
}
BOX_CONTROL_ORDER = ("opacity", "emission_compensation", "filter_strength")

BOX_LAYOUTS: Dict[str, Dict[str, str]] = {
    "right_front_groups": {
        "label": "右侧／前侧分组沿 E 分层（默认）",
        "description": "四种无需辅助灯的薄板在右侧，四种需要辅助灯的薄板在前侧并上移半层；两组各沿 E 布置四层，移到数据体后高度交替。",
    },
    "separated_right": {
        "label": "右侧沿 E 分层",
        "description": "在右侧沿 E 方向布置八层；不与数据体在空间上重叠，透射类材质没有光路重叠。",
    },
    "data_aligned": {
        "label": "与数据体同轴、沿 E 分层重叠",
        "description": "以实际数据中心为 XY 中心，在数据 E 范围内布置八层；透射与滤色材质因此有真实光路。",
    },
}
DEFAULT_BOX_LAYOUT = "right_front_groups"
DEFAULT_MATERIAL_ID = "flat_emission"


_SPECS: Tuple[BoxMaterialSpec, ...] = (
    BoxMaterialSpec(
        material_id="flat_emission",
        label="平面发光",
        summary="Transparent＋Emission；与当前导出一致，颜色最稳定。",
        limitations=(
            "单表面不透明度默认 0.30；正对穿过封闭薄板时理论总不透明度为 1-(1-a)²。"
            "不需要辅助光。"
        ),
        parameters={"opacity": 0.30, "emission_strength": 1.0},
        ranges={"opacity": _PARAMETER_RANGES["opacity"],
                "emission_strength": _PARAMETER_RANGES["emission_strength"]},
        controls=("opacity",),
        requires_light=False,
        prefers_data_aligned=False,
        experimental=False,
        emits_by_design=True,
        recommended_samples=32,
        node_contract=("ShaderNodeBsdfTransparent", "ShaderNodeEmission", "ShaderNodeMixShader"),
    ),
    BoxMaterialSpec(
        material_id="matte",
        label="哑光薄片",
        summary="Transparent＋Principled；高粗糙度、弱高光，需要中性辅助光。",
        limitations="自发光补偿默认 0；强调明暗和厚度，不产生镜面高光色块。",
        parameters={
            "opacity": 0.50, "roughness": 0.92, "specular": 0.05,
            "emission_compensation": 0.0,
        },
        ranges={"opacity": _PARAMETER_RANGES["opacity"],
                "roughness": _PARAMETER_RANGES["roughness"],
                "specular": _PARAMETER_RANGES["specular"],
                "emission_compensation": _PARAMETER_RANGES["emission_compensation"]},
        controls=("opacity", "emission_compensation"),
        requires_light=True,
        prefers_data_aligned=False,
        experimental=False,
        emits_by_design=False,
        recommended_samples=64,
        node_contract=("ShaderNodeBsdfTransparent", "ShaderNodeBsdfPrincipled", "ShaderNodeMixShader"),
    ),
    BoxMaterialSpec(
        material_id="satin",
        label="缎面塑料",
        summary="Transparent＋Principled；中等粗糙度和高光，需要中性辅助光。",
        limitations="自发光补偿默认 0；高光不得遮盖数据，可继续在节点中调粗糙度。",
        parameters={
            "opacity": 0.40, "roughness": 0.30, "specular": 0.55,
            "emission_compensation": 0.0,
        },
        ranges={"opacity": _PARAMETER_RANGES["opacity"],
                "roughness": _PARAMETER_RANGES["roughness"],
                "specular": _PARAMETER_RANGES["specular"],
                "emission_compensation": _PARAMETER_RANGES["emission_compensation"]},
        controls=("opacity", "emission_compensation"),
        requires_light=True,
        prefers_data_aligned=False,
        experimental=False,
        emits_by_design=False,
        recommended_samples=64,
        node_contract=("ShaderNodeBsdfTransparent", "ShaderNodeBsdfPrincipled", "ShaderNodeMixShader"),
    ),
    BoxMaterialSpec(
        material_id="holographic",
        label="全息边缘",
        summary="Fresnel＋Transparent＋Emission；正面低遮挡，边缘和掠射角发光。",
        limitations="无折射；正面与边缘上限分别记录，边缘亮度由节点中的 Emission 强度决定。",
        parameters={
            "opacity": 0.06, "edge_opacity": 0.85, "fresnel_power": 2.5,
            "ior": 1.45, "emission_strength": 1.0,
        },
        ranges={"opacity": _PARAMETER_RANGES["opacity"],
                "edge_opacity": _PARAMETER_RANGES["edge_opacity"],
                "fresnel_power": _PARAMETER_RANGES["fresnel_power"],
                "ior": _PARAMETER_RANGES["ior"],
                "emission_strength": _PARAMETER_RANGES["emission_strength"]},
        controls=("opacity",),
        requires_light=False,
        prefers_data_aligned=False,
        experimental=False,
        emits_by_design=True,
        recommended_samples=64,
        node_contract=("ShaderNodeBsdfTransparent", "ShaderNodeEmission",
                       "ShaderNodeMixShader", "ShaderNodeFresnel", "ShaderNodeNewGeometry"),
        opacity_label="正面不透明度",
    ),
    BoxMaterialSpec(
        material_id="data_translucent",
        label="数据漫透射",
        summary="Transparent＋Translucent；由数据体的发光产生漫透射染色，薄板自身不发光。",
        limitations=(
            "实验性：需要与数据体重叠的布局和较高采样，效果依赖数据体亮度；"
            "不会用薄板自发光兜底。"
        ),
        parameters={"translucency": 0.65, "tint_strength": 0.60},
        ranges={"translucency": _PARAMETER_RANGES["translucency"],
                "tint_strength": _PARAMETER_RANGES["tint_strength"]},
        controls=(),
        requires_light=False,
        prefers_data_aligned=True,
        experimental=True,
        emits_by_design=False,
        recommended_samples=128,
        node_contract=("ShaderNodeBsdfTransparent", "ShaderNodeBsdfTranslucent", "ShaderNodeMixShader"),
    ),
    BoxMaterialSpec(
        material_id="transmission_filter",
        label="透射窗／滤色片",
        summary="中性或淡色 Transparent；不折射、不发光，直接透过数据颜色。",
        limitations=(
            "需与数据体重叠才有透射染色；右侧布局下当前没有光路重叠。"
            "封闭薄板前后两个表面会连续滤色两次；强度 0 为透射窗。"
        ),
        parameters={"filter_strength": 0.0},
        ranges={"filter_strength": _PARAMETER_RANGES["filter_strength"]},
        controls=("filter_strength",),
        requires_light=False,
        prefers_data_aligned=True,
        experimental=False,
        emits_by_design=False,
        recommended_samples=64,
        node_contract=("ShaderNodeBsdfTransparent",),
    ),
    BoxMaterialSpec(
        material_id="reflection_glass",
        label="无折射玻璃",
        summary="Fresnel＋Transparent＋Glossy；只保留反射与边缘高光，不弯曲数据。",
        limitations="需要中性辅助光；粗糙度和反射强度在 Blender 节点中编辑，不是 Glass BSDF 假透明混合。",
        parameters={
            "ior": 1.45, "roughness": 0.08, "reflection_strength": 1.0,
            "emission_compensation": 0.0,
        },
        ranges={"ior": _PARAMETER_RANGES["ior"],
                "roughness": _PARAMETER_RANGES["roughness"],
                "reflection_strength": _PARAMETER_RANGES["reflection_strength"],
                "emission_compensation": _PARAMETER_RANGES["emission_compensation"]},
        controls=("emission_compensation",),
        requires_light=True,
        prefers_data_aligned=False,
        experimental=False,
        emits_by_design=False,
        recommended_samples=96,
        node_contract=("ShaderNodeBsdfTransparent", "ShaderNodeBsdfAnisotropic",
                       "ShaderNodeMixShader", "ShaderNodeFresnel"),
    ),
    BoxMaterialSpec(
        material_id="optical_glass",
        label="真实玻璃／亚克力",
        summary="Glass BSDF＋Volume Absorption；展示用途的真实折射和厚度吸收。",
        limitations=(
            "折射会改变数据的视觉位置；IOR、粗糙度、吸收密度在 Blender 节点中编辑，"
            "不使用 Transparent 混合伪装物理不透明度。"
        ),
        parameters={
            "ior": 1.45, "roughness": 0.05, "absorption_density": 0.0,
            "emission_compensation": 0.0,
        },
        ranges={"ior": _PARAMETER_RANGES["ior"],
                "roughness": _PARAMETER_RANGES["roughness"],
                "absorption_density": _PARAMETER_RANGES["absorption_density"],
                "emission_compensation": _PARAMETER_RANGES["emission_compensation"]},
        controls=("emission_compensation",),
        requires_light=True,
        prefers_data_aligned=False,
        experimental=False,
        emits_by_design=False,
        recommended_samples=128,
        node_contract=("ShaderNodeBsdfGlass", "ShaderNodeVolumeAbsorption"),
    ),
)

MATERIAL_SPECS = _SPECS
MATERIAL_IDS = tuple(spec.material_id for spec in _SPECS)
MATERIAL_BY_ID: Dict[str, BoxMaterialSpec] = {spec.material_id: spec for spec in _SPECS}


def material_spec(material_id) -> BoxMaterialSpec:
    try:
        return MATERIAL_BY_ID[str(material_id)]
    except KeyError as exc:
        raise BlenderMaterialError(
            f"未知薄板材质 {material_id!r}；可用：{', '.join(MATERIAL_IDS)}"
        ) from exc


def material_label(material_id) -> str:
    return material_spec(material_id).label


def layout_spec(layout_id) -> Dict[str, str]:
    text = str(layout_id)
    if text not in BOX_LAYOUTS:
        raise BlenderMaterialError(
            f"未知薄板布局 {layout_id!r}；可用：{', '.join(BOX_LAYOUTS)}"
        )
    return dict(BOX_LAYOUTS[text])


def _finite_float(name, value) -> float:
    if isinstance(value, bool):
        raise BlenderMaterialError(f"参数 {name} 不接受布尔值。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BlenderMaterialError(f"参数 {name} 必须是有限数值。") from exc
    if not math.isfinite(number):
        raise BlenderMaterialError(f"参数 {name} 不能是 NaN 或 Inf。")
    return number


def resolve_parameters(material_id, overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, float]:
    """按注册表默认值补齐并校验参数；未知参数、NaN/Inf、越界均拒绝。"""
    spec = material_spec(material_id)
    provided = dict(overrides or {})
    unknown = set(provided).difference(spec.parameters)
    if unknown:
        raise BlenderMaterialError(
            f"材质 {spec.label} 不支持参数 {', '.join(sorted(unknown))}；"
            f"可用：{', '.join(sorted(spec.parameters))}"
        )
    result = spec.default_parameters()
    for name, value in provided.items():
        number = _finite_float(name, value)
        low, high = spec.parameter_range(name)
        if number < low or number > high:
            raise BlenderMaterialError(
                f"材质 {spec.label} 的参数 {name}={number:g} 超出范围 {low:g}–{high:g}。"
            )
        result[name] = number
    return result


def resolved_samples(material_ids) -> int:
    values = [material_spec(material_id).recommended_samples for material_id in material_ids]
    return max([32, *values])


def box_controls(material_id) -> Tuple[str, ...]:
    return tuple(material_spec(material_id).controls)


def control_label(material_id, control: str) -> str:
    spec = material_spec(material_id)
    if control == "opacity":
        return spec.opacity_label
    return BOX_CONTROL_LABELS.get(control, control)
