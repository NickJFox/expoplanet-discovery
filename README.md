# Transit Lens — TESS Exoplanet Discovery

Transit Lens is a full-stack tool for screening public TESS light curves. It resolves general star names through the TESS Input Catalog, independently searches available brightness measurements for a repeating transit-like dip, then separately compares the target with confirmed planets and TESS Objects of Interest in the NASA Exoplanet Archive.

> The signal score is a screening result, not a planet confirmation. Stellar variability, eclipsing binaries, contamination, and instrument artifacts require human vetting and follow-up observations.

## Features

- Search by TIC ID or general star name when public TESS light-curve data is available
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

## API

- `GET /api/health`
- `GET /api/targets/resolve?q=TIC%20261136679`
- `GET /api/targets/random`
- `GET /api/targets/{target}/inspect`

The inspection response includes the normalized folded curve, independently detected period, signal measurements and reasons, and catalog matches. Remote inspection requires access to MAST and the NASA Exoplanet Archive.

Cleaned light curves are cached for 24 hours, and completed inspections are cached while the API process is running. The first search for a new star may take several seconds while archive products download; repeat searches are substantially faster.

## Detection approach

The detector does not use catalog status when searching or scoring a graph. It cleans and stitches ordinary public TESS light curves, uses a coarse-to-fine Box Least Squares search to identify the strongest repeating box-shaped dip across periods from 0.5 to 45 days, and folds the measurements on that period. It then estimates out-of-event noise using a robust median absolute deviation and screens signal-to-noise, depth, localized shoulders, and event width. Catalog matching happens afterward. This is intentionally explainable and conservative; a production scientific pipeline should add odd/even tests, secondary-eclipse searches, centroid checks, stellar-radius constraints, and injection/recovery validation.
