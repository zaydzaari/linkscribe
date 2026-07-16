from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from linkscribe.processor import MediaProcessor, ProcessingError, normalize_and_validate_url


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/abc123",
        "https://www.youtube.com/watch?v=abc123",
        "https://m.youtube.com/shorts/abc123",
        "https://www.tiktok.com/@person/video/123",
        "https://vm.tiktok.com/abc123/",
        "https://www.instagram.com/reel/abc123/",
    ],
)
def test_supported_urls(url: str) -> None:
    assert normalize_and_validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://youtube.com.evil.example/video",
        "https://127.0.0.1/video",
        "https://user:pass@youtube.com/video",
        "https://youtube.com:8443/video",
        "https://example.com/video",
    ],
)
def test_unsafe_or_unsupported_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_and_validate_url(url)


def test_duration_limit_is_enforced(settings, monkeypatch) -> None:
    processor = MediaProcessor(settings)
    metadata = subprocess.CompletedProcess([], 0, json.dumps({"duration": 3601}), "")
    monkeypatch.setattr(processor, "_run", lambda *args, **kwargs: metadata)
    with pytest.raises(ProcessingError, match="60-minute limit"):
        processor.inspect("https://youtu.be/abc")


def test_download_prefers_an_audio_bearing_h264_fallback(settings, monkeypatch) -> None:
    processor = MediaProcessor(settings)
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: int):
        commands.append(command)
        if "--dump-single-json" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({"duration": 1}), "")
        if "--format" in command:
            template = Path(command[command.index("--output") + 1])
            template.with_name("source.mp4").write_bytes(b"media")
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"wav")
        elif "--output-txt" in command:
            prefix = Path(command[command.index("--output-file") + 1])
            prefix.with_suffix(".txt").write_text("Speech", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(processor, "_run", fake_run)
    processor.process("format-test", "https://www.tiktok.com/@person/video/123")

    download = next(command for command in commands if "--format" in command)
    selector = download[download.index("--format") + 1]
    assert selector == "bestaudio/best[vcodec^=h264][acodec!=none]/best[acodec!=none]"


def test_processing_pipeline_cleans_temporary_media(settings, monkeypatch) -> None:
    processor = MediaProcessor(settings)

    def fake_run(command: list[str], *, timeout: int):
        if "--dump-single-json" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"title": "Demo", "duration": 12}),
                "",
            )
        if "--format" in command:
            template = Path(command[command.index("--output") + 1])
            template.with_name("source.m4a").write_bytes(b"audio")
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"wav")
        elif "--output-txt" in command:
            prefix = Path(command[command.index("--output-file") + 1])
            prefix.with_suffix(".txt").write_text(" Hello   from the demo. \n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(processor, "_run", fake_run)
    result = processor.process("job123", "https://youtu.be/abc")

    assert result.transcript == "Hello from the demo."
    assert result.title == "Demo"
    assert result.source == "YouTube"
    assert result.duration_seconds == 12
    assert not (settings.jobs_dir / "job123").exists()
