from __future__ import annotations

from fastapi.testclient import TestClient

from linkscribe.api import create_app


def auth(settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_token}"}


def test_health_is_public_and_reports_worker(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["worker_ready"] is True
    assert response.headers["x-content-type-options"] == "nosniff"


def test_job_creation_requires_authentication(settings) -> None:
    with TestClient(create_app(settings)) as client:
        missing = client.post("/v1/jobs", json={"url": "https://youtu.be/abc"})
        invalid = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer wrong"},
            json={"url": "https://youtu.be/abc"},
        )
    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_create_and_read_job(settings) -> None:
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/v1/jobs", headers=auth(settings), json={"url": "https://youtu.be/abc"}
        )
        job_id = created.json()["id"]
        fetched = client.get(f"/v1/jobs/{job_id}", headers=auth(settings))

    assert created.status_code == 202
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "queued"
    assert fetched.json()["queue_position"] == 1
    assert fetched.headers["cache-control"] == "no-store"


def test_unsupported_url_returns_clear_error(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/jobs", headers=auth(settings), json={"url": "https://example.com/video"}
        )
    assert response.status_code == 400
    assert "YouTube" in response.json()["detail"]


def test_completed_transcript_is_chunked(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/v1/jobs", headers=auth(settings), json={"url": "https://youtu.be/abc"}
        ).json()
        claimed = app.state.store.claim_next()
        app.state.store.complete(
            claimed.id,
            transcript="0123456789" * 250,
            title="Long demo",
            source="YouTube",
            duration_seconds=10,
        )
        job = client.get(f"/v1/jobs/{created['id']}", headers=auth(settings)).json()
        chunk = client.get(
            f"/v1/jobs/{created['id']}/transcript?offset=0&limit=1000",
            headers=auth(settings),
        ).json()

    assert job["transcript_truncated"] is True
    assert len(job["transcript"]) == settings.inline_transcript_chars
    assert chunk["total_characters"] == 2500
    assert chunk["next_offset"] == 1000


def test_only_queued_jobs_can_be_cancelled(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        queued = client.post(
            "/v1/jobs", headers=auth(settings), json={"url": "https://youtu.be/abc"}
        ).json()
        cancelled = client.delete(f"/v1/jobs/{queued['id']}", headers=auth(settings))
        again = client.delete(f"/v1/jobs/{queued['id']}", headers=auth(settings))
    assert cancelled.status_code == 204
    assert again.status_code == 409
