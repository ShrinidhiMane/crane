import pytest

from crane.queue import QUEUE, InvalidJob


def test_jobs_run_oldest_first(make_queue):
    q = make_queue()
    first = q.submit("primes", 10)[0]
    second = q.submit("primes", 20)[0]
    assert q.claim().id == first
    assert q.claim().id == second
    assert q.claim() is None


def test_complete_records_result_and_counts_it_for_the_worker(make_queue, clock):
    q = make_queue("worker-a")
    job_id = q.submit("primes", 10)[0]
    q.claim()
    clock.advance(1.5)
    assert q.complete(job_id, {"primes_up_to_n": 4})

    job = q.get(job_id)
    assert job["status"] == "done"
    assert job["result"] == {"primes_up_to_n": 4}
    assert job["worker"] == "worker-a"
    assert job["duration_ms"] == 1500
    st = q.stats()
    assert (st["done"], st["running"], st["queued"]) == (1, 0, 0)
    assert st["done_by_worker"] == {"worker-a": 1}


def test_two_workers_never_get_the_same_job(make_queue):
    a, b = make_queue("worker-a"), make_queue("worker-b")
    ids = set(a.submit("primes", 10, count=50))
    got_a, got_b = [], []
    while True:
        ja, jb = a.claim(), b.claim()
        if ja:
            got_a.append(ja.id)
        if jb:
            got_b.append(jb.id)
        if not ja and not jb:
            break
    assert set(got_a).isdisjoint(got_b)
    assert set(got_a) | set(got_b) == ids


def test_lost_worker_job_is_retried_by_another_worker(make_queue, clock):
    dead, alive = make_queue("worker-dead"), make_queue("worker-alive")
    job_id = dead.submit("sleep", 10)[0]
    dead.claim()  # ...and then the pod is killed, so it never completes

    clock.advance(19)
    assert alive.reap() == {"retried": 0, "failed": 0}  # lease still valid
    clock.advance(2)
    assert alive.reap() == {"retried": 1, "failed": 0}

    job = alive.get(job_id)
    assert job["status"] == "queued"
    assert "lease expired" in job["error"]

    retry = alive.claim()
    assert retry.id == job_id and retry.attempts == 2
    assert alive.complete(job_id, {"slept_seconds": 10})
    assert alive.get(job_id)["worker"] == "worker-alive"
    assert alive.stats()["retried"] == 1


def test_late_result_from_a_worker_that_lost_its_lease_is_ignored(make_queue, clock):
    slow, fast = make_queue("worker-slow"), make_queue("worker-fast")
    job_id = slow.submit("primes", 10)[0]
    slow.claim()
    clock.advance(25)
    fast.reap()
    fast.claim()
    # The slow worker wakes up and tries to report: too late, the job isn't its anymore.
    assert slow.complete(job_id, {"primes_up_to_n": 4}) is False
    assert fast.complete(job_id, {"primes_up_to_n": 4}) is True
    assert fast.stats()["done_by_worker"] == {"worker-fast": 1}


def test_job_fails_after_max_attempts(make_queue, clock):
    q = make_queue(max_attempts=3)
    job_id = q.submit("sleep", 5)[0]
    for _ in range(3):
        assert q.claim().id == job_id
        clock.advance(21)
        q.reap()
    job = q.get(job_id)
    assert job["status"] == "failed"
    assert job["attempts"] == 3
    assert q.stats()["failed"] == 1
    assert q.r.llen(QUEUE) == 0


def test_job_that_raises_is_retried_then_failed(make_queue):
    q = make_queue(max_attempts=2)
    job_id = q.submit("primes", 10)[0]
    q.claim()
    assert q.give_up_on(job_id, "boom") == "retried"
    q.claim()
    assert q.give_up_on(job_id, "boom") == "failed"
    assert q.get(job_id)["error"] == "boom"


def test_trim_keeps_only_the_newest_finished_jobs(make_queue, clock):
    q = make_queue(keep_finished=3)
    ids = []
    for _ in range(5):
        ids += q.submit("primes", 10)
        clock.advance(1)
    for job_id in ids:
        q.claim()
        clock.advance(1)
        q.complete(job_id, {})
    assert q.trim() == 2
    assert q.get(ids[0]) is None and q.get(ids[1]) is None
    assert [j["id"] for j in q.recent()] == list(reversed(ids[2:]))


@pytest.mark.parametrize("kind,n", [("primes", 0), ("primes", 3_000_001), ("sleep", 16), ("rm -rf", 1)])
def test_bad_jobs_are_rejected(make_queue, kind, n):
    with pytest.raises(InvalidJob):
        make_queue().submit(kind, n)


def test_only_recent_heartbeats_count_as_active_workers(make_queue, clock):
    a, b = make_queue("worker-a"), make_queue("worker-b")
    a.heartbeat()
    clock.advance(20)
    b.heartbeat()
    assert [w["pod"] for w in b.stats()["active_workers"]] == ["worker-b"]
    clock.advance(700)
    assert b.forget_idle_workers() == 2
