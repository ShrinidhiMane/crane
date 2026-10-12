import time

from fastapi.testclient import TestClient

from crane import jobs
from crane.__main__ import probe
from crane.api import create_app
from crane.worker import Worker


def test_count_primes():
    assert [jobs.count_primes(n) for n in (0, 1, 2, 10, 100, 1000)] == [0, 0, 1, 4, 25, 168]


def test_worker_runs_a_job_end_to_end(make_queue):
    q = make_queue("worker-a")
    job_id = q.submit("primes", 100)[0]
    w = Worker(q, q.s)
    w.beat()
    assert w.run_once() is True
    assert q.get(job_id)["result"] == {"primes_up_to_n": 25}
    assert w.run_once() is False  # queue empty


def test_worker_retries_a_job_that_raises(make_queue, monkeypatch):
    q = make_queue("worker-a")
    job_id = q.submit("primes", 100)[0]

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(jobs, "run", boom)
    Worker(q, q.s).run_once()
    job = q.get(job_id)
    assert job["status"] == "queued"
    assert job["error"] == "RuntimeError: disk full"


def test_stop_signal_lets_the_current_job_finish(make_queue):
    q = make_queue("worker-a")
    job_id = q.submit("primes", 100)[0]
    w = Worker(q, q.s, idle_sleep=0)
    w.stop()  # SIGTERM arrives...
    w.run_once()  # ...but a job already started still completes
    assert q.get(job_id)["status"] == "done"
    assert w.stopping


def test_liveness_probe_reads_the_heartbeat_file(make_queue, tmp_path, monkeypatch):
    beat = tmp_path / "beat"
    monkeypatch.setenv("CRANE_HEARTBEAT_FILE", str(beat))
    assert probe() == 1  # no heartbeat yet
    beat.touch()
    assert probe() == 0
    old = time.time() - 60
    import os

    os.utime(beat, (old, old))
    assert probe() == 1  # stale: the worker is stuck


def client(make_queue, pod="api-1"):
    q = make_queue(pod)
    return TestClient(create_app(queue=q, settings=q.s)), q


def test_api_submit_list_and_stats(make_queue):
    c, _ = client(make_queue)
    r = c.post("/api/jobs", json={"kind": "primes", "n": 1000, "count": 3})
    assert r.status_code == 201
    assert len(r.json()["ids"]) == 3
    assert r.headers["X-Served-By"] == "api-1"

    assert len(c.get("/api/jobs").json()["jobs"]) == 3
    st = c.get("/api/stats").json()
    assert st["queued"] == 3 and st["submitted"] == 3 and st["served_by"] == "api-1"

    one = r.json()["ids"][0]
    assert c.get(f"/api/jobs/{one}").json()["status"] == "queued"
    assert c.get("/api/jobs/nope").status_code == 404


def test_api_rejects_bad_jobs(make_queue):
    c, _ = client(make_queue)
    assert c.post("/api/jobs", json={"kind": "primes", "n": 10**9}).status_code == 422
    assert c.post("/api/jobs", json={"kind": "shell", "n": 1}).status_code == 422
    assert c.post("/api/jobs", json={"kind": "primes", "n": 10, "count": 501}).status_code == 422


def test_health_endpoints(make_queue):
    c, q = client(make_queue)
    assert c.get("/healthz").json() == {"status": "ok"}
    assert c.get("/readyz").status_code == 200

    def down():
        raise ConnectionError("redis is down")

    q.r.ping = down
    assert c.get("/readyz").status_code == 503
    assert c.get("/healthz").status_code == 200  # liveness doesn't depend on Redis


def test_dashboard_is_served(make_queue):
    c, _ = client(make_queue)
    r = c.get("/")
    assert r.status_code == 200 and "<title>Crane</title>" in r.text
