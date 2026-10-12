"""Entry point. One image, four jobs:

python -m crane api       serve the API and dashboard
python -m crane worker    run jobs from the queue
python -m crane janitor   one pass of housekeeping (run by a CronJob)
python -m crane probe     exit 0 if this worker's heartbeat is fresh (liveness probe)
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path


def janitor() -> None:
    from .config import Settings, redis_client
    from .queue import Queue

    s = Settings()
    q = Queue(redis_client(s), s)
    reaped = q.reap()
    trimmed = q.trim()
    forgotten = q.forget_idle_workers()
    st = q.stats()
    logging.getLogger("crane.janitor").info(
        "reaped=%s trimmed=%d forgot_workers=%d | queued=%d running=%d done=%d failed=%d",
        reaped,
        trimmed,
        forgotten,
        st["queued"],
        st["running"],
        st["done"],
        st["failed"],
    )


def probe(max_age: float = 30.0) -> int:
    from .config import Settings

    beat = Path(Settings().heartbeat_file)
    try:
        age = time.time() - beat.stat().st_mtime
    except FileNotFoundError:
        print("no heartbeat yet")
        return 1
    print(f"heartbeat {age:.1f}s ago")
    return 0 if age <= max_age else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cmd = argv[0] if argv else "api"
    if cmd == "api":
        from .api import main as run_api

        run_api()
    elif cmd == "worker":
        from .worker import main as run_worker

        run_worker()
    elif cmd == "janitor":
        janitor()
    elif cmd == "probe":
        return probe()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
