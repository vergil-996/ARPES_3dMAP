import unittest

import numpy as np

from bandscope.core.opacity_curve import (
    OPACITY_LINEAR,
    OPACITY_SMOOTH,
    evaluate_opacity_curve,
    insert_opacity_point,
    pchip_opacity_segments,
    sanitized_opacity_segments,
)


class OpacityCurveTests(unittest.TestCase):
    def test_linear_vertical_steps_use_the_last_point_at_exact_intensity(self):
        points = [[0.0, 0.0], [0.4, 0.1], [0.4, 0.8], [0.4, 0.6], [1.0, 1.0]]
        step = 0.4
        samples = np.array([
            np.nextafter(step, 0.0), step, np.nextafter(step, 1.0),
        ])
        actual = evaluate_opacity_curve(samples, points, interpolation=OPACITY_LINEAR)
        np.testing.assert_allclose(actual, [0.1, 0.6, 0.6], atol=1.0e-14)

        descending = [[0.0, 1.0], [0.7, 0.9], [0.7, 0.2], [1.0, 0.0]]
        actual = evaluate_opacity_curve(
            np.array([np.nextafter(0.7, 0.0), 0.7, np.nextafter(0.7, 1.0)]),
            descending, interpolation=OPACITY_LINEAR,
        )
        np.testing.assert_allclose(actual, [0.9, 0.2, 0.2], atol=1.0e-14)

    def test_smooth_curve_passes_through_points_and_preserves_plateau(self):
        points = [[0.0, 0.0], [0.2, 0.5], [0.4, 0.5], [0.7, 0.5], [1.0, 1.0]]
        samples = np.linspace(0.0, 1.0, 1001)
        actual = evaluate_opacity_curve(samples, points, interpolation=OPACITY_SMOOTH)
        np.testing.assert_allclose(
            evaluate_opacity_curve([point[0] for point in points], points,
                                   interpolation=OPACITY_SMOOTH),
            [point[1] for point in points], atol=1.0e-14,
        )
        plateau = (samples >= 0.2) & (samples <= 0.7)
        np.testing.assert_allclose(actual[plateau], 0.5, atol=1.0e-14)

    def test_smooth_curve_does_not_overshoot_local_extrema(self):
        points = [[0.0, 0.1], [0.2, 0.8], [0.45, 0.2], [0.7, 0.9], [1.0, 0.3]]
        coefficients = pchip_opacity_segments(points)
        for index, coefficient in enumerate(coefficients):
            left, right = points[index:index + 2]
            samples = np.linspace(left[0], right[0], 101)
            actual = evaluate_opacity_curve(
                samples, points, interpolation=OPACITY_SMOOTH, segments=coefficients,
            )
            self.assertGreaterEqual(float(actual.min()), min(left[1], right[1]) - 1.0e-12)
            self.assertLessEqual(float(actual.max()), max(left[1], right[1]) + 1.0e-12)

    def test_smooth_split_and_consecutive_insertions_preserve_curve(self):
        points = [[0.0, 0.0], [0.25, 0.8], [0.6, 0.2], [1.0, 0.9]]
        samples = np.linspace(0.0, 1.0, 2001)
        expected = evaluate_opacity_curve(samples, points, interpolation=OPACITY_SMOOTH)
        inserted, segments, _index = insert_opacity_point(
            points, 0.4, interpolation=OPACITY_SMOOTH,
        )
        actual = evaluate_opacity_curve(
            samples, inserted, interpolation=OPACITY_SMOOTH, segments=segments,
        )
        np.testing.assert_allclose(actual, expected, atol=2.0e-15, rtol=0.0)
        self.assertIsNotNone(sanitized_opacity_segments(inserted, segments))

        inserted_again, segments_again, _index = insert_opacity_point(
            inserted, 0.75, interpolation=OPACITY_SMOOTH, segments=segments,
        )
        actual = evaluate_opacity_curve(
            samples, inserted_again, interpolation=OPACITY_SMOOTH, segments=segments_again,
        )
        np.testing.assert_allclose(actual, expected, atol=3.0e-15, rtol=0.0)

    def test_smooth_calculation_restarts_at_a_step(self):
        points = [[0.0, 0.0], [0.4, 0.1], [0.4, 0.8], [1.0, 1.0]]
        expected = evaluate_opacity_curve(
            np.linspace(0.0, 1.0, 501), points, interpolation=OPACITY_SMOOTH,
        )
        split, segments, _index = insert_opacity_point(
            points, 0.2, interpolation=OPACITY_SMOOTH,
        )
        actual = evaluate_opacity_curve(
            np.linspace(0.0, 1.0, 501), split,
            interpolation=OPACITY_SMOOTH, segments=segments,
        )
        np.testing.assert_allclose(actual, expected, atol=2.0e-15, rtol=0.0)


if __name__ == "__main__":
    unittest.main()
