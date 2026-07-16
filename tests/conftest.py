from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("LINKSCRIBE_API_TOKEN", "test-token-that-is-longer-than-32-characters")

from linkscribe.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    whisper_bin = tmp_path / "whisper-cli"
    whisper_model = tmp_path / "ggml-base.bin"
    ytdlp_bin = tmp_path / "yt-dlp"
    deno_bin = tmp_path / "deno"
    whisper_bin.touch()
    whisper_model.touch()
    ytdlp_bin.touch()
    deno_bin.touch()
    return Settings(
        api_token=os.environ["LINKSCRIBE_API_TOKEN"],
        db_path=tmp_path / "data" / "linkscribe.db",
        jobs_dir=tmp_path / "data" / "jobs",
        ytdlp_bin=ytdlp_bin,
        deno_bin=deno_bin,
        whisper_bin=whisper_bin,
        whisper_model=whisper_model,
        whisper_threads=2,
        max_duration_seconds=3600,
        max_download_bytes=1024 * 1024,
        job_ttl_hours=24,
        inline_transcript_chars=20,
        rate_limit_per_minute=100,
    )
