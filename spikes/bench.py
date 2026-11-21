"""
Usage: python bench.py [port]      (Redis must already be running on that port)
Throwaway code. It is not part of the application.
"""

import collections
import multiprocessing as mp
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path

import redis

HERE = Path(__file__).parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 6379
LUA = (HERE / "limiter.lua").read_text()


def client() -> "redis.Redis[str]":
    return redis.Redis(port=PORT, decode_responses=True)


def keys(rid: str) -> list[str]:
    return ["{org1}:bucket", "{org1}:q:202611", f"{{org1}}:idem:{rid}", "usage:events"]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


# 1. the race: naive check-then-increment vs the Lua script
def naive_race(limit: int, threads: int, per_thread: int) -> int:
    r = client()
    r.flushall()
    admitted = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal admitted
        c = client()
        for _ in range(per_thread):
            used = int(c.get("count") or 0)  # step 1: read
            if used < limit:  # step 2: decide
                c.incr("count")  # step 3: write (not atomic with 1-2)
                with lock:
                    admitted += 1

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return admitted


def lua_admitted(limit: int, threads: int, per_thread: int) -> int:
    r = client()
    r.flushall()
    admitted = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal admitted
        c = client()
        s = c.register_script(LUA)
        for _ in range(per_thread):
            rid = uuid.uuid4().hex
            out = s(keys=keys(rid), args=[1000, 10**6, limit, 3_000_000, 600, rid, "o", "k", "0"])
            if out[0] == 1:
                with lock:
                    admitted += 1

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return admitted


#2. exactness across several OS processes (simulated instances)
def proc_worker(args: tuple[int, int, int]) -> collections.Counter[str]:
    limit, threads, per_thread = args
    res: collections.Counter[str] = collections.Counter()
    lock = threading.Lock()

    def worker() -> None:
        c = client()
        s = c.register_script(LUA)
        for _ in range(per_thread):
            rid = uuid.uuid4().hex
            out = s(keys=keys(rid), args=[1000, 10**6, limit, 3_000_000, 600, rid, "o", "k", "0"])
            with lock:
                res[out[1]] += 1

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return res


def multi_process(limit: int, procs: int, threads: int, per_thread: int) -> tuple[int, int, int]:
    r = client()
    r.flushall()
    with mp.Pool(procs) as pool:
        parts = pool.map(proc_worker, [(limit, threads, per_thread)] * procs)
    total: collections.Counter[str] = collections.Counter()
    for p in parts:
        total.update(p)
    return total["ok"], int(r.get("{org1}:q:202611") or 0), r.xlen("usage:events")


# 3. latency of one call
def latency(n: int) -> dict[str, float]:
    r = client()
    r.flushall()
    s = r.register_script(LUA)
    ping: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        r.ping()
        ping.append((time.perf_counter() - t0) * 1000)
    adm: list[float] = []
    for _ in range(n):
        rid = uuid.uuid4().hex
        t0 = time.perf_counter()
        s(keys=keys(rid), args=[10**6, 10**7, 10**9, 3_000_000, 600, rid, "o", "k", "0"])
        adm.append((time.perf_counter() - t0) * 1000)
    return {
        "ping_p50": statistics.median(ping),
        "ping_p95": pct(ping, 0.95),
        "admit_p50": statistics.median(adm),
        "admit_p95": pct(adm, 0.95),
        "admit_p99": pct(adm, 0.99),
        "admit_max": max(adm),
    }


if __name__ == "__main__":
    print(f"redis port {PORT}, redis {client().info()['redis_version']}")
    print("\n[1] race: limit 100, 50 threads x 100 attempts (5000 attempts)")
    naive = [naive_race(100, 50, 100) for _ in range(5)]
    lua = [lua_admitted(100, 50, 100) for _ in range(5)]
    print(f"  naive GET/INCR admitted per run: {naive}  (limit 100)")
    print(f"  Lua script     admitted per run: {lua}  (limit 100)")

    print("\n[2] multi-process exactness: 4 processes x 10 threads x 100 requests, quota 300")
    for _ in range(3):
        ok, counter, stream = multi_process(300, 4, 10, 100)
        print(f"  admitted={ok} counter={counter} stream_len={stream}")

    print("\n[3] latency, one client, 5000 sequential calls (ms)")
    for k, v in latency(5000).items():
        print(f"  {k:10s} {v:.3f}")