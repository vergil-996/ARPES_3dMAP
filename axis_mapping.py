import numpy as np


AXIS_MAPPING_VERSION = "mat_keyed_v2"

NPZ_COORD_CANDIDATES = {
    "X": ("kx", "x", "X"),
    "Y": ("ky", "y", "Y"),
    "E": ("E", "energy", "En", "Ef"),
    "T": ("time", "delay", "t", "T"),
}

MAT_COORD_CANDIDATES = {
    "X": ("kx", "X", "x"),
    "Y": ("ky", "Y", "y"),
    "E": NPZ_COORD_CANDIDATES["E"],
    "T": NPZ_COORD_CANDIDATES["T"],
}

ROLE_ALIASES = {
    "kx": "X",
    "x": "X",
    "X": "X",
    "ky": "Y",
    "y": "Y",
    "Y": "Y",
    "E": "E",
    "energy": "E",
    "En": "E",
    "Ef": "E",
    "time": "T",
    "delay": "T",
    "t": "T",
    "T": "T",
}


def find_first_key(keys, candidates):
    return next((name for name in candidates if name in keys), None)


def normalize_role_name(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    value = str(value).strip()
    return ROLE_ALIASES.get(value, value)


def metadata_axis_roles(mapping_version, axis_order, role_order):
    if _scalar_text(mapping_version) != AXIS_MAPPING_VERSION or axis_order is None:
        return None

    normalized = [
        normalize_role_name(item)
        for item in np.asarray(axis_order).reshape(-1).tolist()
    ]
    if len(normalized) != len(role_order) or set(normalized) != set(role_order):
        return None
    return {role: normalized.index(role) for role in role_order}


def legacy_hdf5_axis_roles(shape, coord_lengths, role_order):
    shape = tuple(int(size) for size in shape)
    x_len = coord_lengths.get("X")
    y_len = coord_lengths.get("Y")
    e_len = coord_lengths.get("E")
    t_len = coord_lengths.get("T")

    if len(shape) == 3 and {"X", "Y", "E"} == set(role_order):
        if (
            x_len is not None
            and y_len is not None
            and e_len is not None
            and shape[0] == e_len
            and shape[1] == x_len
            and shape[2] == y_len
            and not (shape[0] == x_len and shape[1] == y_len and shape[2] == e_len)
        ):
            return {"X": 1, "Y": 2, "E": 0}

    if len(shape) == 4 and {"X", "Y", "E", "T"} == set(role_order):
        if (
            x_len is not None
            and y_len is not None
            and t_len is not None
            and shape[0] == t_len
            and shape[2] == x_len
            and shape[3] == y_len
            and (e_len is None or shape[1] == e_len)
            and not (
                shape[0] == x_len
                and shape[1] == y_len
                and (e_len is None or shape[2] == e_len)
                and shape[3] == t_len
            )
        ):
            return {"X": 2, "Y": 3, "E": 1, "T": 0}

    return None


def infer_axis_roles(shape, coord_lengths, role_order):
    shape = tuple(int(size) for size in shape)
    roles = {}
    remaining = list(range(len(shape)))

    def assign_unique(role):
        length = coord_lengths.get(role)
        if length is None:
            return
        matches = [index for index in remaining if shape[index] == length]
        if len(matches) == 1:
            roles[role] = matches[0]
            remaining.remove(matches[0])

    def assign_first(role):
        length = coord_lengths.get(role)
        if length is None:
            return
        matches = [index for index in remaining if shape[index] == length]
        if matches:
            roles[role] = matches[0]
            remaining.remove(matches[0])

    for role in ("T", "E"):
        if role in role_order:
            assign_unique(role)
    for role in ("X", "Y"):
        if role in role_order:
            assign_first(role)

    fallback_roles = []
    if ("X" in roles or "Y" in roles) and "E" in role_order and "E" not in roles:
        fallback_roles.append("E")
    fallback_roles.extend(
        role for role in role_order if role not in roles and role not in fallback_roles
    )
    for role in fallback_roles:
        if not remaining:
            break
        roles[role] = remaining.pop(0)

    if remaining:
        raise ValueError(
            f"无法识别多余维度: shape={shape}, remaining={remaining}, roles={roles}"
        )
    return roles


def canonicalize_array(values, roles, role_order):
    present_roles = [role for role in role_order if role in roles]
    present_axes = [roles[role] for role in present_roles]
    if len(set(present_axes)) != len(present_axes):
        raise ValueError(f"轴映射重复: {roles}")
    if set(present_axes) != set(range(values.ndim)):
        raise ValueError(f"轴映射不完整: shape={values.shape}, roles={roles}")

    canonical = values.transpose(*present_axes) if present_axes else values
    for axis, role in enumerate(role_order):
        if role not in roles:
            canonical = np.expand_dims(canonical, axis=axis)
    return canonical


def role_order_for_shape(shape, has_time_coord):
    return ["X", "Y", "E"] + (["T"] if has_time_coord or len(shape) == 4 else [])


def _scalar_text(value):
    if value is None:
        return ""
    values = np.asarray(value).reshape(-1)
    if values.size == 0:
        return ""
    item = values[0]
    if isinstance(item, bytes):
        return item.decode("utf-8", errors="ignore")
    return str(item)
