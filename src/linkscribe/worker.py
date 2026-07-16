from __future__ import annotations

import logging
import signal
import threading
import time

from .config import Settings
from .processor import MediaProcessor, ProcessingError
from .store import JobStore

LOGGER = logging.getLogger("linkscribe.worker")


class Worker:
    def __init__(self, settings: Settings, poll_seconds: float = 2.0) -> None:
        self.settings = settings
        self.store = JobStore(settings.db_path, settings.job_ttl_hours)
        self.processor = MediaProcessor(settings)
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()

    def stop(self, *_: object) -> None:
        self.stop_event.set()

    def run_forever(self) -> None:
        self.settings.prepare_directories()
        self.store.initialize()
        recovered = self.store.recover_interrupted()
        if recovered:
            LOGGER.warning("Re-queued %s interrupted job(s)", recovered)

        last_cleanup = 0.0
        LOGGER.info("Worker ready with %s thread(s)", self.settings.whisper_threads)
        while not self.stop_event.is_set():
            if time.monotonic() - last_cleanup >= 3600:
                deleted = self.store.delete_expired()
                if deleted:
                    LOGGER.info("Deleted %s expired job(s)", deleted)
                last_cleanup = time.monotonic()

            job = self.store.claim_next()
            if job is None:
                self.stop_event.wait(self.poll_seconds)
                continue

            LOGGER.info("Processing job %s", job.id)
            try:
                result = self.processor.process(job.id, job.source_url)
                self.store.complete(
                    job.id,
                    transcript=result.transcript,
                    title=result.title,
                    source=result.source,
                    duration_seconds=result.duration_seconds,
                )
                LOGGER.info("Completed job %s", job.id)
            except ProcessingError as exc:
                self.store.fail(job.id, str(exc))
                LOGGER.warning("Job %s failed: %s", job.id, exc)
            except Exception:
                self.store.fail(job.id, "Unexpected worker failure")
                LOGGER.exception("Unexpected failure in job %s", job.id)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    worker = Worker(settings)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    run()
