#!/usr/bin/env python3
"""Fetch and plot TESS DV data from Exo.MAST for exoplanet transit inspection."""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MATPLOTLIB_CACHE_DIR = PROJECT_ROOT / ".cache" / "matplotlib"
MATPLOTLIB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(MATPLOTLIB_CACHE_DIR)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests


BASE_URL = "https://exo.mast.stsci.edu/api/v0.1"


def request_json(url: str, timeout: int = 60, retries: int = 3, backoff: float = 1.0) -> dict:
    """Fetch JSON from a URL with retry support and no inherited proxy settings."""
    session = requests.Session()
    session.trust_env = False
    headers = {"User-Agent": "Mozilla/5.0"}

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=timeout, headers=headers)
            if response.status_code == 200:
                return response.json()

            if response.status_code in {429, 500, 502, 503, 504} and attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue

            response.raise_for_status()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {exc}") from exc

    if last_error is not None:
        raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error
    raise RuntimeError(f"Failed to fetch {url}: unknown error")


class DemoLightCurve:
    """A small synthetic light curve that mimics a transit-like dip for offline testing."""

    def __init__(self, target: str) -> None:
        self.targetid = target
        time = np.linspace(0, 20, 1000)
        flux = 1.0 + 0.002 * np.sin(2 * np.pi * time / 3.2)
        transit_mask = (time > 7.2) & (time < 7.8)
        flux[transit_mask] *= 0.96
        noise = np.random.default_rng(42).normal(0, 0.0015, size=len(time))
        self.time = time
        self.flux = flux + noise


def build_demo_lightcurve(target: str) -> DemoLightCurve:
    """Create a synthetic light curve when the API is unavailable."""
    return DemoLightCurve(target)


def normalize_target(target: str) -> str:
    """Extract a numeric TIC identifier from common input forms like 'TIC 261136679'."""
    match = re.search(r"(\d+)", target)
    if match:
        return match.group(1)
    return target.strip()


def format_sector(sector: int | None) -> str:
    """Convert a numeric sector into the Exo.MAST sector notation used by the API."""
    if sector is None:
        return "s0001-s0001"
    return f"s{sector:04d}-s{sector:04d}"


def fetch_tce_options(tic_id: str) -> list[str]:
    """Return the available TCE entries for a TESS target from the Exo.MAST API."""
    url = f"{BASE_URL}/dvdata/tess/{tic_id}/tces/"
    payload = request_json(url, timeout=60, retries=3, backoff=1.0)
    tces = payload.get("TCE", [])
    if not tces:
        raise RuntimeError(f"No TCE data found for TIC {tic_id}")
    return tces


def fetch_phase_curve(tic_id: str, sector: int | None = None, tce: int | None = None) -> tuple[list[float], list[float], str]:
    """Fetch phase-folded detrended flux data from Exo.MAST."""
    tce_options = fetch_tce_options(tic_id)
    selected_tce = tce or 1
    sector_value = format_sector(sector)

    if selected_tce is None:
        selected_tce = 1

    for option in tce_options:
        if option.endswith(f":TCE_{selected_tce}"):
            sector_value = option.split(":", 1)[0]
            break

    url = f"{BASE_URL}/dvdata/tess/{tic_id}/table/?tce={selected_tce}&sector={sector_value}"
    payload = request_json(url, timeout=60, retries=3, backoff=1.0)

    rows = payload.get("data", [])
    phases: list[float] = []
    fluxes: list[float] = []
    for row in rows:
        phase = row.get("PHASE")
        flux = row.get("LC_DETREND")
        if phase is None or flux is None:
            continue
        phases.append(float(phase))
        fluxes.append(float(flux))

    if not phases:
        raise RuntimeError("The Exo.MAST response did not include usable phase/flux rows")

    return phases, fluxes, f"TIC {tic_id} • TCE {selected_tce} • {sector_value}"


def download_lightcurve(target: str, sector: int | None = None, tce: int | None = None, *, no_fallback: bool = False):
    """Download a real phase curve from Exo.MAST or fall back to a demo curve."""
    try:
        tic_id = normalize_target(target)
        phases, fluxes, label = fetch_phase_curve(tic_id, sector=sector, tce=tce)
        return {
            "targetid": f"TIC {tic_id}",
            "label": label,
            "time": phases,
            "flux": fluxes,
        }
    except Exception as exc:  # exercised in offline environments
        if no_fallback:
            raise
        print(f"Falling back to a synthetic light curve because the Exo.MAST request failed: {exc}")
        return build_demo_lightcurve(target)


def prepare_flux_for_plot(flux_values: np.ndarray | list[float]) -> np.ndarray:
    """Return flux as a zero-centered fractional deviation."""
    flux_array = np.asarray(flux_values, dtype=float)
    if flux_array.size == 0:
        return flux_array

    median_flux = np.median(flux_array)
    # Exo.MAST LC_DETREND is already a fractional deviation centered on zero.
    # Conventional light curves are centered on one and need division by the
    # median to be expressed on the same scale.
    if abs(median_flux) < 0.5:
        return flux_array - median_flux

    return (flux_array - median_flux) / median_flux


