"""
Usage: python restart_test.py            (needs redis-server on PATH)
"""

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import redis

PORT = 6391
LUA = (Path(__file__).parent / "limiter.lua").read_text()


def start(d: str, aof: str) -> subprocess.Popen[bytes]:
    args = ["redis-server", "--port", str(PORT), "--dir", d, "--save", ""]
    args += ["--appendonly", "yes", "--appendfsync", aof] if aof != "no" else ["--appendonly", "no"]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL)
    for _ in range(50):
        try:
            redis.Redis(port=PORT).ping()
            return p
        except redis.ConnectionError:
            time.sleep(0.1)
    raise RuntimeError("redis did not start")


def run(aof: str) -> None:
    d = tempfile.mkdtemp()
    p = start(d, aof)
    stop = threading.Event()
    acked = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal acked
        c = redis.Redis(port=PORT, decode_responses=True)
        s = c.register_script(LUA)
        while not stop.is_set():
            rid = uuid.uuid4().hex
            try:
                out = s(
                    keys=["{o}:b", "{o}:q", f"{{o}}:i:{rid}", "usage:events"],
                    args=[10**6, 10**7, 10**9, 3_000_000, 600, rid, "o", "k", "0"],
                )
            except redis.RedisError:
                return
            if out[0] == 1:
                with lock:
                    acked += 1

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    time.sleep(2.0)
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    stop.set()
    [t.join() for t in ts]
    p2 = start(d, aof)
    c = redis.Redis(port=PORT, decode_responses=True)
    counter = int(c.get("{o}:q") or 0)
    stream = c.xlen("usage:events")
    print(f"appendfsync={aof:8s} acked_ok={acked:6d} counter_after={counter:6d} "f"stream_after={stream:6d} diff(counter-acked)={counter - acked}")
    p2.terminate()
    p2.wait()
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for mode in ("no", "everysec", "always"):
        run(mode)