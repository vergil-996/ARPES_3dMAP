import unittest

from bandscope.processing.denoise_config import (
    DEFAULT_GAUSSIAN_PARAMS,
    DEFAULT_LOW_RANK_PARAMS,
    DEFAULT_MEDIAN_PARAMS,
    DEFAULT_NLMEANS_PARAMS,
    DEFAULT_SG_PARAMS,
    DEFAULT_WAVELET_PARAMS,
    GAUSSIAN_METHOD_NAME,
    LOW_RANK_METHOD_NAME,
    MEDIAN_METHOD_NAME,
    NLM_METHOD_NAME,
    SG_METHOD_NAME,
    WAVELET_METHOD_NAME,
    default_method_params,
    make_denoise_node,
    normalize_denoise_nodes,
)


METHOD_DEFAULTS = (
    (GAUSSIAN_METHOD_NAME, DEFAULT_GAUSSIAN_PARAMS),
    (MEDIAN_METHOD_NAME, DEFAULT_MEDIAN_PARAMS),
    (SG_METHOD_NAME, DEFAULT_SG_PARAMS),
    (WAVELET_METHOD_NAME, DEFAULT_WAVELET_PARAMS),
    (NLM_METHOD_NAME, DEFAULT_NLMEANS_PARAMS),
    (LOW_RANK_METHOD_NAME, DEFAULT_LOW_RANK_PARAMS),
)


class DefaultMethodParamsTests(unittest.TestCase):
    def test_every_method_returns_an_independent_copy_of_its_defaults(self):
        for name, expected in METHOD_DEFAULTS:
            with self.subTest(method=name):
                first = default_method_params(name)
                second = default_method_params(name)
                self.assertEqual(first, expected)
                self.assertIsNot(first, second)
                self.assertIsNot(first, expected)
                first[next(iter(first))] = "changed"
                self.assertEqual(second, expected)
                self.assertEqual(default_method_params(name), expected)

    def test_aliases_resolve_to_the_same_default_parameters(self):
        self.assertEqual(default_method_params("频域平滑"), DEFAULT_GAUSSIAN_PARAMS)
        self.assertEqual(default_method_params("滑动平均"), DEFAULT_MEDIAN_PARAMS)
        self.assertEqual(default_method_params("贝叶斯去噪"), DEFAULT_NLMEANS_PARAMS)


class DenoiseNodeTests(unittest.TestCase):
    def test_repeated_methods_have_unique_ids_and_independent_partial_params(self):
        first = make_denoise_node(
            GAUSSIAN_METHOD_NAME,
            {"sigma_kx": 2.5},
            enabled=0,
        )
        second = make_denoise_node(GAUSSIAN_METHOD_NAME)

        self.assertNotEqual(first["id"], second["id"])
        self.assertIsInstance(first["id"], str)
        self.assertIsInstance(second["id"], str)
        self.assertIs(first["enabled"], False)
        self.assertIs(second["enabled"], True)
        self.assertEqual(
            first["params"],
            {**DEFAULT_GAUSSIAN_PARAMS, "sigma_kx": 2.5},
        )
        self.assertIsNot(first["params"], second["params"])
        first["params"]["sigma_ky"] = 8.0
        self.assertEqual(second["params"], DEFAULT_GAUSSIAN_PARAMS)

    def test_explicit_id_is_preserved_and_input_params_are_copied(self):
        params = {"rank": 2}
        node = make_denoise_node(
            LOW_RANK_METHOD_NAME,
            params,
            node_id="low-rank-node",
            enabled=1,
        )

        self.assertEqual(
            node,
            {
                "id": "low-rank-node",
                "label": LOW_RANK_METHOD_NAME,
                "params": {"rank": 2},
                "enabled": True,
            },
        )
        self.assertIsNot(node["params"], params)
        params["rank"] = 5
        self.assertEqual(node["params"]["rank"], 2)


