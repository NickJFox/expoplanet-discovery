"""Remote astronomy data access and response assembly."""

from __future__ import annotations

import csv
import io
import random
import re
import time as time_module
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
PROCESSED_CACHE_VERSION = 3
MAX_ARCHIVE_PRODUCTS = 4
MAX_SEARCH_POINTS = 10_000
MAX_ANALYSIS_POINTS = 10_000
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
    alias_list = aliases.get("aliases", [])
    tic_alias = next(
        (alias for alias in alias_list if re.fullmatch(r"TIC\s+\d+", alias, re.IGNORECASE)),
        None,
    )
    if tic_alias is None:
        return None

    result = {
        "input": target,
        "tic_id": normalize_target(tic_alias),
        "resolved_name": aliases.get("default_name") or payload["manifest"]["resolved_name"],
    }
    kic_alias = next((alias for alias in alias_list if re.fullmatch(r"KIC\s+\d+", alias, re.IGNORECASE)), None)
    if kic_alias:
        result["kepler_id"] = normalize_target(kic_alias)
    return result


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
    # Kepler and KOI names should preserve their KIC alias so their original,
    # multi-year Kepler observations can be used instead of a shorter TESS view.
    prefers_kepler = bool(re.match(r"\s*(?:Kepler|KOI|KIC)[-\s]", target, re.IGNORECASE))
    if prefers_kepler:
        try:
            alias_match = _resolve_catalog_alias(target)
            if alias_match:
                return alias_match
        except Exception:
            pass
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


def fetch_archive_lightcurve(identifier: str, mission: str = "TESS") -> tuple[np.ndarray, np.ndarray, str]:
    """Download and clean public TESS or Kepler light curves."""
    mission = mission.upper()
    is_kepler = mission == "KEPLER"
    id_prefix = "kic" if is_kepler else "tic"
    display_mission = "Kepler" if is_kepler else "TESS"
    PROCESSED_CACHE.mkdir(parents=True, exist_ok=True)
    processed_path = PROCESSED_CACHE / f"{id_prefix}-{identifier}-v{PROCESSED_CACHE_VERSION}.npz"
    if processed_path.exists() and time_module.time() - processed_path.stat().st_mtime < PROCESSED_CACHE_SECONDS:
        with np.load(processed_path) as cached:
            return cached["time"].copy(), cached["flux"].copy(), str(cached["label"].item())

    import lightkurve as lk

    search_target = f"KIC {identifier}" if is_kepler else f"TIC {identifier}"
    search = retry_archive_call(lambda: lk.search_lightcurve(search_target, mission=display_mission))
    if len(search) == 0:
        raise RuntimeError(f"No public {display_mission} light curve was found for {search_target}")

    authors = [str(value).upper() for value in search.table["author"]]
    selected = search
    selected_author = "available archive product"
    author_priority = ("KEPLER",) if is_kepler else ("SPOC", "QLP", "TESS-SPOC", "GSFC-ELEANOR-LITE")
    for preferred in author_priority:
        indices = [index for index, author in enumerate(authors) if author == preferred]
        if indices:
            selected = search[indices]
            selected_author = preferred
            break

    # Use one product per sector/quarter. Prefer 30-minute Kepler long cadence
    # or 2-minute TESS cadence to retain transit structure without duplicates.
    product_by_sector: dict[str, tuple[int, float]] = {}
    for index, row in enumerate(selected.table):
        sector = str(row["mission"])
        exposure = float(row["exptime"])
        preferred_exposure = 1800.0 if is_kepler else 120.0
        cadence_distance = abs(np.log(max(exposure, 1) / preferred_exposure))
        current = product_by_sector.get(sector)
        if current is None or cadence_distance < current[1]:
            product_by_sector[sector] = (index, cadence_distance)
    product_indices = sorted(product[0] for product in product_by_sector.values())
    # Render's free instance has a 512 MB memory ceiling. Loading every sector
    # for frequently observed stars can exceed it, so use a representative set
    # spread across the available baseline. Four products still provide useful
    # multi-event screening while keeping lightkurve's peak memory bounded.
    if len(product_indices) > MAX_ARCHIVE_PRODUCTS:
        representative = np.linspace(0, len(product_indices) - 1, MAX_ARCHIVE_PRODUCTS, dtype=int)
        product_indices = [product_indices[index] for index in representative]
    selected = selected[product_indices]

    LIGHTKURVE_CACHE.mkdir(parents=True, exist_ok=True)
    collection = retry_archive_call(lambda: selected.download_all(download_dir=str(LIGHTKURVE_CACHE)))
    if collection is None or len(collection) == 0:
        raise RuntimeError(f"{display_mission} light-curve products for {search_target} could not be downloaded")

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
        raise RuntimeError(f"No usable {display_mission} brightness measurements were available for {search_target}")

    stitched = lk.LightCurveCollection(cleaned).stitch()
    time = np.asarray(stitched.time.value, dtype=float)
    flux = np.asarray(stitched.flux.value, dtype=float)
    finite = np.isfinite(time) & np.isfinite(flux)
    time, flux = time[finite], flux[finite]
    if time.size < 40:
        raise RuntimeError(f"Fewer than 40 usable {display_mission} brightness measurements were available for {search_target}")
    product_label = "quarter" if is_kepler else "sector"
    label = f"{display_mission} light curve · {selected_author} · {len(cleaned)} {product_label} product(s)"
    np.savez_compressed(processed_path, time=time, flux=flux, label=label)
    return time, flux, label


