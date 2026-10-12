"""A reliable job queue on Redis.

How a job moves:

    submit ──► crane:queue ──claim──► crane:leases ──complete──► done
                   ▲                       │
                   └──── retry ◄── lease expired / job raised (attempts left)
                                           │
                                           └──► failed (no attempts left)

The important rule: a worker never just *takes* a job. It *leases* it for a
few seconds. If the worker's pod is killed mid-job, the lease runs out and the
reaper puts the job back in the queue for another worker. So a crashed pod
delays a job; it never loses it.

Every state change is a small Lua script, so Redis runs it as one atomic step
and two pods can never grab or finish the same job at the same time.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings

QUEUE = "crane:queue"  # list of job ids waiting to run (LPUSH in, RPOP out)
LEASES = "crane:leases"  # zset: job id -> lease expiry (unix seconds)
JOBS = "crane:jobs"  # zset: job id -> created time (for listing)
FINISHED = "crane:finished"  # zset: job id -> finished time (for trimming)
STATS = "crane:stats"  # hash: submitted / done / failed / retried
BY_WORKER = "crane:by_worker"  # hash: pod name -> jobs finished
WORKERS = "crane:workers"  # zset: pod name -> last heartbeat

KINDS = ("primes", "sleep")
MAX_SLEEP = 15


def job_key(job_id: str) -> str:
    return f"crane:job:{job_id}"


# KEYS: queue, leases   ARGV: now, lease_seconds, worker, node
_CLAIM = """
local id = redis.call('RPOP', KEYS[1])
if not id then return false end
local jk = 'crane:job:' .. id
redis.call('ZADD', KEYS[2], tonumber(ARGV[1]) + tonumber(ARGV[2]), id)
redis.call('HINCRBY', jk, 'attempts', 1)
redis.call('HSET', jk, 'status', 'running', 'worker', ARGV[3], 'node', ARGV[4], 'started', ARGV[1])
return id
"""

# KEYS: leases, finished, stats, by_worker   ARGV: id, worker, now, result_json
# Only the worker that still holds the lease may finish the job. If the lease
# already expired and the job went to someone else, this result is dropped.
_COMPLETE = """
local jk = 'crane:job:' .. ARGV[1]
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
if redis.call('HGET', jk, 'worker') ~= ARGV[2] then return 0 end
redis.call('ZREM', KEYS[1], ARGV[1])
local started = tonumber(redis.call('HGET', jk, 'started'))
local ms = math.floor((tonumber(ARGV[3]) - started) * 1000)
redis.call('HSET', jk, 'status', 'done', 'result', ARGV[4], 'finished', ARGV[3], 'duration_ms', ms)
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[1])
redis.call('HINCRBY', KEYS[3], 'done', 1)
redis.call('HINCRBY', KEYS[4], ARGV[2], 1)
return 1
"""

# Put a job back in the queue, or mark it failed if it's out of attempts.
# KEYS: leases, queue, finished, stats   ARGV: id, now, max_attempts, error, worker ('' = any)
_RETRY_OR_FAIL = """
local jk = 'crane:job:' .. ARGV[1]
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
if ARGV[5] ~= '' and redis.call('HGET', jk, 'worker') ~= ARGV[5] then return 0 end
redis.call('ZREM', KEYS[1], ARGV[1])
local attempts = tonumber(redis.call('HGET', jk, 'attempts') or '0')
if attempts >= tonumber(ARGV[3]) then
  redis.call('HSET', jk, 'status', 'failed', 'error', ARGV[4], 'finished', ARGV[2])
  redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
  redis.call('HINCRBY', KEYS[4], 'failed', 1)
  return 2
end
redis.call('HSET', jk, 'status', 'queued', 'error', ARGV[4], 'worker', '')
redis.call('RPUSH', KEYS[2], ARGV[1])
redis.call('HINCRBY', KEYS[4], 'retried', 1)
return 1
"""


class InvalidJob(ValueError):
    """The client asked for a job we won't run."""


@dataclass
class Claimed:
    id: str
    kind: str
    n: int
    attempts: int