def bin_phase_curve(
    phase_values: np.ndarray | list[float],
    flux_values: np.ndarray | list[float],
    bins: int = 120,
) -> tuple[np.ndarray, np.ndarray]:
    """Median-bin a phase curve to make a repeated transit visible above noise."""
    phases = np.asarray(phase_values, dtype=float)
    fluxes = np.asarray(flux_values, dtype=float)
    finite = np.isfinite(phases) & np.isfinite(fluxes)
    phases = phases[finite]
    fluxes = fluxes[finite]
    if phases.size == 0:
        return np.array([]), np.array([])

    edges = np.linspace(phases.min(), phases.max(), bins + 1)
    assignments = np.clip(np.digitize(phases, edges) - 1, 0, bins - 1)
    binned_phase: list[float] = []
    binned_flux: list[float] = []
    for index in range(bins):
        in_bin = assignments == index
        if np.any(in_bin):
            binned_phase.append(float(np.median(phases[in_bin])))
            binned_flux.append(float(np.median(fluxes[in_bin])))
    return np.asarray(binned_phase), np.asarray(binned_flux)


def plot_lightcurve(lightcurve, output_path: Path) -> None:
    """Plot the phase-folded flux curve."""
    if isinstance(lightcurve, dict):
        fig, (ax, zoom_ax) = plt.subplots(2, 1, figsize=(10, 7), constrained_layout=True)
        x_values = np.asarray(lightcurve["time"], dtype=float)
        y_values = prepare_flux_for_plot(lightcurve["flux"])
        finite = np.isfinite(x_values) & np.isfinite(y_values)
        x_values = x_values[finite]
        y_values = y_values[finite]
        order = np.argsort(x_values)
        x_values = x_values[order]
        y_values = y_values[order]

        ax.scatter(x_values, y_values, s=5, color="0.55", alpha=0.18, linewidths=0, label="Observations")
        binned_phase, binned_flux = bin_phase_curve(x_values, y_values)
        ax.scatter(
            binned_phase,
            binned_flux,
            s=22,
            color="tab:blue",
            edgecolors="white",
            linewidths=0.3,
            zorder=3,
            label="Median-binned flux",
        )
        ax.axvline(0, color="gray", lw=0.8, alpha=0.6)
        ax.set_title(lightcurve["label"])
        if y_values.size:
            low, high = np.percentile(y_values, [0.5, 99.5])
            padding = max((high - low) * 0.12, 1e-5)
            ax.set_ylim(low - padding, high + padding)
        ax.set_ylabel("Relative flux deviation")
        ax.set_xlabel("Phase")
        ax.legend(loc="best")
        ax.grid(alpha=0.3)

        phase_span = float(np.ptp(x_values))
        zoom_half_width = min(max(phase_span * 0.025, 0.25), 1.0)
        in_zoom = np.abs(x_values) <= zoom_half_width
        zoom_phase = x_values[in_zoom]
        zoom_flux = y_values[in_zoom]
        zoom_ax.scatter(zoom_phase, zoom_flux, s=9, color="0.45", alpha=0.25, linewidths=0)
        zoom_binned_phase, zoom_binned_flux = bin_phase_curve(zoom_phase, zoom_flux, bins=80)
        zoom_ax.scatter(
            zoom_binned_phase,
            zoom_binned_flux,
            s=28,
            color="tab:blue",
            edgecolors="white",
            linewidths=0.4,
            zorder=3,
        )
        zoom_ax.axvline(0, color="gray", lw=0.8, alpha=0.6)
        zoom_ax.axhline(0, color="gray", lw=0.8, alpha=0.35)
        zoom_ax.set_xlim(-zoom_half_width, zoom_half_width)
        if zoom_flux.size:
            low, high = np.percentile(zoom_flux, [0.5, 99.5])
            padding = max((high - low) * 0.12, 1e-5)
            zoom_ax.set_ylim(low - padding, high + padding)
        zoom_ax.set_title("Transit-centered detail")
        zoom_ax.set_xlabel("Phase relative to expected transit")
        zoom_ax.set_ylabel("Relative flux deviation")
        zoom_ax.grid(alpha=0.3)
    else:
        fig, ax = plt.subplots(figsize=(10, 4))
        y_values = prepare_flux_for_plot(lightcurve.flux)
        ax.scatter(lightcurve.time, y_values, s=8, color="gray", alpha=0.4)
        ax.set_title(f"{lightcurve.targetid} - synthetic light curve")
        ax.set_ylim(-0.05, 0.05)
        ax.set_xlabel("Time")
        ax.set_ylabel("Relative flux deviation")
        ax.grid(alpha=0.3)
        fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and plot a TESS light curve from Exo.MAST")
    parser.add_argument("--target", required=True, help="Target TIC ID or value like 'TIC 261136679'")
    parser.add_argument("--sector", type=int, default=None, help="Optional TESS sector number")
    parser.add_argument("--tce", type=int, default=None, help="Optional TCE number, defaults to TCE_1")
    parser.add_argument("--output", default="tess_lightcurve.png", help="Output image path")
    parser.add_argument("--no-fallback", action="store_true", help="Raise an error instead of falling back to a synthetic curve")
    args = parser.parse_args()

    output_path = Path(args.output)
    lightcurve = download_lightcurve(args.target, sector=args.sector, tce=args.tce, no_fallback=args.no_fallback)
    plot_lightcurve(lightcurve, output_path)

    print(f"Processed light curve for {args.target}")
    print(f"Saved plot to {output_path.resolve()}")


if __name__ == "__main__":
    main()
