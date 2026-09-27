"""平带倍率的数学与参数校验（计划 §3、§8「数学和数据」）。

直接在源码树里导入 ``plugins.flat_band_opacity.effect``：它只用 NumPy，
不依赖 Qt / VTK，因此这些检查在无图形环境下也能跑。
"""
import math
import unittest

import numpy as np

from bandscope.extensions.api import EnergyAxisSpec
from plugins.flat_band_opacity.effect import (
    DEFAULT_BACKGROUND,
    DEFAULT_GAIN,
    EffectState,
    FlatBand,
    MIN_GAIN,
    gaussian,
    multiplier_for,
    preset_unit_mismatch,
    suggested_fwhm,
)

FWHM_TO_HALF = 4.0 * math.log(2.0)


def axis(values):
    values = np.asarray(values, dtype=np.float64)
    return EnergyAxisSpec(
        values=values,
        unit="eV",
        source="coord",
        roi_range=(float(values.min()), float(values.max())),
        full_range=(float(values.min()), float(values.max())),
    )


def make_band(center=None, fwhm=None, gain=DEFAULT_GAIN, enabled=True, band_id=None):
    band = FlatBand(band_id=band_id) if band_id else FlatBand()
    band.enabled = enabled
    band.center = center
    band.fwhm = fwhm
    band.gain = gain
    return band


class GaussianTests(unittest.TestCase):
    def test_peak_is_one_and_half_maximum_is_at_half_fwhm(self):
        values = np.linspace(-1.0, 1.0, 4001)
        profile = gaussian(values, 0.2, 0.4)
        self.assertAlmostEqual(float(profile.max()), 1.0, places=12)
        half = gaussian(np.array([0.2 - 0.2, 0.2 + 0.2]), 0.2, 0.4)
        np.testing.assert_allclose(half, [0.5, 0.5], rtol=1e-9)

    def test_degenerate_width_is_not_a_crash(self):
        values = np.linspace(-1.0, 1.0, 11)
        for width in (0.0, -1.0, float("nan"), None):
            np.testing.assert_array_equal(
                gaussian(values, 0.0, width), np.zeros(values.shape)
            )


