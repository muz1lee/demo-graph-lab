from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from video_perception_service.contracts import TrackPointsRequest
from video_perception_service.cotracker_backend import CoTrackerBackend


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.backend = CoTrackerBackend.from_environment()
    yield


app = FastAPI(
    title="Robot Video Perception Service",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health(request: Request) -> dict:
    return request.app.state.backend.health()


@app.post("/track_points")
async def track_points(payload: TrackPointsRequest, request: Request) -> dict:
    try:
        return await run_in_threadpool(request.app.state.backend.track, payload)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
