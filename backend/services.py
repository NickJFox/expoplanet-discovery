"""Remote astronomy data access and response assembly."""

from __future__ import annotations

import csv
import io
import random
import re
import time as time_module
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import numpy as np

from main import normalize_target, prepare_flux_for_plot, request_json

from .analysis import analyze_phase_curve


NASA_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
NASA_ALIASES = "https://exoplanetarchive.ipac.caltech.edu/cgi-bin/Lookup/nph-aliaslookup.py"
RANDOM_TARGETS = ["261136679", "307210830", "150428135", "441462736", "231663901", "278683844"]
LIGHTKURVE_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "lightkurve"
PROCESSED_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "processed-lightcurves"
PROCESSED_CACHE_SECONDS = 24 * 60 * 60
PROCESSED_CACHE_VERSION = 2
MAX_SEARCH_POINTS = 50_000
MAX_ANALYSIS_POINTS = 20_000
MAX_PLOT_POINTS = 8_000


class ArchiveTimeoutError(RuntimeError):
    """Raised when a remote astronomy archive times out after retries."""


def retry_archive_call(operation, attempts: int = 2):
    """Retry transient archive timeouts without hiding unrelated failures."""
    import requests

    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            is_timeout = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or "timed out" in str(exc).lower()
            if not is_timeout:
                raise
            if attempt == attempts - 1:
                raise ArchiveTimeoutError(
                    "The TESS archive took too long to respond. Please try this search again in a moment."
                ) from exc
            time_module.sleep(0.75)


def _tap(query: str) -> list[dict[str, str]]:
    import requests

    url = f"{NASA_TAP}?query={quote(query)}&format=csv"
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, timeout=45, headers={"User-Agent": "exoplanet-discovery/1.0"})
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))


@lru_cache(maxsize=256)
def _resolve_catalog_alias(target: str) -> dict | None:
    """Resolve a NASA-recognized host alias to its canonical name and TIC ID."""
    payload = request_json(f"{NASA_ALIASES}?objname={quote(target.strip())}", timeout=30)
    if payload.get("manifest", {}).get("lookup_status") != "OK":
        return None

    stars = payload.get("system", {}).get("objects", {}).get("stellar_set", {}).get("stars", {})
    requested_star = next(
        (star for star in stars.values() if star.get("requested_object") == "True"),
        None,
    )
    if requested_star is None:
        return None

    aliases = requested_star.get("alias_set", {})
    tic_alias = next(
        (alias for alias in aliases.get("aliases", []) if re.fullmatch(r"TIC\s+\d+", alias, re.IGNORECASE)),
        None,
    )
    if tic_alias is None:
        return None

    return {
        "input": target,
        "tic_id": normalize_target(tic_alias),
        "resolved_name": aliases.get("default_name") or payload["manifest"]["resolved_name"],
    }


@lru_cache(maxsize=256)
def _resolve_tic_target(target: str) -> dict | None:
    """Resolve a general star name through MAST's TESS Input Catalog."""
    from astroquery.mast import Catalogs

    rows = Catalogs.query_object(target.strip(), catalog="TIC", radius=0.001)
    if len(rows) == 0:
        return None
    tic_id = str(rows[0]["ID"])
    return {"input": target, "tic_id": tic_id, "resolved_name": target.strip()}


def resolve_target(target: str) -> dict:
    numeric = re.fullmatch(r"\s*(?:TIC\s*)?(\d+)\s*", target, re.IGNORECASE)
    if numeric:
        return {"input": target, "tic_id": numeric.group(1), "resolved_name": f"TIC {numeric.group(1)}"}
    try:
        tic_match = _resolve_tic_target(target)
        if tic_match:
            return tic_match
    except Exception:
        pass
    try:
        alias_match = _resolve_catalog_alias(target)
        if alias_match:
            return alias_match
    except Exception:
        pass
    safe = target.strip().replace("'", "''")
    rows = _tap(
        "select top 1 hostname,tic_id from pscomppars "
        f"where upper(hostname)=upper('{safe}') and tic_id is not null"
    )
    if not rows:
        raise ValueError("Star could not be found. Check spelling or the name of another star.")
    tic_id = normalize_target(rows[0]["tic_id"])
    return {"input": target, "tic_id": tic_id, "resolved_name": rows[0]["hostname"]}


