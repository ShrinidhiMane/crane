import fakeredis
import pytest

from crane.config import Settings
from crane.queue import Queue


class Clock:
    """A clock the tests move by hand, so lease expiry needs no real waiting."""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def clock():
    return Clock()


def make_settings(pod: str = "worker-a", tmp_path=None, **kw) -> Settings:
    base = dict(
        redis_host="unused",
        redis_port=0,
        redis_password=None,
        lease_seconds=20,
        max_attempts=3,
        keep_finished=500,
        max_n=3_000_000,
        pod_name=pod,
        node_name="node-1",
        heartbeat_file=str(tmp_path / f"{pod}.beat") if tmp_path else "/tmp/crane-test.beat",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def make_queue(r, clock, tmp_path):
    def _make(pod: str = "worker-a", **kw) -> Queue:
        return Queue(r, make_settings(pod, tmp_path, **kw), clock=clock)

    return _make
