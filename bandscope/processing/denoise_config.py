from collections.abc import Mapping
from copy import deepcopy
from uuid import uuid4


GAUSSIAN_METHOD_NAME = "高斯平滑"
MEDIAN_METHOD_NAME = "中值滤波"
SG_METHOD_NAME = "Savitzky-Golay滤波"
WAVELET_METHOD_NAME = "小波去噪"
NLM_METHOD_NAME = "非局部均值"
LOW_RANK_METHOD_NAME = "低秩 SVD"

GAUSSIAN_METHOD_ALIASES = {GAUSSIAN_METHOD_NAME, "频域平滑"}
MEDIAN_METHOD_ALIASES = {MEDIAN_METHOD_NAME, "滑动平均"}
SG_METHOD_ALIASES = {SG_METHOD_NAME, "Savitzky-Golay婊ゆ尝"}
WAVELET_METHOD_ALIASES = {WAVELET_METHOD_NAME, "灏忔尝鍘诲櫔"}
NLMEANS_METHOD_ALIASES = {NLM_METHOD_NAME, "贝叶斯去噪"}
NLM_METHOD_ALIASES = NLMEANS_METHOD_ALIASES
LOW_RANK_METHOD_ALIASES = {LOW_RANK_METHOD_NAME}

DEFAULT_GAUSSIAN_PARAMS = {
    "sigma_kx": 1.0,
    "sigma_ky": 1.0,
    "sigma_e": 1.0,
}
DEFAULT_MEDIAN_PARAMS = {
    "size_kx": 3,
    "size_ky": 3,
    "size_e": 3,
}
DEFAULT_SG_PARAMS = {
    "window_length": 5,
    "polyorder": 2,
    "smoothing_axis": "e",
}
DEFAULT_WAVELET_PARAMS = {
    "wavelet": "db4",
    "level": 3,
    "threshold_rule": "universal",
    "threshold_mode": "soft",
    "strength": 1.0,
    "noise_sigma": 0.0,
}
DEFAULT_NLMEANS_PARAMS = {
    "patch_size": 5,
    "patch_distance": 3,
    "h_scale": 1.15,
    "noise_sigma": 0.0,
}
DEFAULT_LOW_RANK_PARAMS = {
    "rank": 3,
}

SG_AXIS_LABEL_TO_KEY = {
    "E轴": "e",
    "Kx轴": "kx",
    "Ky轴": "ky",
}
SG_AXIS_KEY_TO_LABEL = {value: key for key, value in SG_AXIS_LABEL_TO_KEY.items()}
MAX_MEDIAN_SIZE = 31
MAX_NLM_PATCH_SIZE = 15
MAX_NLM_PATCH_DISTANCE = 20
MAX_SG_POLYORDER = 4

WAVELET_OPTIONS = ["haar", "db2", "db4", "sym4", "coif1"]
THRESHOLD_RULE_OPTIONS = ["universal", "sure", "bayes"]
THRESHOLD_MODE_OPTIONS = ["soft", "hard"]

DENOISE_METHODS = (
    "None",
    GAUSSIAN_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
    NLM_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
)

_METHOD_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in (
        (GAUSSIAN_METHOD_NAME, GAUSSIAN_METHOD_ALIASES),
        (MEDIAN_METHOD_NAME, MEDIAN_METHOD_ALIASES),
        (SG_METHOD_NAME, SG_METHOD_ALIASES),
        (WAVELET_METHOD_NAME, WAVELET_METHOD_ALIASES),
        (NLM_METHOD_NAME, NLMEANS_METHOD_ALIASES),
        (LOW_RANK_METHOD_NAME, LOW_RANK_METHOD_ALIASES),
    )
    for alias in aliases
}


def canonical_method_name(name):
    if name is None:
        return "None"
    if not isinstance(name, str):
        raise ValueError(f"去噪方法名称必须是字符串，收到 {type(name).__name__}。")
    normalized = name.strip()
    if normalized == "None":
        return "None"
    try:
        return _METHOD_ALIAS_TO_CANONICAL[normalized]
    except KeyError as exc:
        raise ValueError(f"未知去噪方法：{name!r}。") from exc


_METHOD_DEFAULT_PARAMS = {
    GAUSSIAN_METHOD_NAME: DEFAULT_GAUSSIAN_PARAMS,
    MEDIAN_METHOD_NAME: DEFAULT_MEDIAN_PARAMS,
    SG_METHOD_NAME: DEFAULT_SG_PARAMS,
    WAVELET_METHOD_NAME: DEFAULT_WAVELET_PARAMS,
    NLM_METHOD_NAME: DEFAULT_NLMEANS_PARAMS,
    LOW_RANK_METHOD_NAME: DEFAULT_LOW_RANK_PARAMS,
}

_METHOD_LEGACY_CONTROL_KEYS = {
    GAUSSIAN_METHOD_NAME: "gaussian",
    MEDIAN_METHOD_NAME: "median",
    SG_METHOD_NAME: "sg",
    WAVELET_METHOD_NAME: "wavelet",
    NLM_METHOD_NAME: "nlmeans",
    LOW_RANK_METHOD_NAME: "low_rank",
}


def default_method_params(name):
    canonical = canonical_method_name(name)
    try:
        return deepcopy(_METHOD_DEFAULT_PARAMS[canonical])
    except KeyError as exc:
        raise ValueError(f"去噪方法 {name!r} 没有可配置参数。") from exc


def _copied_params(params, description):
    if params is None:
        return {}
    if not isinstance(params, Mapping):
        raise ValueError(f"{description}必须是键值映射。")
    return deepcopy(dict(params))


def _node_id(node_id):
    if node_id is None or node_id == "":
        return uuid4().hex
    return str(node_id)


def make_denoise_node(name, params=None, node_id=None, enabled=True):
    canonical = canonical_method_name(name)
    if canonical == "None":
        raise ValueError("None 不是可创建的去噪节点。")
    merged = default_method_params(canonical)
    merged.update(_copied_params(params, f"{canonical} 参数"))
    return {
        "id": _node_id(node_id),
        "label": canonical,
        "params": merged,
        "enabled": bool(enabled),
    }


def normalize_denoise_nodes(nodes, legacy_controls=None):
    if legacy_controls is None:
        legacy_controls = {}
    if not isinstance(legacy_controls, Mapping):
        raise ValueError("legacy_controls 必须是键值映射。")

    normalized = []
    used_ids = set()
    for index, spec in enumerate(nodes or (), start=1):
        try:
            if isinstance(spec, Mapping):
                name = spec.get("label") or spec.get("method") or spec.get("name")
                if name is None:
                    raise ValueError("去噪方法规格缺少 label、method 或 name。")
                params = spec.get("params")
                node_id = spec.get("id", spec.get("node_id"))
                enabled = spec.get("enabled", True)
            else:
                name = spec
                params = None
                node_id = None
                enabled = True

            canonical = canonical_method_name(name)
            if canonical == "None":
                continue
            legacy_key = _METHOD_LEGACY_CONTROL_KEYS[canonical]
            merged = _copied_params(
                legacy_controls.get(legacy_key),
                f"旧版 {canonical} 参数",
            )
            merged.update(_copied_params(params, f"{canonical} 参数"))
            node = make_denoise_node(canonical, merged, node_id, enabled)
        except ValueError as exc:
            raise ValueError(f"第 {index} 个去噪节点无效：{exc}") from exc

        while node["id"] in used_ids:
            node["id"] = uuid4().hex
        used_ids.add(node["id"])
        normalized.append(node)
    return normalized
