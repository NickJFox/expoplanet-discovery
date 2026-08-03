import numpy as np

from main import bin_phase_curve, prepare_flux_for_plot


def test_prepare_flux_for_plot_centers_flux_and_preserves_transit_dips() -> None:
    flux = np.array([1.0, 1.0, 0.98, 1.0, 1.0])

    normalized = prepare_flux_for_plot(flux)

    assert normalized[0] == 0.0
    assert normalized[2] < 0.0
    assert np.isclose(normalized[2], -0.02)


def test_prepare_flux_for_plot_preserves_zero_centered_detrended_flux() -> None:
    flux = np.array([0.001, 0.0, -0.002, 0.0, 0.001])

    normalized = prepare_flux_for_plot(flux)

    np.testing.assert_allclose(normalized, flux)


def test_bin_phase_curve_returns_median_for_each_bin() -> None:
    phase = np.array([0.1, 0.2, 0.8, 0.9])
    flux = np.array([0.0, -0.02, 0.01, 0.03])

    binned_phase, binned_flux = bin_phase_curve(phase, flux, bins=2)

    np.testing.assert_allclose(binned_phase, [0.15, 0.85])
    np.testing.assert_allclose(binned_flux, [-0.01, 0.02])