class Queue:
    def __init__(self, r, settings: Settings, clock: Callable[[], float] = time.time):
        self.r = r
        self.s = settings
        self.clock = clock
        self._claim = r.register_script(_CLAIM)
        self._complete = r.register_script(_COMPLETE)
        self._retry_or_fail = r.register_script(_RETRY_OR_FAIL)

    # ---- submitting -------------------------------------------------------

    def validate(self, kind: str, n: int) -> None:
        if kind not in KINDS:
            raise InvalidJob(f"kind must be one of {', '.join(KINDS)}")
        if kind == "primes" and not 1 <= n <= self.s.max_n:
            raise InvalidJob(f"n must be between 1 and {self.s.max_n:,}")
        if kind == "sleep" and not 1 <= n <= MAX_SLEEP:
            raise InvalidJob(f"sleep seconds must be between 1 and {MAX_SLEEP}")

    def submit(self, kind: str, n: int, count: int = 1, submitted_by: str = "") -> list[str]:
        self.validate(kind, n)
        now = self.clock()
        ids = [uuid.uuid4().hex[:12] for _ in range(count)]
        pipe = self.r.pipeline(transaction=True)
        for job_id in ids:
            pipe.hset(
                job_key(job_id),
                mapping={
                    "id": job_id,
                    "kind": kind,
                    "n": n,
                    "status": "queued",
                    "attempts": 0,
                    "created": now,
                    "submitted_by": submitted_by,
                },
            )
            pipe.zadd(JOBS, {job_id: now})
            pipe.lpush(QUEUE, job_id)
        pipe.hincrby(STATS, "submitted", count)
        pipe.execute()
        return ids

    # ---- working ----------------------------------------------------------

    def claim(self) -> Claimed | None:
        job_id = self._claim(
            keys=[QUEUE, LEASES],
            args=[self.clock(), self.s.lease_seconds, self.s.pod_name, self.s.node_name],
        )
        if not job_id:
            return None
        h = self.r.hgetall(job_key(job_id))
        return Claimed(id=job_id, kind=h["kind"], n=int(h["n"]), attempts=int(h["attempts"]))

    def complete(self, job_id: str, result: Any) -> bool:
        return bool(
            self._complete(
                keys=[LEASES, FINISHED, STATS, BY_WORKER],
                args=[job_id, self.s.pod_name, self.clock(), json.dumps(result)],
            )
        )

    def give_up_on(self, job_id: str, error: str) -> str:
        """The job raised an error: retry it, or fail it if out of attempts."""
        code = self._retry_or_fail(
            keys=[LEASES, QUEUE, FINISHED, STATS],
            args=[job_id, self.clock(), self.s.max_attempts, error[:500], self.s.pod_name],
        )
        return {0: "not-mine", 1: "retried", 2: "failed"}[int(code)]

    def heartbeat(self) -> None:
        self.r.zadd(WORKERS, {self.s.pod_name: self.clock()})

    # ---- housekeeping -----------------------------------------------------

    def reap(self) -> dict[str, int]:
        """Retry (or fail) every job whose lease has run out."""
        now = self.clock()
        out = {"retried": 0, "failed": 0}
        for job_id in self.r.zrangebyscore(LEASES, "-inf", now):
            code = int(
                self._retry_or_fail(
                    keys=[LEASES, QUEUE, FINISHED, STATS],
                    args=[job_id, now, self.s.max_attempts, "lease expired: the worker stopped responding", ""],
                )
            )
            if code == 1:
                out["retried"] += 1
            elif code == 2:
                out["failed"] += 1
        return out

    def trim(self) -> int:
        """Delete the oldest finished jobs beyond the keep_finished limit."""
        extra = self.r.zcard(FINISHED) - self.s.keep_finished
        if extra <= 0:
            return 0
        old = self.r.zrange(FINISHED, 0, extra - 1)
        pipe = self.r.pipeline(transaction=True)
        for job_id in old:
            pipe.delete(job_key(job_id))
        pipe.zrem(FINISHED, *old)
        pipe.zrem(JOBS, *old)
        pipe.execute()
        return len(old)

    def forget_idle_workers(self, idle_seconds: int = 600) -> int:
        return int(self.r.zremrangebyscore(WORKERS, "-inf", self.clock() - idle_seconds))

    # ---- reading ----------------------------------------------------------

    def get(self, job_id: str) -> dict | None:
        h = self.r.hgetall(job_key(job_id))
        return _decode(h) if h else None

    def recent(self, limit: int = 30) -> list[dict]:
        ids = self.r.zrevrange(JOBS, 0, limit - 1)
        pipe = self.r.pipeline(transaction=False)
        for job_id in ids:
            pipe.hgetall(job_key(job_id))
        return [_decode(h) for h in pipe.execute() if h]

    def stats(self, active_within: int = 15) -> dict:
        now = self.clock()
        pipe = self.r.pipeline(transaction=False)
        pipe.llen(QUEUE)
        pipe.zcard(LEASES)
        pipe.hgetall(STATS)
        pipe.hgetall(BY_WORKER)
        pipe.zrangebyscore(WORKERS, now - active_within, "+inf", withscores=True)
        queued, running, totals, by_worker, active = pipe.execute()
        done_by = {k: int(v) for k, v in by_worker.items()}
        workers = [
            {"pod": pod, "last_seen_s": round(now - seen, 1), "done": done_by.get(pod, 0)} for pod, seen in active
        ]
        workers.sort(key=lambda w: w["pod"])
        return {
            "queued": queued,
            "running": running,
            "submitted": int(totals.get("submitted", 0)),
            "done": int(totals.get("done", 0)),
            "failed": int(totals.get("failed", 0)),
            "retried": int(totals.get("retried", 0)),
            "active_workers": workers,
            "done_by_worker": done_by,
        }


def _decode(h: dict) -> dict:
    out: dict[str, Any] = dict(h)
    for key in ("n", "attempts", "duration_ms"):
        if out.get(key, "") != "":
            out[key] = int(out[key])
    for key in ("created", "started", "finished"):
        if out.get(key, "") != "":
            out[key] = float(out[key])
    if out.get("result"):
        out["result"] = json.loads(out["result"])
    return out