def fetch_tess_lightcurve(tic_id: str) -> tuple[np.ndarray, np.ndarray, str]:
    return fetch_archive_lightcurve(tic_id, "TESS")


def fetch_kepler_lightcurve(kic_id: str) -> tuple[np.ndarray, np.ndarray, str]:
    return fetch_archive_lightcurve(kic_id, "KEPLER")


def find_repeating_dip(time: np.ndarray, flux: np.ndarray) -> dict[str, float]:
    """Use a coarse-to-fine Box Least Squares search for a repeating dip."""
    from astropy.timeseries import BoxLeastSquares

    order = np.argsort(time)
    time, flux = time[order], flux[order]
    span = float(np.ptp(time))
    # Long-baseline Kepler curves contain scientifically important planets well
    # beyond 45 days (Kepler-90 h is about 332 days). Require at least two
    # possible events while allowing periods up to 400 days.
    maximum_period = min(400.0, max(1.0, span / 2))
    if maximum_period <= 0.5:
        raise RuntimeError("The TESS observations do not cover enough time for a repeating-signal search")

    # Search the complete observing baseline so long-period planets are not
    # invisible to candidate selection. A representative sample keeps this
    # broad scan bounded, and the second pass restores fine period precision.
    coarse_time, coarse_flux = time, flux
    if coarse_time.size > MAX_SEARCH_POINTS:
        sample = np.linspace(0, coarse_time.size - 1, MAX_SEARCH_POINTS, dtype=int)
        coarse_time, coarse_flux = coarse_time[sample], coarse_flux[sample]

    # Transit duration must stay small relative to orbital period. A single
    # global duration grid previously let broad stellar variability at 0.5 days
    # outrank compact transits such as TRAPPIST-1's. Search physical duration
    # ranges independently while still allowing long Kepler events.
    bands = [
        (0.5, min(3.0, maximum_period), [0.02, 0.04, 0.06, 0.08, 0.12]),
        (3.0, min(20.0, maximum_period), [0.04, 0.08, 0.12, 0.2, 0.3]),
        (20.0, maximum_period, [0.08, 0.12, 0.2, 0.3, 0.4, 0.6, 0.8]),
    ]
    coarse_model = BoxLeastSquares(coarse_time, coarse_flux)
    candidates: list[tuple[float, float, float, list[float]]] = []
    total_period_count = 6_000 if maximum_period > 45 else 3_000
    total_log_span = np.log(maximum_period / 0.5)
    for low_period, high_period, durations in bands:
        if high_period <= low_period:
            continue
        fraction = np.log(high_period / low_period) / total_log_span
        period_count = max(1_500, int(total_period_count * fraction))
        coarse_periods = np.geomspace(low_period, high_period, period_count)
        coarse = coarse_model.power(coarse_periods, durations)
        band_candidates: list[float] = []
        for index in np.argsort(coarse.power)[::-1]:
            period = float(coarse_periods[index])
            if all(abs(period - existing) / existing > 0.01 for existing in band_candidates):
                position = int(index)
                grid_low = float(coarse_periods[max(0, position - 2)])
                grid_high = float(coarse_periods[min(coarse_periods.size - 1, position + 2)])
                candidates.append((period, grid_low, grid_high, durations))
                band_candidates.append(period)
            if len(band_candidates) == 6:
                break

    if time.size > MAX_SEARCH_POINTS:
        sample = np.linspace(0, time.size - 1, MAX_SEARCH_POINTS, dtype=int)
        search_time, search_flux = time[sample], flux[sample]
    else:
        search_time, search_flux = time, flux
    refined_model = BoxLeastSquares(search_time, search_flux)
    best: dict[str, float] | None = None
    for _, low, high, durations in candidates:
        result = refined_model.power(np.linspace(low, high, 160), durations)
        index = int(np.nanargmax(result.power))
        power = float(result.power[index])
        if best is None or power > best["bls_power"]:
            best = {
                "period_days": float(result.period[index]),
                "duration_days": float(result.duration[index]),
                "transit_time": float(result.transit_time[index]),
                "bls_power": power,
            }
    if best is None:
        raise RuntimeError("No repeating-signal periods could be tested")
    return {
        **best,
    }


