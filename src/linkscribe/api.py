from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .processor import normalize_and_validate_url
from .store import Job, JobStore

LOGGER = logging.getLogger("linkscribe.api")


class CreateJobRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048, description="Public media URL")


class JobResponse(BaseModel):
    id: str
    status: str
    source_url: str
    title: str | None = None
    source: str | None = None
    duration_seconds: float | None = None
    transcript: str | None = None
    transcript_truncated: bool = False
    transcript_characters: int | None = None
    queue_position: int | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    expires_at: str


class TranscriptChunk(BaseModel):
    job_id: str
    text: str
    offset: int
    next_offset: int | None
    total_characters: int


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: deque[float] = deque()

    def check(self) -> None:
        now = time.monotonic()
        while self.requests and self.requests[0] <= now - self.window_seconds:
            self.requests.popleft()
        if len(self.requests) >= self.limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded; retry shortly")
        self.requests.append(now)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare_directories()
    store = JobStore(settings.db_path, settings.job_ttl_hours)
    limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.initialize()
        yield

    app = FastAPI(
        title="LinkScribe API",
        version=__version__,
        description="Resource-aware media transcription and English translation gateway.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        scheme, _, credential = (authorization or "").partition(" ")
        valid = scheme.lower() == "bearer" and hmac.compare_digest(
            credential, settings.api_token
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        limiter.check()

    def serialize(job: Job) -> JobResponse:
        transcript = job.transcript
        truncated = bool(transcript and len(transcript) > settings.inline_transcript_chars)
        inline = transcript[: settings.inline_transcript_chars] if transcript else None
        return JobResponse(
            id=job.id,
            status=job.status,
            source_url=job.source_url,
            title=job.title,
            source=job.source,
            duration_seconds=job.duration_seconds,
            transcript=inline,
            transcript_truncated=truncated,
            transcript_characters=len(transcript) if transcript else None,
            queue_position=store.queue_position(job),
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
            expires_at=job.expires_at,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    static_dir = Path(__file__).with_name("static")

    @app.get("/", include_in_schema=False)
    async def console() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/privacy", include_in_schema=False)
    async def privacy() -> FileResponse:
        return FileResponse(static_dir / "privacy.html")

    @app.get("/health", operation_id="healthCheck")
    async def health() -> dict[str, object]:
        worker_ready = all(
            path.is_file()
            for path in (settings.ytdlp_bin, settings.whisper_bin, settings.whisper_model)
        )
        return {
            "status": "ok",
            "version": __version__,
            "worker_ready": worker_ready,
        }

    @app.post(
        "/v1/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        operation_id="createTranscriptionJob",
        dependencies=[Depends(authenticate)],
    )
    async def create_job(payload: CreateJobRequest) -> JobResponse:
        try:
            url = normalize_and_validate_url(payload.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return serialize(store.create(url))

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobResponse,
        operation_id="getTranscriptionJob",
        dependencies=[Depends(authenticate)],
    )
    async def get_job(
        job_id: str,
        wait_seconds: Annotated[int, Query(ge=0, le=25)] = 0,
    ) -> JobResponse:
        deadline = time.monotonic() + wait_seconds
        while True:
            job = store.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status not in {"queued", "processing"} or time.monotonic() >= deadline:
                return serialize(job)
            await asyncio.sleep(1)

    @app.get(
        "/v1/jobs/{job_id}/transcript",
        response_model=TranscriptChunk,
        operation_id="getTranscriptChunk",
        dependencies=[Depends(authenticate)],
    )
    async def transcript_chunk(
        job_id: str,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1000, le=20000)] = 12000,
    ) -> TranscriptChunk:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "completed" or job.transcript is None:
            raise HTTPException(status_code=409, detail="Transcript is not ready")
        total = len(job.transcript)
        if offset >= total and total:
            raise HTTPException(status_code=416, detail="Offset is beyond the transcript")
        text = job.transcript[offset : offset + limit]
        next_offset = offset + len(text) if offset + len(text) < total else None
        return TranscriptChunk(
            job_id=job.id,
            text=text,
            offset=offset,
            next_offset=next_offset,
            total_characters=total,
        )

    @app.delete(
        "/v1/jobs/{job_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        operation_id="cancelTranscriptionJob",
        dependencies=[Depends(authenticate)],
    )
    async def cancel_job(job_id: str) -> None:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if not store.cancel(job_id):
            raise HTTPException(status_code=409, detail="Only queued jobs can be cancelled")

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception(
            "Unhandled error for %s %s", request.method, request.url.path, exc_info=exc
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


def run() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8080, access_log=False)


app = create_app() if __name__ != "__main__" else None


if __name__ == "__main__":
    run()
