"""Lightweight helpers shared by the web service.

Keep this module free of plotting imports so FastAPI can become healthy before
optional scientific/plotting packages are loaded.
"""

from __future__ import annotations

import re
import time

import numpy as np


def request_json(url: str, timeout: int = 60, retries: int = 3, backoff: float = 1.0) -> dict:
    """Fetch JSON from a URL with retry support and no inherited proxies."""
    import requests

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

    raise RuntimeError(f"Failed to fetch {url}: {last_error or 'unknown error'}")


def normalize_target(target: str) -> str:
    """Extract a numeric identifier from common catalog input forms."""
    match = re.search(r"(\d+)", target)
    return match.group(1) if match else target.strip()


def prepare_flux_for_plot(flux_values: np.ndarray | list[float]) -> np.ndarray:
    """Return flux as a zero-centered fractional deviation."""
    flux_array = np.asarray(flux_values, dtype=float)
    if flux_array.size == 0:
        return flux_array

    median_flux = np.median(flux_array)
    if abs(median_flux) < 0.5:
        return flux_array - median_flux
    return (flux_array - median_flux) / median_flux