@lru_cache(maxsize=32)
def fetch_tess_lightcurve(tic_id: str) -> tuple[np.ndarray, np.ndarray, str]:
    """Download and clean ordinary public TESS light curves for a TIC target."""
    PROCESSED_CACHE.mkdir(parents=True, exist_ok=True)
    processed_path = PROCESSED_CACHE / f"tic-{tic_id}-v{PROCESSED_CACHE_VERSION}.npz"
    if processed_path.exists() and time_module.time() - processed_path.stat().st_mtime < PROCESSED_CACHE_SECONDS:
        with np.load(processed_path) as cached:
            return cached["time"].copy(), cached["flux"].copy(), str(cached["label"].item())

    import lightkurve as lk

    search = retry_archive_call(lambda: lk.search_lightcurve(f"TIC {tic_id}", mission="TESS"))
    if len(search) == 0:
        raise RuntimeError(f"No public TESS light curve was found for TIC {tic_id}")

    authors = [str(value).upper() for value in search.table["author"]]
    selected = search
    selected_author = "available archive product"
    for preferred in ("SPOC", "QLP", "TESS-SPOC", "GSFC-ELEANOR-LITE"):
        indices = [index for index, author in enumerate(authors) if author == preferred]
        if indices:
            selected = search[indices]
            selected_author = preferred
            break

    # Some sectors contain both 20-second and 120-second SPOC products. Use a
    # single product per sector and prefer the cadence closest to 120 seconds,
    # which retains transit structure without duplicating or exploding points.
    product_by_sector: dict[str, tuple[int, float]] = {}
    for index, row in enumerate(selected.table):
        sector = str(row["mission"])
        exposure = float(row["exptime"])
        cadence_distance = abs(np.log(max(exposure, 1) / 120.0))
        current = product_by_sector.get(sector)
        if current is None or cadence_distance < current[1]:
            product_by_sector[sector] = (index, cadence_distance)
    selected = selected[sorted(product[0] for product in product_by_sector.values())]

    LIGHTKURVE_CACHE.mkdir(parents=True, exist_ok=True)
    collection = retry_archive_call(lambda: selected.download_all(download_dir=str(LIGHTKURVE_CACHE)))
    if collection is None or len(collection) == 0:
        raise RuntimeError(f"TESS light-curve products for TIC {tic_id} could not be downloaded")

    cleaned = []
    for curve in collection:
        try:
            prepared = curve.remove_nans().normalize()
            cadence_days = float(np.nanmedian(np.diff(prepared.time.value)))
            window_length = max(101, int(round(1.0 / cadence_days)))
            if window_length % 2 == 0:
                window_length += 1
            maximum_window = len(prepared) - 1 if len(prepared) % 2 == 0 else len(prepared) - 2
            window_length = min(window_length, maximum_window)
            prepared = prepared.flatten(window_length=window_length, break_tolerance=5)
            if len(prepared) >= 40:
                cleaned.append(prepared)
        except Exception:
            continue
    if not cleaned:
        raise RuntimeError(f"No usable TESS brightness measurements were available for TIC {tic_id}")

    stitched = lk.LightCurveCollection(cleaned).stitch()
    time = np.asarray(stitched.time.value, dtype=float)
    flux = np.asarray(stitched.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time, flux = time[finite], flux[finite]
    if time.size < 40:
        raise RuntimeError(f"Fewer than 40 usable TESS brightness measurements were available for TIC {tic_id}")
    label = f"TESS light curve · {selected_author} · {len(cleaned)} sector product(s)"
    np.savez_compressed(processed_path, time=time, flux=flux, label=label)
    return time, flux, label


def find_repeating_dip(time: np.ndarray, flux: np.ndarray) -> dict[str, float]:
    """Use a coarse-to-fine Box Least Squares search for a repeating dip."""
    from astropy.timeseries import BoxLeastSquares

    order = np.argsort(time)
    time, flux = time[order], flux[order]
    span = float(np.ptp(time))
    maximum_period = min(45.0, max(1.0, span / 2))
    if maximum_period <= 0.5:
        raise RuntimeError("The TESS observations do not cover enough time for a repeating-signal search")

    # A multi-year baseline requires extremely fine period spacing, but searching
    # that entire grid directly is expensive. Find candidates in the densest
    # 80-day observing window, then refine those candidates across all sectors.
    window_days = min(80.0, span)
    left = 0
    best_left, best_right = 0, time.size - 1
    best_count = 0
    for right in range(time.size):
        while time[right] - time[left] > window_days:
            left += 1
        if right - left + 1 > best_count:
            best_left, best_right, best_count = left, right, right - left + 1
    coarse_time = time[best_left:best_right + 1]
    coarse_flux = flux[best_left:best_right + 1]
    if coarse_time.size > 30_000:
        sample = np.linspace(0, coarse_time.size - 1, 30_000, dtype=int)
        coarse_time, coarse_flux = coarse_time[sample], coarse_flux[sample]

    durations = [0.04, 0.08, 0.12, 0.2, 0.3, 0.4]
    coarse_periods = np.geomspace(0.5, maximum_period, 5_000)
    coarse = BoxLeastSquares(coarse_time, coarse_flux).power(coarse_periods, durations)
    candidates: list[float] = []
    for index in np.argsort(coarse.power)[::-1]:
        period = float(coarse_periods[index])
        if all(abs(period - existing) / existing > 0.01 for existing in candidates):
            candidates.append(period)
        if len(candidates) == 16:
            break

    if time.size > MAX_SEARCH_POINTS:
        sample = np.linspace(0, time.size - 1, MAX_SEARCH_POINTS, dtype=int)
        search_time, search_flux = time[sample], flux[sample]
    else:
        search_time, search_flux = time, flux
    refined_grids = []
    for period in candidates:
        position = int(np.searchsorted(coarse_periods, period))
        low = coarse_periods[max(0, position - 2)]
        high = coarse_periods[min(coarse_periods.size - 1, position + 2)]
        refined_grids.append(np.linspace(low, high, 240))
    refined_periods = np.unique(np.concatenate(refined_grids))
    result = BoxLeastSquares(search_time, search_flux).power(refined_periods, durations)
    index = int(np.nanargmax(result.power))
    return {
        "period_days": float(result.period[index]),
        "duration_days": float(result.duration[index]),
        "transit_time": float(result.transit_time[index]),
        "bls_power": float(result.power[index]),
    }


def prepare_curve_points(phases: np.ndarray, flux: np.ndarray, limit: int = MAX_PLOT_POINTS) -> list[tuple[float, float]]:
    """Return phase-sorted representative points suitable for an interactive SVG."""
    order = np.argsort(phases)
    if order.size > limit:
        order = order[np.linspace(0, order.size - 1, limit, dtype=int)]
    return [(float(phases[index]), float(flux[index])) for index in order]


@lru_cache(maxsize=256)
def catalog_matches(tic_id: str, target_name: str = "") -> dict:
    try:
        planets = _tap(
            "select pl_name,hostname,discoverymethod,disc_year,pl_orbper,pl_rade,tran_flag "
            f"from pscomppars where tic_id like '%{tic_id}%'"
        )
        tois = _tap(
            "select toi,tid,tfopwg_disp,pl_orbper,pl_trandep,pl_rade "
            f"from toi where tid={int(tic_id)}"
        )
    except Exception as exc:
        return {"status": "unavailable", "host_name": None, "planets": [], "tois": [], "message": str(exc)}

    # General TIC resolution may return a newer source ID than the legacy TIC ID
    # attached to an Exoplanet Archive record. Follow NASA's alias mapping only
    # when the direct, current-TIC comparison has no match.
    if not planets and not tois and target_name:
        try:
            alias = _resolve_catalog_alias(target_name)
            if alias:
                legacy_tic_id = alias["tic_id"]
                canonical_name = str(alias["resolved_name"]).replace("'", "''")
                planets = _tap(
                    "select pl_name,hostname,discoverymethod,disc_year,pl_orbper,pl_rade,tran_flag "
                    "from pscomppars "
                    f"where tic_id like '%{legacy_tic_id}%' or upper(hostname)=upper('{canonical_name}')"
                )
                tois = _tap(
                    "select toi,tid,tfopwg_disp,pl_orbper,pl_trandep,pl_rade "
                    f"from toi where tid={int(legacy_tic_id)}"
                )
        except Exception:
            pass
    dispositions = {row.get("tfopwg_disp", "").upper() for row in tois}
    if planets:
        status = "confirmed"
    elif dispositions & {"CP", "PC", "APC"}:
        status = "candidate"
    elif tois:
        status = "cataloged_toi"
    else:
        status = "no_match"
    host_name = next((row.get("hostname", "").strip() for row in planets if row.get("hostname", "").strip()), None)
    if host_name is None and tois:
        toi = tois[0].get("toi", "").strip()
        if toi:
            host_name = f"TOI-{toi.split('.')[0]}"
    return {"status": status, "host_name": host_name, "planets": planets, "tois": tois}


@lru_cache(maxsize=64)
def inspect_target(target: str) -> dict:
    resolved = resolve_target(target)
    tic_id = resolved["tic_id"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        curve_future = executor.submit(fetch_tess_lightcurve, tic_id)
        catalog_future = executor.submit(catalog_matches, tic_id, target.strip())
        times, fluxes, label = curve_future.result()
        catalog = catalog_future.result()
    normalized = prepare_flux_for_plot(fluxes)
    detection = find_repeating_dip(times, normalized)
    period = detection["period_days"]
    epoch = detection["transit_time"]
    phases = ((times - epoch + period / 2) % period) - period / 2
    if phases.size > MAX_ANALYSIS_POINTS:
        analysis_sample = np.linspace(0, phases.size - 1, MAX_ANALYSIS_POINTS, dtype=int)
        analysis = analyze_phase_curve(
            phases[analysis_sample],
            normalized[analysis_sample],
            expected_center=0.0,
            expected_width=detection["duration_days"],
        )
    else:
        analysis = analyze_phase_curve(
            phases,
            normalized,
            expected_center=0.0,
            expected_width=detection["duration_days"],
        )
    points = prepare_curve_points(phases, normalized)
    if catalog["host_name"]:
        resolved["resolved_name"] = catalog["host_name"]
    return {
        "target": resolved,
        "label": label,
        "detection": {key: round(value, 8) for key, value in detection.items()},
        "observation_count": int(times.size),
        "curve": {"phase": [round(float(x), 7) for x, _ in points], "flux": [round(float(y), 8) for _, y in points]},
        "analysis": analysis.to_dict(),
        "catalog": catalog,
        "data_source": label,
        "is_synthetic": False,
    }


def random_target() -> str:
    return random.choice(RANDOM_TARGETS)
