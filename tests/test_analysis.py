import numpy as np

from backend.analysis import analyze_phase_curve
from backend.services import find_repeating_dip, prepare_curve_points


def test_detects_localized_transit_signal() -> None:
    rng = np.random.default_rng(7)
    phase = np.linspace(-0.5, 0.5, 1200)
    flux = rng.normal(0, 0.0008, phase.size)
    flux[np.abs(phase - 0.08) < 0.018] -= 0.008

    result = analyze_phase_curve(phase, flux)

    assert result.classification == "strong_candidate"
    assert result.score >= 75
    assert result.signal_to_noise >= 10
    assert abs(result.phase_center - 0.08) < 0.03
    assert result.depth_ppm > 5_000


def test_does_not_promote_flat_noisy_curve() -> None:
    rng = np.random.default_rng(11)
    phase = np.linspace(-0.5, 0.5, 1200)
    flux = rng.normal(0, 0.001, phase.size)

    result = analyze_phase_curve(phase, flux)

    assert result.classification in {"no_signal", "weak_signal"}
    assert result.score < 50


def test_requires_enough_data() -> None:
    result = analyze_phase_curve([0, 1, 2], [0, -0.01, 0])
    assert result.classification == "insufficient_data"


def test_finds_repeating_dip_without_catalog_information() -> None:
    time = np.linspace(0, 30, 3000)
    expected_period = 3.0
    phase = ((time - 0.4 + expected_period / 2) % expected_period) - expected_period / 2
    flux = np.ones_like(time)
    flux[np.abs(phase) < 0.08] -= 0.01

    detection = find_repeating_dip(time, flux)

    assert abs(detection["period_days"] - expected_period) < 0.05


def test_large_curve_is_bounded_and_sorted_for_browser() -> None:
    phase = np.linspace(1, -1, 25_000)
    flux = np.ones_like(phase)

    points = prepare_curve_points(phase, flux)

    assert len(points) == 8_000
    assert points[0][0] == -1
    assert points[-1][0] == 1
    assert all(left[0] <= right[0] for left, right in zip(points, points[1:]))
