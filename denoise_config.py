SG_METHOD_NAME = "Savitzky-Golay滤波"
WAVELET_METHOD_NAME = "小波去噪"

# Keep aliases emitted by historical builds whose UTF-8 labels were decoded as GBK.
SG_METHOD_ALIASES = {SG_METHOD_NAME, "Savitzky-Golay婊ゆ尝"}
WAVELET_METHOD_ALIASES = {WAVELET_METHOD_NAME, "灏忔尝鍘诲櫔"}

DEFAULT_SG_PARAMS = {
    "window_length": 5,
    "polyorder": 2,
    "smoothing_axis": "e",
}
SG_AXIS_LABEL_TO_KEY = {
    "E轴": "e",
    "Kx轴": "kx",
    "Ky轴": "ky",
}
SG_AXIS_KEY_TO_LABEL = {value: key for key, value in SG_AXIS_LABEL_TO_KEY.items()}
MAX_SG_POLYORDER = 4

DEFAULT_WAVELET_PARAMS = {
    "wavelet": "db4",
    "level": 3,
    "threshold_rule": "universal",
    "threshold_mode": "soft",
    "strength": 1.0,
}
WAVELET_OPTIONS = ["haar", "db2", "db4", "sym4", "coif1"]
THRESHOLD_RULE_OPTIONS = ["universal", "sure", "bayes"]
THRESHOLD_MODE_OPTIONS = ["soft", "hard"]

DENOISE_METHODS = (
    "None",
    "频域平滑",
    "滑动平均",
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
    "卡尔曼滤波",
    "贝叶斯去噪",
)
