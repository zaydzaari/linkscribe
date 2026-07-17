from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

import clients.linkscribe as client_module
from clients.linkscribe import ClientError, JobFailedError, LinkScribeClient


def test_client_requires_https() -> None:
    with pytest.raises(ClientError, match="HTTPS"):
        LinkScribeClient("http://example.com", "secret")


def test_client_allows_local_http_for_development() -> None:
    client = LinkScribeClient("http://127.0.0.1:8080", "secret")
    assert client.base_url == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1.evil.example",
        "https://user:pass@example.com",
        "https://example.com/api",
        "https://example.com?token=leak",
    ],
)
def test_client_rejects_unsafe_api_origins(url: str) -> None:
    with pytest.raises(ClientError):
        LinkScribeClient(url, "secret")


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_request_sends_bearer_token_and_json(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, *, timeout: int):
        captured["authorization"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse({"id": "job-1", "status": "queued"})

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    client = LinkScribeClient("https://api.example.com", "secret", timeout=12)
    result = client.submit("https://youtu.be/demo")

    assert result["id"] == "job-1"
    assert captured == {
        "authorization": "Bearer secret",
        "body": {"url": "https://youtu.be/demo"},
        "timeout": 12,
    }


def test_http_error_returns_api_detail(monkeypatch) -> None:
    def fail(*_: object, **__: object):
        raise HTTPError(
            "https://api.example.com/v1/jobs",
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"invalid credential"}'),
        )

    monkeypatch.setattr(client_module, "urlopen", fail)
    client = LinkScribeClient("https://api.example.com", "secret")

    with pytest.raises(ClientError, match="API error 401: invalid credential"):
        client.submit("https://youtu.be/demo")


def test_network_error_is_wrapped(monkeypatch) -> None:
    def fail(*_: object, **__: object):
        raise URLError("connection refused")

    monkeypatch.setattr(client_module, "urlopen", fail)
    client = LinkScribeClient("https://api.example.com", "secret")

    with pytest.raises(ClientError, match="Could not reach LinkScribe: connection refused"):
        client.status("job-1")


def test_transcript_reads_every_chunk(monkeypatch) -> None:
    client = LinkScribeClient("https://api.example.com", "secret")
    responses = iter(
        [
            {"text": "Hello ", "next_offset": 6},
            {"text": "world", "next_offset": None},
        ]
    )
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: next(responses))

    assert client.transcript("job-1") == "Hello world"


def test_wait_returns_completed_job(monkeypatch, capsys) -> None:
    client = LinkScribeClient("https://api.example.com", "secret")
    responses = iter(
        [
            {"id": "job-1", "status": "processing"},
            {"id": "job-1", "status": "completed"},
        ]
    )
    monkeypatch.setattr(client, "status", lambda *args, **kwargs: next(responses))

    result = client.wait("job-1", max_wait=5)

    assert result["status"] == "completed"
    assert "LinkScribe job job-1: completed" in capsys.readouterr().err


def test_wait_raises_for_failed_job(monkeypatch) -> None:
    client = LinkScribeClient("https://api.example.com", "secret")
    monkeypatch.setattr(
        client,
        "status",
        lambda *args, **kwargs: {"id": "job-1", "status": "failed", "error": "no audio"},
    )

    with pytest.raises(JobFailedError, match="no audio"):
        client.wait("job-1", max_wait=5)


def test_json3_captions_are_normalized_and_duplicate_events_removed(tmp_path) -> None:
    captions = tmp_path / "captions.en-orig.json3"
    captions.write_text(
        json.dumps(
            {
                "events": [
                    {"segs": [{"utf8": "Hello\n"}, {"utf8": "world"}]},
                    {"segs": [{"utf8": "Hello world"}]},
                    {"segs": [{"utf8": "This is LinkScribe."}]},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        client_module._parse_youtube_json3(captions)
        == "Hello world This is LinkScribe."
    )


def test_transcribe_uses_local_captions_after_youtube_bot_challenge(
    monkeypatch, capsys
) -> None:
    client = LinkScribeClient("https://api.example.com", "secret")
    monkeypatch.setattr(client, "submit", lambda url: {"id": "job-1"})

    def fail_wait(*_: object, **__: object):
        raise JobFailedError(
            {
                "id": "job-1",
                "status": "failed",
                "error": "Sign in to confirm you're not a bot",
            }
        )

    monkeypatch.setattr(client, "wait", fail_wait)
    monkeypatch.setattr(
        client_module,
        "fetch_local_youtube_captions",
        lambda url: "Recovered public caption transcript.",
    )

    transcript = client_module.transcribe_url(
        client, "https://www.youtube.com/watch?v=demo"
    )

    assert transcript == "Recovered public caption transcript."
    assert "trying public captions locally" in capsys.readouterr().err


def test_transcribe_does_not_fallback_for_unrelated_failures(monkeypatch) -> None:
    client = LinkScribeClient("https://api.example.com", "secret")
    monkeypatch.setattr(client, "submit", lambda url: {"id": "job-1"})
    monkeypatch.setattr(
        client,
        "wait",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            JobFailedError(
                {"id": "job-1", "status": "failed", "error": "No speech detected"}
            )
        ),
    )

    with pytest.raises(JobFailedError, match="No speech detected"):
        client_module.transcribe_url(
            client, "https://www.youtube.com/watch?v=demo"
        )