class MultiplierTests(unittest.TestCase):
    def test_no_bands_means_no_effect(self):
        values = np.linspace(-1.0, 1.0, 9)
        self.assertIsNone(multiplier_for([], values))
        self.assertIsNone(multiplier_for([make_band(enabled=False)], values))
        self.assertIsNone(multiplier_for([make_band()], values))

    def test_band_count_from_zero_to_many(self):
        values = np.linspace(-1.0, 1.0, 201)
        for count in (0, 1, 3, 8, 24):
            centers = np.linspace(-0.8, 0.8, count) if count else []
            bands = [make_band(float(c), 0.05, 0.8) for c in centers]
            result = multiplier_for(bands, values)
            if count == 0:
                self.assertIsNone(result)
                continue
            self.assertEqual(result.shape, values.shape)
            for center in centers:
                index = int(np.argmin(np.abs(values - center)))
                # 采样点未必正好落在峰位，与解析值比较而不是与 1.0 比较。
                expected = DEFAULT_BACKGROUND + 0.8 * float(
                    gaussian(np.array([values[index]]), center, 0.05)[0]
                )
                self.assertAlmostEqual(float(result[index]), expected, places=6)

    def test_far_from_every_band_the_multiplier_is_the_background(self):
        values = np.linspace(-1.0, 1.0, 401)
        bands = [make_band(0.0, 0.1, 0.8)]
        result = multiplier_for(bands, values, background=0.2)
        np.testing.assert_allclose(result[values < -0.5], 0.2)
        np.testing.assert_allclose(result[values > 0.5], 0.2)

    def test_overlapping_bands_use_the_maximum_not_the_sum(self):
        values = np.linspace(-1.0, 1.0, 2001)
        single = multiplier_for([make_band(0.0, 0.2, 0.8)], values, 0.2)
        doubled = multiplier_for(
            [make_band(0.0, 0.2, 0.8), make_band(0.0, 0.2, 0.8)], values, 0.2
        )
        np.testing.assert_allclose(doubled, single)

        offset = multiplier_for(
            [make_band(0.0, 0.2, 0.8), make_band(0.05, 0.2, 0.8)], values, 0.2
        )
        self.assertLessEqual(float(offset.max()), 0.2 + 0.8 + 1e-12)

    def test_each_band_keeps_its_own_gain(self):
        values = np.linspace(-1.0, 1.0, 2001)
        bands = [
            make_band(-0.5, 0.08, 1.0),
            make_band(0.5, 0.08, 3.0),
        ]
        result = multiplier_for(bands, values, 0.2)
        weak = float(result[np.argmin(np.abs(values + 0.5))])
        strong = float(result[np.argmin(np.abs(values - 0.5))])
        self.assertAlmostEqual(weak, 1.2, places=4)
        self.assertAlmostEqual(strong, 3.2, places=4)

    def test_raising_one_band_does_not_leak_into_another(self):
        values = np.linspace(-1.0, 1.0, 2001)
        before = multiplier_for(
            [make_band(-0.5, 0.08, 0.8), make_band(0.5, 0.08, 0.8)], values, 0.2
        )
        after = multiplier_for(
            [make_band(-0.5, 0.08, 0.8), make_band(0.5, 0.08, 4.0)], values, 0.2
        )
        weak = np.argmin(np.abs(values + 0.5))
        self.assertAlmostEqual(float(before[weak]), float(after[weak]), places=9)

    def test_zero_gain_and_extreme_background_are_defined(self):
        values = np.linspace(-0.5, 0.5, 51)
        zero = multiplier_for([make_band(0.0, 0.1, 0.0)], values, 0.2)
        np.testing.assert_allclose(zero, 0.2)

        # 高斯只在无穷远处才严格为 0，远端只能是“小到看不见”。
        dark = multiplier_for([make_band(0.0, 0.1, 0.8)], values, 0.0)
        self.assertLess(float(dark.min()), 1e-9)
        self.assertAlmostEqual(float(dark.max()), 0.8, places=4)

        opaque = multiplier_for([make_band(0.0, 0.1, 0.8)], values, 1.0)
        self.assertEqual(float(opaque.min()), 1.0)

    def test_multiplier_is_bounded_and_finite(self):
        values = np.linspace(-1.0, 1.0, 101)
        bands = [make_band(0.0, 0.1, 5.0)]
        result = multiplier_for(bands, values, 1.0)
        self.assertTrue(np.all(np.isfinite(result)))
        self.assertGreaterEqual(float(result.min()), 0.0)
        self.assertLessEqual(float(result.max()), 1.0 + 5.0)


class ValidationTests(unittest.TestCase):
    def test_enabled_row_needs_finite_center_positive_width_non_negative_gain(self):
        self.assertIsNotNone(make_band(None, 0.1, 0.8).invalid_reason())
        self.assertIsNotNone(make_band(0.0, None, 0.8).invalid_reason())
        self.assertIsNotNone(make_band(0.0, 0.0, 0.8).invalid_reason())
        self.assertIsNotNone(make_band(0.0, -0.1, 0.8).invalid_reason())
        self.assertIsNotNone(make_band(0.0, 0.1, -1.0).invalid_reason())
        self.assertIsNotNone(make_band(float("nan"), 0.1, 0.8).invalid_reason())
        self.assertIsNone(make_band(0.0, 0.1, 0.0).invalid_reason())

    def test_disabled_row_is_reported_as_disabled(self):
        band = make_band(None, None, 0.8, enabled=False)
        self.assertFalse(band.active)

    def test_negative_gain_is_rejected_by_the_model(self):
        values = np.linspace(-0.5, 0.5, 21)
        band = make_band(0.0, 0.1, -2.0)
        self.assertIsNone(multiplier_for([band], values))


