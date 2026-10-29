"""坐标单位约定：哪些轴可以在缺少单位元数据时给出默认单位。

空间轴与能量轴只如实转述数据来源的单位元数据，缺省不猜。时间轴（泵浦-探测
延迟）是唯一例外：数据来源没有声明 ``time_unit`` 时按 fs 呈现——这是实验室的
时间轴约定，不是从坐标数值推断出来的；坐标本身仍原样使用文件数组或索引回退。
"""

#: 时间轴在宿主里的坐标键（``AnalyzerCore.coords['delay']``）。
TIME_AXIS_KEY = "delay"

#: 数据来源未声明时间轴单位时的默认单位。
DEFAULT_TIME_UNIT = "fs"


def resolve_axis_unit(axis_key, unit):
    """返回一根轴可用于显示与导出的单位。

    显式单位优先（去空白后原样转述）；只有时间轴在缺单位时回退到
    :data:`DEFAULT_TIME_UNIT`。其它轴缺单位时返回空串，由调用方决定显示
    「坐标值」还是 index 标签。
    """

    text = str(unit or "").strip()
    if text:
        return text
    if str(axis_key) == TIME_AXIS_KEY:
        return DEFAULT_TIME_UNIT
    return ""
