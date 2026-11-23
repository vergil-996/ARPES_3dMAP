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
