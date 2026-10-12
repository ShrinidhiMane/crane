"""The worker loop: lease a job, run it, report the result.

On SIGTERM (what Kubernetes sends before it stops a pod) the worker finishes
the job it's on, then exits. If the pod is killed hard instead (SIGKILL, node
lost, out of memory), the job's lease runs out and another worker retries it.
"""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

import redis

from . import jobs
from .config import Settings, redis_client
from .queue import Queue

log = logging.getLogger("crane.worker")


class Worker:
    def __init__(self, queue: Queue, settings: Settings, idle_sleep: float = 0.5, reap_every: float = 5.0):
        self.q = queue
        self.s = settings
        self.idle_sleep = idle_sleep
        self.reap_every = reap_every
        self.stopping = False
        self._last_reap = 0.0
        self._beat = Path(settings.heartbeat_file)

    def stop(self, *_):
        if not self.stopping:
            log.info("got stop signal, finishing the current job before exiting")
        self.stopping = True

    def beat(self) -> None:
        """Tell Redis (for the dashboard) and the liveness probe that we're alive."""
        self.q.heartbeat()
        self._beat.touch()

    def run_once(self) -> bool:
        """Do at most one job. Returns True if a job was run."""
        now = time.monotonic()
        if now - self._last_reap >= self.reap_every:
            self._last_reap = now
            reaped = self.q.reap()
            if any(reaped.values()):
                log.warning("recovered lost jobs: %s", reaped)

        job = self.q.claim()
        if job is None:
            return False

        log.info("start job=%s kind=%s n=%s attempt=%s", job.id, job.kind, job.n, job.attempts)
        started = time.monotonic()
        try:
            result = jobs.run(job.kind, job.n)
        except Exception as e:  # noqa: BLE001 - any job error is reported, not fatal
            outcome = self.q.give_up_on(job.id, f"{type(e).__name__}: {e}")
            log.error("job=%s raised %r -> %s", job.id, e, outcome)
            return True

        if self.q.complete(job.id, result):
            log.info("done job=%s in %.0f ms result=%s", job.id, (time.monotonic() - started) * 1000, result)
        else:
            log.warning("job=%s finished after its lease expired; another worker owns it now", job.id)
        return True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("worker %s on node %s is ready", self.s.pod_name, self.s.node_name)
        while not self.stopping:
            try:
                self.beat()
                if not self.run_once():
                    time.sleep(self.idle_sleep)
            except redis.ConnectionError as e:
                # Redis is down or restarting. Keep beating the liveness file so
                # Kubernetes doesn't restart a healthy worker for someone else's outage.
                log.warning("redis unavailable (%s), retrying in 2s", e)
                self._beat.touch()
                time.sleep(2)
        log.info("worker %s stopped cleanly", self.s.pod_name)


def main() -> None:
    settings = Settings()
    Worker(Queue(redis_client(settings), settings), settings).run_forever()
