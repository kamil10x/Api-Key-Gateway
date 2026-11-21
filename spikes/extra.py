"""
Usage: python extra.py [port]
Throwaway code. It is not part of the application.
"""

import collections
import sys
import threading
import time
from pathlib import Path

import redis

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 6379
LUA = (Path(__file__).parent / "limiter.lua").read_text()


def concurrent_replay(threads: int, rounds: int) -> None:
    """Many clients send the SAME Idempotency-Key at the same instant."""
    bad = 0
    for i in range(rounds):
        r = redis.Redis(port=PORT, decode_responses=True)
        r.flushall()
        res: collections.Counter[str] = collections.Counter()
        lock = threading.Lock()
        barrier = threading.Barrier(threads)
        rid = f"same-{i}"

        def worker() -> None:
            c = redis.Redis(port=PORT, decode_responses=True)
            s = c.register_script(LUA)
            barrier.wait()
            out = s(
                keys=["{o}:b", "{o}:q", f"{{o}}:idem:{rid}", "usage:events"],
                args=[1, 100, 1000, 3_000_000, 600, rid, "o", "k", "1"],
            )
            with lock:
                res[out[1]] += 1

        ts = [threading.Thread(target=worker) for _ in range(threads)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        if res["ok"] != 1 or r.get("{o}:q") != "1" or r.xlen("usage:events") != 1:
            bad += 1
    print(f"concurrent replay: {rounds} rounds x {threads} simultaneous clients, "
          f"rounds with !=1 admission: {bad}")


def refill(rate: int, burst: int, seconds: float, threads: int) -> None:
    """Hammer a bucket for `seconds`; admitted should be about burst + rate*seconds."""
    r = redis.Redis(port=PORT, decode_responses=True)
    r.flushall()
    admitted = 0
    lock = threading.Lock()
    stop_at = time.time() + seconds

    def worker() -> None:
        nonlocal admitted
        c = redis.Redis(port=PORT, decode_responses=True)
        s = c.register_script(LUA)
        n = 0
        while time.time() < stop_at:
            out = s(
                keys=["{o}:b", "{o}:q", "{o}:idem:x", "usage:events"],
                args=[rate, burst, 10**9, 3_000_000, 600, "r", "o", "k", "0"],
            )
            n += out[0]
        with lock:
            admitted += n

    t0 = time.time()
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    elapsed = time.time() - t0
    print(f"refill: rate={rate}/s burst={burst} elapsed={elapsed:.2f}s admitted={admitted} "
          f"expected~{burst + rate * elapsed:.0f}")


if __name__ == "__main__":
    concurrent_replay(threads=20, rounds=200)
    refill(rate=100, burst=10, seconds=3, threads=8)
    refill(rate=20, burst=5, seconds=5, threads=4)