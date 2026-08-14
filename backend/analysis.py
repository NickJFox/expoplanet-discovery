"""Explainable transit-like signal measurements for phase-folded light curves."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class TransitAnalysis:
    classification: str
    score: int
    signal_to_noise: float
    depth: float
    depth_ppm: int
    phase_center: float
    duration_phase: float
    in_transit_points: int
    noise: float
    reasons: list[str]
    caveat: str = (
        "This is an automated signal screen, not a planet confirmation. "
        "Instrumental and stellar effects can mimic transits."
    )

    def to_dict(self) -> dict:
        return asdict(self)


def _robust_noise(values: np.ndarray) -> float:
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    return float(max(1.4826 * mad, np.std(values) * 0.25, 1e-8))


def analyze_phase_curve(phase_values, flux_values) -> TransitAnalysis:
    """Find and score the strongest box-shaped dimming without catalog knowledge."""
    phase = np.asarray(phase_values, dtype=float)
    flux = np.asarray(flux_values, dtype=float)
    finite = np.isfinite(phase) & np.isfinite(flux)
    phase, flux = phase[finite], flux[finite]
    if phase.size < 40:
        return TransitAnalysis("insufficient_data", 0, 0, 0, 0, 0, 0, int(phase.size), 0, ["Fewer than 40 usable observations."])

    order = np.argsort(phase)
    phase, flux = phase[order], flux[order]
    baseline = float(np.median(flux))
    centered = flux - baseline
    span = float(np.ptp(phase))
    if span <= 0:
        return TransitAnalysis("insufficient_data", 0, 0, 0, 0, 0, 0, int(phase.size), 0, ["The phase axis has no measurable span."])

    best = None
    # Search plausible transit widths. This is independent of the expected phase zero.
    for fraction in np.geomspace(0.008, 0.12, 18):
        width = span * float(fraction)
        for center in np.linspace(float(phase.min()), float(phase.max()), 180):
            inside = np.abs(phase - center) <= width / 2
            count = int(inside.sum())
            if count < 6 or count > phase.size * 0.25:
                continue
            outside = ~inside
            out_level = float(np.median(centered[outside]))
            in_level = float(np.median(centered[inside]))
            depth = out_level - in_level
            noise = _robust_noise(centered[outside] - out_level)
            snr = depth / noise * np.sqrt(count)
            if best is None or snr > best[0]:
                best = (float(snr), float(depth), float(center), float(width), count, noise, inside)

    if best is None:
        return TransitAnalysis("no_signal", 0, 0, 0, 0, 0, 0, 0, _robust_noise(centered), ["No window contained enough data to evaluate."])

    snr, depth, center, width, count, noise, inside = best
    left = (phase >= center - width * 1.5) & (phase < center - width / 2)
    right = (phase > center + width / 2) & (phase <= center + width * 1.5)
    localized = bool(left.sum() >= 3 and right.sum() >= 3)
    shoulder_level = float(np.median(centered[left | right])) if localized else 0.0
    localized = localized and (shoulder_level - float(np.median(centered[inside]))) > depth * 0.45

    score = 0
    reasons: list[str] = []
    if snr >= 10:
        score += 45
        reasons.append(f"Strong dimming signal (SNR {snr:.1f}).")
    elif snr >= 7:
        score += 35
        reasons.append(f"Potentially significant dimming (SNR {snr:.1f}).")
    elif snr >= 5:
        score += 20
        reasons.append(f"Marginal dimming signal (SNR {snr:.1f}).")
    else:
        reasons.append(f"Signal-to-noise is below the screening threshold ({snr:.1f}).")
    if depth > max(noise * 2, 0):
        score += 25
        reasons.append(f"Measured depth is {depth * 1_000_000:,.0f} ppm.")
    if localized and snr >= 7:
        score += 20
        reasons.append("The dip is localized with higher-flux shoulders on both sides.")
    else:
        reasons.append("The dip does not have clearly defined shoulders.")
    duration_fraction = width / span
    if 0.008 <= duration_fraction <= 0.08 and snr >= 7:
        score += 10
        reasons.append("The event width is compatible with a compact transit-like feature.")
    score = min(score, 100)
    classification = "strong_candidate" if score >= 75 else "possible_candidate" if score >= 50 else "weak_signal" if score >= 25 else "no_signal"
    return TransitAnalysis(
        classification, score, round(snr, 2), round(depth, 8), int(round(depth * 1_000_000)),
        round(center, 6), round(width, 6), count, round(noise, 8), reasons,
    )
