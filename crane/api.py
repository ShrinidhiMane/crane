"""HTTP API and dashboard.

Every response carries an X-Served-By header with the pod's name, and the
dashboard shows it, so you can watch the LoadBalancer spread requests across
API replicas.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, redis_client
from .queue import KINDS, InvalidJob, Queue

STATIC = Path(__file__).parent / "static"


class NewJobs(BaseModel):
    kind: str = Field(default="primes", description=f"one of {', '.join(KINDS)}")
    n: int = Field(default=1_000_000, description="primes: count primes up to n; sleep: seconds")
    count: int = Field(default=1, ge=1, le=500, description="how many identical jobs to add")


def create_app(queue: Queue | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if queue is None:
        queue = Queue(redis_client(settings), settings)

    app = FastAPI(title="Crane", version=__version__)

    @app.middleware("http")
    async def served_by(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Served-By"] = settings.pod_name
        return response

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz():
        """Liveness: the process is up and answering. Deliberately ignores Redis."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        """Readiness: only take traffic while Redis is reachable."""
        try:
            queue.r.ping()
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"status": "not ready", "error": str(e)}, status_code=503)
        return {"status": "ready"}

    @app.post("/api/jobs", status_code=201)
    def add_jobs(body: NewJobs):
        try:
            ids = queue.submit(body.kind, body.n, body.count, submitted_by=settings.pod_name)
        except InvalidJob as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {"ids": ids, "served_by": settings.pod_name}

    @app.get("/api/jobs")
    def list_jobs(limit: int = 30):
        return {"jobs": queue.recent(max(1, min(limit, 200)))}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job

    @app.get("/api/stats")
    def stats():
        return {**queue.stats(), "served_by": settings.pod_name, "node": settings.node_name}

    return app


def main() -> None:
    import os

    import uvicorn

    uvicorn.run(
        "crane.api:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
        access_log=False,
    )