def prepare_curve_points(phases: np.ndarray, flux: np.ndarray, limit: int = MAX_PLOT_POINTS) -> list[tuple[float, float]]:
    """Return phase-sorted representative points suitable for an interactive SVG."""
    order = np.argsort(phases)
    if order.size > limit:
        order = order[np.linspace(0, order.size - 1, limit, dtype=int)]
    return [(float(phases[index]), float(flux[index])) for index in order]


def catalog_matches(tic_id: str, target_name: str = "") -> dict:
    errors: list[str] = []

    def catalog_query(query: str) -> tuple[list[dict[str, str]], bool]:
        try:
            return retry_archive_call(lambda: _tap(query), attempts=3), True
        except Exception as exc:
            errors.append(str(exc))
            return [], False

    planets, planets_available = catalog_query(
        "select pl_name,hostname,discoverymethod,disc_year,pl_orbper,pl_rade,tran_flag "
        f"from pscomppars where tic_id like '%{tic_id}%'"
    )
    try:
        toi_id = int(tic_id)
    except (TypeError, ValueError):
        tois, tois_available = [], False
    else:
        tois, tois_available = catalog_query(
            "select toi,tid,tfopwg_disp,pl_orbper,pl_trandep,pl_rade "
            f"from toi where tid={toi_id}"
        )

    # General TIC resolution may return a newer source ID than the legacy TIC ID
    # attached to an Exoplanet Archive record. Follow NASA's alias mapping only
    # when the direct, current-TIC comparison has no match.
    if not planets and not tois and target_name:
        try:
            alias = _resolve_catalog_alias(target_name)
            if alias:
                legacy_tic_id = alias["tic_id"]
                canonical_name = str(alias["resolved_name"]).replace("'", "''")
                alias_planets, alias_planets_available = catalog_query(
                    "select pl_name,hostname,discoverymethod,disc_year,pl_orbper,pl_rade,tran_flag "
                    "from pscomppars "
                    f"where tic_id like '%{legacy_tic_id}%' or upper(hostname)=upper('{canonical_name}')"
                )
                alias_tois, alias_tois_available = catalog_query(
                    "select toi,tid,tfopwg_disp,pl_orbper,pl_trandep,pl_rade "
                    f"from toi where tid={int(legacy_tic_id)}"
                )
                if alias_planets or not planets_available:
                    planets, planets_available = alias_planets, alias_planets_available
                if alias_tois or not tois_available:
                    tois, tois_available = alias_tois, alias_tois_available
        except Exception:
            pass
    dispositions = {row.get("tfopwg_disp", "").upper() for row in tois}
    if planets:
        status = "confirmed"
    elif dispositions & {"CP", "PC", "APC"}:
        status = "candidate"
    elif tois:
        status = "cataloged_toi"
    elif planets_available and tois_available:
        status = "no_match"
    else:
        status = "unavailable"
    host_name = next((row.get("hostname", "").strip() for row in planets if row.get("hostname", "").strip()), None)
    if host_name is None and tois:
        toi = tois[0].get("toi", "").strip()
        if toi:
            host_name = f"TOI-{toi.split('.')[0]}"
    result = {"status": status, "host_name": host_name, "planets": planets, "tois": tois}
    if errors:
        result["message"] = "; ".join(dict.fromkeys(errors))
    return result


def inspect_target(target: str) -> dict:
    resolved = resolve_target(target)
    tic_id = resolved["tic_id"]
    # Run these sequentially on memory-constrained hobby hosting. Concurrent
    # archive clients increase peak memory enough to trigger Render's 512 MB
    # out-of-memory kill while lightkurve is processing FITS products.
    if resolved.get("kepler_id"):
        times, fluxes, label = fetch_kepler_lightcurve(resolved["kepler_id"])
    else:
        times, fluxes, label = fetch_tess_lightcurve(tic_id)
    catalog = catalog_matches(tic_id, target.strip())
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
