from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import Settings

SUPPORTED_HOSTS = {
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "youtu.be": "YouTube",
    "youtube.com": "YouTube",
}


class ProcessingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaResult:
    transcript: str
    title: str | None
    source: str | None
    duration_seconds: float | None


def normalize_and_validate_url(raw_url: str) -> str:
    if len(raw_url) > 2048:
        raise ValueError("URL is too long")
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    if not parsed.hostname or parsed.username or parsed.password or parsed.port:
        raise ValueError("URL must contain a normal public hostname without credentials or ports")

    hostname = parsed.hostname.lower().rstrip(".")
    matched = any(hostname == host or hostname.endswith(f".{host}") for host in SUPPORTED_HOSTS)
    if not matched:
        raise ValueError("Only YouTube, TikTok, and Instagram links are supported")
    return raw_url.strip()


def source_name(url: str) -> str | None:
    hostname = (urlparse(url).hostname or "").lower()
    for host, label in SUPPORTED_HOSTS.items():
        if hostname == host or hostname.endswith(f".{host}"):
            return label
    return None


class MediaProcessor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "LC_ALL": "C.UTF-8"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessingError("Processing timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "Command failed").strip()
            detail = re.sub(r"(?i)(cookie|token|authorization)[^\n]*", "[redacted]", detail)
            raise ProcessingError(detail[-800:]) from exc

    def _ytdlp_base(self) -> list[str]:
        command = [
            str(self.settings.ytdlp_bin),
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--socket-timeout",
            "20",
            "--retries",
            "3",
            "--fragment-retries",
            "3",
        ]
        if self.settings.deno_bin:
            command.extend(
                [
                    "--js-runtimes",
                    f"deno:{self.settings.deno_bin}",
                    "--remote-components",
                    "ejs:github",
                ]
            )
        if self.settings.ytdlp_cookies_file:
            command.extend(["--cookies", str(self.settings.ytdlp_cookies_file)])
        return command

    def inspect(self, url: str) -> dict[str, object]:
        command = [*self._ytdlp_base(), "--dump-single-json", "--skip-download", url]
        result = self._run(command, timeout=90)
        try:
            metadata = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProcessingError("The media site returned invalid metadata") from exc

        duration = metadata.get("duration")
        if duration is not None and float(duration) > self.settings.max_duration_seconds:
            max_minutes = self.settings.max_duration_seconds // 60
            raise ProcessingError(f"Video is longer than the {max_minutes}-minute limit")
        return metadata

    def process(self, job_id: str, url: str) -> MediaResult:
        url = normalize_and_validate_url(url)
        if not self.settings.whisper_bin.is_file():
            raise ProcessingError("whisper.cpp executable is missing")
        if not self.settings.whisper_model.is_file():
            raise ProcessingError("Whisper model is missing")
        if not self.settings.ytdlp_bin.is_file():
            raise ProcessingError("yt-dlp executable is missing")

        metadata = self.inspect(url)
        job_dir = self.settings.jobs_dir / job_id
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, exist_ok=False)

        try:
            output_template = str(job_dir / "source.%(ext)s")
            download = [
                *self._ytdlp_base(),
                "--format",
                "bestaudio/best[vcodec^=h264][acodec!=none]/best[acodec!=none]",
                "--max-filesize",
                str(self.settings.max_download_bytes),
                "--output",
                output_template,
                url,
            ]
            self._run(download, timeout=900)

            candidates = [path for path in job_dir.glob("source.*") if path.is_file()]
            if len(candidates) != 1:
                raise ProcessingError("Audio download did not produce exactly one media file")
            source_file = candidates[0]
            if source_file.stat().st_size > self.settings.max_download_bytes:
                raise ProcessingError("Downloaded audio exceeds the configured size limit")

            wav_file = job_dir / "audio.wav"
            self._run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source_file),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav_file),
                ],
                timeout=900,
            )

            transcript_prefix = job_dir / "transcript"
            self._run(
                [
                    str(self.settings.whisper_bin),
                    "--model",
                    str(self.settings.whisper_model),
                    "--file",
                    str(wav_file),
                    "--threads",
                    str(self.settings.whisper_threads),
                    "--translate",
                    "--output-txt",
                    "--output-file",
                    str(transcript_prefix),
                    "--no-timestamps",
                ],
                timeout=max(900, self.settings.max_duration_seconds * 3),
            )
            transcript_file = transcript_prefix.with_suffix(".txt")
            if not transcript_file.is_file():
                raise ProcessingError("Whisper did not create a transcript")
            transcript = " ".join(transcript_file.read_text(encoding="utf-8").split())
            if not transcript:
                raise ProcessingError("No speech was detected in the media")

            duration = metadata.get("duration")
            return MediaResult(
                transcript=transcript,
                title=str(metadata.get("title"))[:300] if metadata.get("title") else None,
                source=source_name(url),
                duration_seconds=float(duration) if duration is not None else None,
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
