"""Settings, read from environment variables.

On Kubernetes, the plain settings come from a ConfigMap, the Redis password
from a Secret, and POD_NAME / NODE_NAME from the Downward API.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value.strip() else default


@dataclass(frozen=True)
class Settings:
    redis_host: str = field(default_factory=lambda: os.environ.get("REDIS_HOST", "localhost"))
    redis_port: int = field(default_factory=lambda: _int("REDIS_PORT", 6379))
    redis_password: str | None = field(default_factory=lambda: os.environ.get("REDIS_PASSWORD") or None)
    # How long a worker may hold a job before it's considered lost and retried.
    lease_seconds: int = field(default_factory=lambda: _int("CRANE_LEASE_SECONDS", 30))
    max_attempts: int = field(default_factory=lambda: _int("CRANE_MAX_ATTEMPTS", 3))
    # Finished jobs kept for the dashboard; the janitor trims anything older.
    keep_finished: int = field(default_factory=lambda: _int("CRANE_KEEP_FINISHED", 500))
    # Biggest prime-count job a client may submit, so one request can't hog a worker.
    max_n: int = field(default_factory=lambda: _int("CRANE_MAX_N", 3_000_000))
    pod_name: str = field(default_factory=lambda: os.environ.get("POD_NAME") or socket.gethostname())
    node_name: str = field(default_factory=lambda: os.environ.get("NODE_NAME", "local"))
    heartbeat_file: str = field(default_factory=lambda: os.environ.get("CRANE_HEARTBEAT_FILE", "/tmp/crane-heartbeat"))


def redis_client(settings: Settings):
    import redis

    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=10,
        health_check_interval=15,
    )
