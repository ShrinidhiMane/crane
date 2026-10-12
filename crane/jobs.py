"""The work a job can do. Both kinds are made to be easy to watch on a dashboard:
`primes` burns CPU (so the autoscaler has something to react to) and `sleep`
takes a set time (so you can kill a worker in the middle of it)."""

from __future__ import annotations

import time
from collections.abc import Callable


def count_primes(n: int) -> int:
    """Count primes up to n with a plain sieve. Written as an ordinary loop on
    purpose, so a big n keeps a CPU busy for about a second."""
    if n < 2:
        return 0
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    i = 2
    while i * i <= n:
        if is_prime[i]:
            for j in range(i * i, n + 1, i):
                is_prime[j] = False
        i += 1
    return sum(is_prime)


def run(kind: str, n: int, should_stop: Callable[[], bool] = lambda: False) -> dict:
    if kind == "primes":
        return {"primes_up_to_n": count_primes(n)}
    if kind == "sleep":
        # Sleep in small steps. A shutdown still waits for the job to finish,
        # but should_stop is there if you ever want to cut it short.
        end = time.monotonic() + n
        while time.monotonic() < end and not should_stop():
            time.sleep(0.1)
        return {"slept_seconds": n}
    raise ValueError(f"unknown job kind: {kind}")
