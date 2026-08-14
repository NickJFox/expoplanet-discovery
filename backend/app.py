"""FastAPI application for interactive TESS signal inspection."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .services import ArchiveTimeoutError, inspect_target, random_target, resolve_target


app = FastAPI(title="Exoplanet Discovery API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/targets/resolve")
def resolve(q: str = Query(min_length=1)) -> dict:
    try:
        return resolve_target(q)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/targets/random")
def random_inspection() -> dict:
    try:
        return inspect_target(random_target())
    except ArchiveTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No usable public TESS data was available for this star. Please try another.") from exc


@app.get("/api/targets/{target}/inspect")
def inspect(target: str) -> dict:
    try:
        return inspect_target(target)
    except ArchiveTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No TESS data for this star. Please try another.") from exc
