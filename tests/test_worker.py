from __future__ import annotations

from linkscribe.processor import MediaResult, ProcessingError
from linkscribe.store import JobStore
from linkscribe.worker import Worker


def test_worker_completes_claimed_job(settings) -> None:
    store = JobStore(settings.db_path)
    store.initialize()
    created = store.create("https://youtu.be/abc")
    worker = Worker(settings, poll_seconds=0.01)

    class SuccessfulProcessor:
        def process(self, job_id: str, url: str) -> MediaResult:
            assert job_id == created.id
            assert url == created.source_url
            worker.stop()
            return MediaResult("Hello", "Demo", "YouTube", 3.0)

    worker.processor = SuccessfulProcessor()
    worker.run_forever()

    completed = store.get(created.id)
    assert completed.status == "completed"
    assert completed.transcript == "Hello"


def test_worker_records_expected_processing_failure(settings) -> None:
    store = JobStore(settings.db_path)
    store.initialize()
    created = store.create("https://youtu.be/abc")
    worker = Worker(settings, poll_seconds=0.01)

    class FailedProcessor:
        def process(self, job_id: str, url: str) -> MediaResult:
            worker.stop()
            raise ProcessingError("Media unavailable")

    worker.processor = FailedProcessor()
    worker.run_forever()

    failed = store.get(created.id)
    assert failed.status == "failed"
    assert failed.error == "Media unavailable"
