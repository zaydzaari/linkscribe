from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    api_token: str
    db_path: Path
    jobs_dir: Path
    ytdlp_bin: Path
    deno_bin: Path | None
    whisper_bin: Path
    whisper_model: Path
    whisper_threads: int = 2
    max_duration_seconds: int = 7200
    max_download_bytes: int = 512 * 1024 * 1024
    job_ttl_hours: int = 24
    inline_transcript_chars: int = 12000
    rate_limit_per_minute: int = 20
    ytdlp_cookies_file: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("LINKSCRIBE_API_TOKEN", "")
        if len(token) < 32:
            raise ValueError("LINKSCRIBE_API_TOKEN must contain at least 32 characters")

        cookies = os.getenv("LINKSCRIBE_YTDLP_COOKIES_FILE", "").strip()
        return cls(
            api_token=token,
            db_path=Path(os.getenv("LINKSCRIBE_DB_PATH", "./data/linkscribe.db")),
            jobs_dir=Path(os.getenv("LINKSCRIBE_JOBS_DIR", "./data/jobs")),
            ytdlp_bin=Path(
                os.getenv("LINKSCRIBE_YTDLP_BIN", "/opt/linkscribe/bin/yt-dlp")
            ),
            deno_bin=(
                Path(os.environ["LINKSCRIBE_DENO_BIN"])
                if os.getenv("LINKSCRIBE_DENO_BIN")
                else None
            ),
            whisper_bin=Path(
                os.getenv(
                    "LINKSCRIBE_WHISPER_BIN",
                    "/opt/whisper.cpp/build/bin/whisper-cli",
                )
            ),
            whisper_model=Path(
                os.getenv(
                    "LINKSCRIBE_WHISPER_MODEL",
                    "/opt/whisper.cpp/models/ggml-base.bin",
                )
            ),
            whisper_threads=_positive_int("LINKSCRIBE_WHISPER_THREADS", 2),
            max_duration_seconds=_positive_int("LINKSCRIBE_MAX_DURATION_SECONDS", 7200),
            max_download_bytes=_positive_int(
                "LINKSCRIBE_MAX_DOWNLOAD_BYTES", 512 * 1024 * 1024
            ),
            job_ttl_hours=_positive_int("LINKSCRIBE_JOB_TTL_HOURS", 24),
            inline_transcript_chars=_positive_int(
                "LINKSCRIBE_INLINE_TRANSCRIPT_CHARS", 12000
            ),
            rate_limit_per_minute=_positive_int(
                "LINKSCRIBE_RATE_LIMIT_PER_MINUTE", 20
            ),
            ytdlp_cookies_file=Path(cookies) if cookies else None,
        )

    def prepare_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