class StateTests(unittest.TestCase):
    def test_round_trip_preserves_count_ids_and_parameters(self):
        state = EffectState(background=0.35)
        state.bands = [
            make_band(-0.4, 0.03, 1.7, band_id="alpha"),
            make_band(0.1, 0.06, 0.0, enabled=False, band_id="beta"),
        ]
        restored = EffectState.from_state(state.to_state())
        self.assertEqual(len(restored.bands), 2)
        self.assertAlmostEqual(restored.background, 0.35)
        self.assertEqual([b.band_id for b in restored.bands], ["alpha", "beta"])
        self.assertAlmostEqual(restored.bands[0].center, -0.4)
        self.assertAlmostEqual(restored.bands[0].gain, 1.7)
        self.assertFalse(restored.bands[1].enabled)

    def test_restore_tolerates_missing_and_unknown_fields(self):
        state = EffectState.from_state({"bands": [{"center": 0.5}]})
        self.assertEqual(len(state.bands), 1)
        self.assertTrue(state.bands[0].enabled)
        self.assertIsNone(state.bands[0].fwhm)
        self.assertEqual(EffectState.from_state(None).bands, [])
        self.assertEqual(EffectState.from_state({}).background, DEFAULT_BACKGROUND)

    def test_reset_keeps_count_and_centers_and_restores_suggestions(self):
        values = np.linspace(-1.0, 1.0, 50)
        spec = axis(values)
        state = EffectState(background=0.9)
        state.bands = [make_band(-0.3, 0.001, 4.0), make_band(0.3, 0.002, 0.1)]
        state.reset_display_parameters(spec)
        self.assertAlmostEqual(state.background, DEFAULT_BACKGROUND)
        self.assertEqual(len(state.bands), 2)
        self.assertAlmostEqual(state.bands[0].center, -0.3)
        self.assertAlmostEqual(state.bands[0].gain, DEFAULT_GAIN)
        self.assertAlmostEqual(state.bands[0].fwhm, suggested_fwhm(spec))

    def test_suggested_fwhm_follows_the_sample_spacing(self):
        coarse = axis(np.linspace(-1.0, 1.0, 5))
        fine = axis(np.linspace(-1.0, 1.0, 401))
        self.assertAlmostEqual(suggested_fwhm(coarse), 0.5 * 4.0, places=6)
        self.assertAlmostEqual(suggested_fwhm(fine), 0.005 * 4.0, places=6)
        self.assertEqual(suggested_fwhm(None), 0.0)

    def test_preset_records_units_without_converting(self):
        spec = axis(np.linspace(-1.0, 1.0, 11))
        state = EffectState()
        state.bands = [make_band(0.25, 0.05, 0.8)]
        preset = state.to_preset(spec)
        self.assertEqual(preset["energy"]["unit"], "eV")
        self.assertIsNone(preset_unit_mismatch(preset, spec))

        other = EnergyAxisSpec(
            values=spec.values,
            unit="meV",
            source="coord",
            roi_range=spec.roi_range,
            full_range=spec.full_range,
        )
        warning = preset_unit_mismatch(preset, other)
        self.assertIsNotNone(warning)
        self.assertIn("eV", warning)
        self.assertIn("meV", warning)

    def test_preset_mismatch_is_silent_without_recorded_units(self):
        spec = axis(np.linspace(-1.0, 1.0, 11))
        self.assertIsNone(preset_unit_mismatch({"bands": []}, spec))


class GainBoundsTests(unittest.TestCase):
    def test_gain_is_clamped_into_the_documented_range(self):
        band = make_band(0.0, 0.1, 99.0)
        self.assertAlmostEqual(band.normalized_gain, 5.0)
        band = make_band(0.0, 0.1, -99.0)
        self.assertAlmostEqual(band.normalized_gain, MIN_GAIN)


class RestoreRobustnessTests(unittest.TestCase):
    """预设文件是用户可以手改的，restore_state 不能因为字段怪就抛异常。

    调用链是「导入预设」按钮的 Qt 槽：未捕获异常会让整个进程退出。
    """

    def _plugin(self):
        from plugins.flat_band_opacity.entry import FlatBandOpacityPlugin

        return FlatBandOpacityPlugin()

    def test_schema_field_of_any_type_is_tolerated(self):
        plugin = self._plugin()
        for payload in (
            {"schema": "v2", "bands": []},
            {"schema": None, "bands": []},
            {"schema": [], "bands": []},
            {"schema": {"nested": 1}, "bands": []},
            {"bands": [{"center": "abc", "fwhm": None, "gain": []}]},
            {"background": "dark", "bands": "not-a-list"},
            {"bands": [None, 42, "x"]},
        ):
            with self.subTest(payload=payload):
                plugin.restore_state(payload)
        self.assertEqual(plugin.export_state()["bands"], [])

    def test_empty_and_none_states_are_ignored(self):
        plugin = self._plugin()
        plugin.restore_state({})
        plugin.restore_state(None)
        self.assertEqual(plugin.export_state()["bands"], [])


if __name__ == "__main__":
    unittest.main()
