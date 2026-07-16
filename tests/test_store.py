from __future__ import annotations

from linkscribe.store import JobStore


def test_job_lifecycle(settings) -> None:
    store = JobStore(settings.db_path, ttl_hours=24)
    store.initialize()
    queued = store.create("https://youtu.be/abc")

    assert queued.status == "queued"
    assert store.queue_position(queued) == 1

    processing = store.claim_next()
    assert processing is not None
    assert processing.id == queued.id
    assert processing.status == "processing"
    assert processing.attempts == 1

    store.complete(
        processing.id,
        transcript="hello",
        title="Demo",
        source="YouTube",
        duration_seconds=4.2,
    )
    completed = store.get(processing.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.transcript == "hello"
    assert completed.completed_at is not None


def test_queue_is_fifo_and_claim_is_exclusive(settings) -> None:
    store = JobStore(settings.db_path)
    store.initialize()
    first = store.create("https://youtu.be/first")
    second = store.create("https://youtu.be/second")

    assert store.queue_position(second) == 2
    assert store.claim_next().id == first.id
    assert store.claim_next().id == second.id
    assert store.claim_next() is None


def test_cancel_only_accepts_queued_jobs(settings) -> None:
    store = JobStore(settings.db_path)
    store.initialize()
    job = store.create("https://youtu.be/abc")
    assert store.cancel(job.id)
    assert store.get(job.id).status == "cancelled"
    assert not store.cancel(job.id)


def test_interrupted_jobs_are_requeued(settings) -> None:
    store = JobStore(settings.db_path)
    store.initialize()
    job = store.create("https://youtu.be/abc")
    store.claim_next()

    assert store.recover_interrupted() == 1
    recovered = store.get(job.id)
    assert recovered.status == "queued"
    assert "re-queued" in recovered.error