class DenoiseNodeNormalizationTests(unittest.TestCase):
    def test_none_entries_are_filtered_and_legacy_aliases_are_canonicalized(self):
        nodes = normalize_denoise_nodes(
            [
                None,
                "None",
                {"label": "None"},
                "频域平滑",
                {"method": "滑动平均"},
                {"name": "贝叶斯去噪"},
            ]
        )

        self.assertEqual(
            [node["label"] for node in nodes],
            [GAUSSIAN_METHOD_NAME, MEDIAN_METHOD_NAME, NLM_METHOD_NAME],
        )
        self.assertEqual(len({node["id"] for node in nodes}), 3)

    def test_legacy_controls_apply_to_every_repeated_instance_with_spec_override(self):
        legacy_controls = {
            "gaussian": {"sigma_kx": 0.75, "sigma_ky": 1.25, "sigma_e": 0.0}
        }
        nodes = normalize_denoise_nodes(
            [
                GAUSSIAN_METHOD_NAME,
                {
                    "id": "custom-gaussian",
                    "label": GAUSSIAN_METHOD_NAME,
                    "params": {"sigma_kx": 2.0},
                    "enabled": False,
                },
            ],
            legacy_controls,
        )

        self.assertEqual(nodes[0]["params"], legacy_controls["gaussian"])
        self.assertEqual(
            nodes[1]["params"],
            {"sigma_kx": 2.0, "sigma_ky": 1.25, "sigma_e": 0.0},
        )
        self.assertIs(nodes[1]["enabled"], False)
        self.assertIsNot(nodes[0]["params"], nodes[1]["params"])
        self.assertIsNot(nodes[0]["params"], legacy_controls["gaussian"])
        legacy_controls["gaussian"]["sigma_ky"] = 9.0
        self.assertEqual(nodes[0]["params"]["sigma_ky"], 1.25)

    def test_each_legacy_control_group_merges_with_remaining_defaults(self):
        cases = (
            (GAUSSIAN_METHOD_NAME, "gaussian", "sigma_kx", 2.0),
            (MEDIAN_METHOD_NAME, "median", "size_kx", 5),
            (SG_METHOD_NAME, "sg", "window_length", 7),
            (WAVELET_METHOD_NAME, "wavelet", "level", 2),
            (NLM_METHOD_NAME, "nlmeans", "patch_size", 3),
            (LOW_RANK_METHOD_NAME, "low_rank", "rank", 2),
        )
        defaults = dict(METHOD_DEFAULTS)
        for name, control_key, param_key, value in cases:
            with self.subTest(method=name):
                nodes = normalize_denoise_nodes(
                    [name],
                    {control_key: {param_key: value}},
                )
                self.assertEqual(
                    nodes[0]["params"],
                    {**defaults[name], param_key: value},
                )

    def test_ids_are_stable_and_duplicate_or_missing_ids_become_unique(self):
        source = [
            {"id": "shared", "label": GAUSSIAN_METHOD_NAME},
            {"id": "shared", "label": MEDIAN_METHOD_NAME},
            {"label": SG_METHOD_NAME},
            {"id": "", "label": WAVELET_METHOD_NAME},
        ]
        nodes = normalize_denoise_nodes(source)

        ids = [node["id"] for node in nodes]
        self.assertEqual(ids[0], "shared")
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue(all(isinstance(node_id, str) and node_id for node_id in ids))
        self.assertEqual(
            [node["id"] for node in normalize_denoise_nodes(nodes)],
            ids,
        )

    def test_normalization_copies_params_and_normalizes_enabled_to_bool(self):
        source = [
            {
                "label": WAVELET_METHOD_NAME,
                "params": {"level": 2},
                "enabled": 1,
            },
            {
                "label": LOW_RANK_METHOD_NAME,
                "params": {"rank": 2},
                "enabled": 0,
            },
        ]
        nodes = normalize_denoise_nodes(source)

        self.assertIs(nodes[0]["enabled"], True)
        self.assertIs(nodes[1]["enabled"], False)
        self.assertIsNot(nodes[0]["params"], source[0]["params"])
        source[0]["params"]["level"] = 4
        self.assertEqual(nodes[0]["params"]["level"], 2)
        self.assertEqual(nodes[0]["params"]["wavelet"], "db4")
        self.assertEqual(nodes[1]["params"], {"rank": 2})


class DenoiseConfigErrorTests(unittest.TestCase):
    def test_unknown_names_raise_clear_value_errors(self):
        calls = (
            lambda: default_method_params("不存在的方法"),
            lambda: make_denoise_node("卡尔曼滤波"),
            lambda: normalize_denoise_nodes(["高斯平滑", "不存在的方法"]),
            lambda: normalize_denoise_nodes([{"label": "不存在的方法"}]),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(ValueError, "未知去噪方法"):
                    call()

        with self.assertRaisesRegex(ValueError, "名称必须是字符串"):
            make_denoise_node(42)
        with self.assertRaisesRegex(ValueError, "没有可配置参数"):
            default_method_params("None")
        with self.assertRaisesRegex(ValueError, "不是可创建的去噪节点"):
            make_denoise_node(None)

    def test_invalid_parameter_containers_raise_value_errors(self):
        calls = (
            lambda: make_denoise_node(GAUSSIAN_METHOD_NAME, [1.0]),
            lambda: normalize_denoise_nodes(
                [{"label": GAUSSIAN_METHOD_NAME, "params": [1.0]}]
            ),
            lambda: normalize_denoise_nodes(
                [GAUSSIAN_METHOD_NAME],
                {"gaussian": [1.0]},
            ),
            lambda: normalize_denoise_nodes([GAUSSIAN_METHOD_NAME], []),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaisesRegex(ValueError, "键值映射"):
                    call()


if __name__ == "__main__":
    unittest.main()
