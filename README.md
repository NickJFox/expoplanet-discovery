# Transit Lens — Exoplanet Transit Discovery

Transit Lens is a full-stack tool for screening public TESS and Kepler light curves. It independently searches available brightness measurements for a repeating transit-like dip, then separately compares the target with confirmed planets and TESS Objects of Interest in the NASA Exoplanet Archive.

> The signal score is a screening result, not a planet confirmation. Stellar variability, eclipsing binaries, contamination, and instrument artifacts require human vetting and follow-up observations.

## Features

- Search by TIC ID or general star name when public TESS or Kepler light-curve data is available
- Choose a random example target
- Interactive phase-folded light-curve visualization
- Independent Box Least Squares period search followed by transit depth, SNR, duration, and morphology screening
- Separate app analysis, NASA TOI candidate, and confirmed-planet states
- No synthetic fallback in the web API

## Project structure

```text
backend/           FastAPI routes, remote data access, and signal analysis
frontend/          React, TypeScript, and Vite web interface
main.py            Original plot-generating command-line tool
tests/             Analysis, API, and plotting tests
```

## Run locally

Use Python 3.10 or newer and Node.js 20 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
uvicorn backend.app:app --reload
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` requests to the backend at `http://127.0.0.1:8000`.

## Test and build

```bash
python3 -m pytest
cd frontend && npm run build
```

## Deploy free on Render

The included `render.yaml` deploys the frontend and API together as one free
Render web service. Push the repository to GitHub, open Render's Blueprint
creation page, connect the repository, and apply the detected blueprint.

Render builds the Vite frontend and FastAPI serves it alongside `/api`. Free
services sleep after periods of inactivity, so the first visit may take about a
minute to start and downloaded light-curve caches are temporary.

## API

- `GET /api/health`
- `GET /api/targets/resolve?q=TIC%20261136679`
- `GET /api/targets/random`
- `GET /api/targets/{target}/inspect`

The inspection response includes the normalized folded curve, independently detected period, signal measurements and reasons, and catalog matches. Remote inspection requires access to MAST and the NASA Exoplanet Archive.

Cleaned light curves are cached for 24 hours, and completed inspections are cached while the API process is running. The first search for a new star may take several seconds while archive products download; repeat searches are substantially faster.

## Detection approach

The detector does not use catalog status when searching or scoring a graph. It cleans and stitches ordinary public light curves, selecting original Kepler quarters for Kepler/KOI/KIC names and TESS sectors for other targets. It uses a coarse-to-fine Box Least Squares search across the complete observing baseline to identify the strongest repeating box-shaped dip from 0.5 days up to the lesser of 400 days or half the available baseline, and folds the measurements on that period. It then estimates out-of-event noise using a robust median absolute deviation and screens signal-to-noise, depth, localized shoulders, and event width. Catalog matching happens afterward. This is intentionally explainable and conservative; a production scientific pipeline should add odd/even tests, secondary-eclipse searches, centroid checks, stellar-radius constraints, and injection/recovery validation.

### Validation controls

| Target | Appropriate data | Expected interpretation |
| --- | --- | --- |
| WASP-46 | TESS | Strong transit-like signal; confirmed transiting planet |
| TOI-700 | TESS | Transit-like signal; confirmed transiting planets |
| TRAPPIST-1 | TESS | Short transit-like signal near 1.51 days; confirmed transiting system |
| Kepler-90 | Kepler | Transit-like signal; confirmed multi-planet system |
| Proxima Centauri | TESS | No required transit signal; its confirmed planets were found by radial velocity |
| 51 Pegasi | TESS, when available | No required transit signal; 51 Pegasi b does not transit from our viewpoint |
| Sirius A | TESS, when available | Negative control; no cataloged confirmed planet |

These controls prevent a single-star threshold tweak from being treated as a general fix. A confirmed planet is not automatically expected to produce a detectable dip: it must transit from our viewing angle, fall within the observations, and be deep enough for that mission’s precision.
