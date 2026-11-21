# SPIKE_RESULTS.md: Redis Lua rate limiter proof of concept

> Day 3 deliverable. Throwaway experiment: **nothing here is part of the application** (code is in `spike/`, excluded from lint). Decision IDs refer to [DECISIONS.md](DECISIONS.md).
>
> **Provenance, please read.** The numbers below come from a **reference run executed by the assistant (Claude) in an isolated sandbox on 2026-11-20**, not on the student's machine. Absolute timings will differ elsewhere; the exactness results do not depend on machine speed and should reproduce exactly. The student's own run, done on 2026-11-21 on different hardware and a different Redis version, is recorded in section 6; the exact-count results match, while latencies differ (notably with AOF `always`).

## 1. Question the spike answers

Can one atomic Redis Lua script make the short-term limit, the monthly quota, duplicate-request handling, and the usage event a single indivisible step, so that limits stay **exact** with many concurrent clients (G-1) and the usage record cannot disagree with the counter (G-3)? And is the cost small enough for the 10 ms budget (G-4)?

## 2. What was built

| File | Purpose |
|---|---|
| `spike/limiter.lua` | `admit`: replay check, quota check, token-bucket refill and take, counter `INCR`, request marker, `XADD` usage event. Returns `{allowed, reason, remaining, retry_after_ms, stream_id}` |
| `spike/void.lua` | `void`: reverses one admission (counter `DECR`, marker to `void`, `void` event), guarded so it applies once |
| `spike/spike.py` | exactness scenarios with threads (burst cap, quota cap, quota before burst, replay, void and retry) |
| `spike/bench.py` | race demonstration (naive vs Lua), multi-process exactness, latency |
| `spike/extra.py` | concurrent replay, token refill accuracy |
| `spike/restart_test.py` | what survives `kill -9` of Redis under three persistence settings |

Design of the script: it reads `TIME` from Redis (not the client clock), checks the monthly counter, refills the bucket from elapsed time, and only if **both** limits pass does it take a token, increment the counter, set the marker, and append the event. A rejection changes nothing. Redis runs a script to completion before any other command, which is what makes the sequence atomic.

## 3. Environment (reference run)

| Item | Value |
|---|---|
| Machine | sandbox container, **1 vCPU**, Linux 6.18 |
| Redis | 7.0.15, local, loopback (no network) |
| Python | 3.12.3 (the project targets 3.11; the spike does not depend on the version) |
| Client | redis-py, threads and `multiprocessing` on the **same** machine as Redis |
| Redis configs | A: `appendonly no`. B: `appendonly yes`, `appendfsync always`, `maxmemory-policy noeviction` |

Because client and server share one core, client-side Python overhead is included in every latency number.

## 4. Methodology and results

### 4.1 Exactness with concurrent threads (`spike.py`)
Each scenario: 50 threads x 100 requests = 5,000 attempts, fresh database.

| Scenario | Setup | Expected | Observed | Counter = stream length? |
|---|---|---|---|---|
| Burst cap | burst 100, near-zero refill, big quota | 100 admitted | **100** | yes (100) |
| Quota cap | big burst, quota 300 | 300 admitted | **300** | yes (300) |
| Quota before burst | burst 500, quota 250 | 250 admitted | **250** | yes (250) |
| Replay (sequential) | same request id x 50 | 1 admitted, 49 replays | **1 / 49** | yes (1) |
| Void and retry | admit, void twice, retry same id | counter back to 0 once; retry admitted fresh | **as expected** | n/a |

### 4.2 The race, and exactness across processes (`bench.py`)
**Race demonstration.** A naive limiter does `GET count`, compare, then `INCR` in three separate calls. Limit 100, 50 threads x 100 attempts, 5 runs each:

| Implementation | Redis config A (no AOF) | Redis config B (AOF always) |
|---|---|---|
| Naive GET/INCR | 106, 114, 111, 111, 107 admitted | 125, 124, 122, 125, 122 admitted |
| Lua script | 100, 100, 100, 100, 100 | 100, 100, 100, 100, 100 |

So the naive approach exceeded the limit by 6 to 25 percent in every run, and the overshoot was larger with the slower AOF configuration (a slower write widens the window between read and write). The script never exceeded the limit.

**Several processes (simulated instances).** 4 OS processes x 10 threads x 100 requests, quota 300, three runs per configuration: **300 admitted, counter 300, stream length 300** in all six runs.

### 4.3 Execution time and the cost of durability (`bench.py`)
5,000 sequential calls from one client, milliseconds, loopback:

| Measure | Config A (no AOF) | Config B (AOF always) |
|---|---|---|
| `PING` p50 / p95 | 0.021 / 0.036 | 0.021 / 0.035 |
| `admit` p50 | 0.056 | 0.229 |
| `admit` p95 | 0.086 | 0.510 |
| `admit` p99 | 0.099 | 0.636 |
| `admit` max | 0.804 | 7.291 |

Reading: the script itself costs tens of microseconds. `appendfsync always` adds a visible cost (p50 about 4x) because every write waits for the sandbox disk. Both are far below 10 ms **for this one call on loopback**. This is not the G-4 measurement: G-4 includes HTTP, auth lookups, and the network, and will be measured with k6 against the real application (DESIGN §21). The disk here is not representative of production.

### 4.4 Token refill accuracy (`extra.py`)
Hammer a bucket for a fixed time from several threads; admitted should be about `burst + rate x elapsed`.

| Rate | Burst | Elapsed | Expected | Observed |
|---|---|---|---|---|
| 100/s | 10 | 3.00 s | ~310 | **309** |
| 20/s | 5 | 5.00 s | ~105 | **104** |

### 4.5 Concurrent duplicate requests (`extra.py`)
200 rounds; in each, 20 clients released simultaneously by a barrier send the **same** request id. Required: exactly 1 admission, counter 1, stream length 1. **Rounds that violated this: 0 of 200.**

### 4.6 What survives `kill -9` of Redis (`restart_test.py`)
8 threads admit for 2 seconds, the server is killed with SIGKILL, restarted from its directory, and the counter is compared with what clients were told (`acked_ok`). Three runs:

| appendfsync | acked (runs 1 / 2 / 3) | counter after restart | counter - acked |
|---|---|---|---|
| `no` (AOF off) | 33,852 / 27,654 / 26,888 | 0 / 0 / 0 | everything lost |
| `everysec` | 30,095 / 25,962 / 19,831 | equal | 0 / 0 / 0 |
| `always` | 15,401 / 15,312 / 14,547 | 15,401 / 15,316 / 14,554 | 0 / +4 / +7 |

Reading: with persistence off, a Redis crash erases all usage and counters, which is why D-11 requires AOF. With `always`, the restarted counter was **never below** the acknowledged count; it exceeded it by 4 and 7 in two runs, always at most the 8 requests that were in flight (applied and persisted, reply lost). That matches the design statement "never undercount, overcount at most the in-flight requests". The stream length equalled the counter in every run, so counter and event stayed together.

**Important limit of this test:** `kill -9` kills the Redis *process*; the operating system still holds the file data in its page cache, so `everysec` also showed no loss. This experiment therefore **cannot distinguish `everysec` from `always`** and says nothing about power loss. Fsync durability is Redis's documented behavior and was not independently verified here.

## 5. Conclusions and limitations

**What the evidence supports**
1. A read-then-write limiter is racy: it exceeded the limit in all 10 naive runs. (D-06)
2. The single-script limiter admitted exactly the allowed number in every concurrency scenario, including across processes, and the usage event count always equalled the counter. (D-06, D-10)
3. Rejections consumed nothing, the quota check precedes the bucket, and refill matched the expected rate. (D-05)
4. Concurrent duplicates of one request id produced exactly one admission in 200 rounds. (D-14)
5. Without AOF a crash loses everything; with `appendfsync always` nothing acknowledged was lost on a process kill. (D-11)
6. Script cost is small. AOF `always` raises it several-fold: sub-millisecond on the reference machine, but p95 2.6 ms and p99 5.0 ms on the student's machine (section 6). The durability setting is therefore a real latency cost to re-measure under k6. (D-11)

**What the spike does NOT prove**
- G-1 through the real HTTP path, with a load balancer and real instances (planned test `test_multi_instance_limits`).
- G-3 end to end: the worker, Postgres, and the void path under concurrency were not exercised (void was tested for logic, sequentially).
- G-4: only one Redis call on loopback was timed.
- Behavior of Redis under power loss, Redis Cluster, replicas, or clock steps.
- Month-boundary behavior (the month label comes from the application, not the script).
- That 5,000-request bursts represent production traffic; the tests are stress shapes, not a workload model.

**Observed limitations of the approach**
- The script runs on Redis's single thread, so a slow script blocks all other Redis commands; it must stay small. Per-call cost here was tens of microseconds.
- All keys touched by one script must live on one node; in a Cluster they would need a shared hash tag. The usage stream is a separate key, so Cluster is out of scope (D-20).
- The bucket stores a float token count; refill matched expectations within 1 token over 3 to 5 seconds, but long-run drift was not measured.

## 6. Student run (completed 2026-11-21)

How to reproduce.

**Option 1 (recommended, Windows with Docker Desktop):** one command runs everything inside a Linux container, including the `kill -9` test. From the project folder in PowerShell:

```powershell
docker run --rm -v "${PWD}\spike:/spike" -w /spike python:3.11-slim bash -c "tr -d '\r' < run_all.sh | bash" | Tee-Object spike_output.txt
```

It installs Redis and the Python client inside the throwaway container (needs internet, takes a few minutes the first time), starts two Redis servers (config A without AOF, config B with AOF `always`), and runs all scripts. The output is also saved to `spike_output.txt`. Timings come from the Docker Desktop Linux VM, not from Windows itself; note the CPU count it prints.

**Option 1b (Windows, no extra downloads: Redis in Docker, scripts on Windows Python).** Windows has no Redis server, so start two Redis containers with published ports, then run the scripts from the `spike` folder (the folder may be named `spikes` on your machine). This covers 4.1 to 4.5; the `kill -9` test (4.6) needs Linux, so use Option 1 for it.

```powershell
docker run -d --name redis-a -p 6379:6379 redis:7 redis-server --appendonly no
docker run -d --name redis-b -p 6380:6379 redis:7 redis-server --appendonly yes --appendfsync always --maxmemory-policy noeviction
cd spike
pip install redis
python spike.py
python bench.py 6379
python bench.py 6380
python extra.py 6379
docker rm -f redis-a redis-b
```

In this mode the client runs on Windows and reaches Redis through Docker's port forwarding, so latencies include that hop and will be higher than in the reference run; write "Windows host to Docker port forward" in the Machine row. The exactness results are unaffected. (`bench.py` was also checked with Windows-style `spawn` multiprocessing in the authoring sandbox.)

**Option 2 (Linux or WSL with Redis 7 installed):**

```bash
redis-server --port 6379 --daemonize yes --save "" --appendonly no
redis-server --port 6380 --daemonize yes --save "" --appendonly yes --appendfsync always --maxmemory-policy noeviction
pip install redis
cd spike
python spike.py                 # 4.1 (uses port 6379)
python bench.py 6379            # 4.2, 4.3 config A
python bench.py 6380            # 4.2, 4.3 config B
python extra.py 6379            # 4.4, 4.5
python restart_test.py          # 4.6 (starts its own Redis on port 6391)
```

Expect `ALL PASS` from `spike.py`, exactly 100 / 300 admitted in the Lua rows, and `0 of 200` replay violations; those do not depend on machine speed. Latencies and the naive-limiter overshoot will differ from the reference run, which is normal (the overshoot is always above 100 but varies run to run).

| Item | Reference run | Your run (2026-11-21) |
|---|---|---|
| Machine / environment | 1 vCPU sandbox, Redis 7.0.15, Python 3.12.3 | Docker Desktop Linux VM on a Windows laptop, **18 CPUs visible**, Python 3.11.18, **Redis 8.0.2** (Debian package inside the container), Option 1 |
| `spike.py` (4.1) | ALL PASS | **ALL PASS** (100 / 300 / 250 admitted, replay 1 and 49, void-and-retry ok) |
| Naive GET/INCR admitted (limit 100), 5 runs | 106 to 125 | Config A: 115, 112, 116, 117, 113. Config B: 115, 120, 107, 113, 124 |
| Lua admitted (limit 100) | 100 every run | **100 in all 10 runs** |
| Multi-process admitted (quota 300), 3 runs each | 300 every run | **300 in all 6 runs**; counter and stream length 300 each time |
| `admit` p50 / p95, config A (ms) | 0.056 / 0.086 | 0.135 / 0.291 (p99 0.858, max 13.2) |
| `admit` p50 / p95, config B (ms) | 0.229 / 0.510 | **0.888 / 2.603** (p99 5.026, max 37.2) |
| Refill accuracy (4.4) | 309 vs ~310; 104 vs ~105 | 308 vs ~310; 104 vs ~105 |
| Concurrent replay violations (4.5) | 0 of 200 | **0 of 200** |
| `kill -9` (4.6), one run | `always`: counter - acked 0 to +7 over 3 runs | `no`: 7,742 acknowledged, counter 0 (all lost). `everysec`: difference 0. `always`: acknowledged 6,464, counter 6,467, **difference +3** (at most 8 in flight) |

**What your run shows.**
1. **Exactness reproduced on different hardware and a different Redis version.** Every exact-count result matched the reference run: the Lua script admitted exactly the allowed number in all scenarios, including across processes, while the naive limiter exceeded the limit in all 10 runs. These results do not depend on speed, which is why they transfer.
2. **The durability setting has a visible latency cost on your machine.** With AOF `always` one `admit` call had p50 0.888 ms, p95 2.603 ms, p99 5.026 ms and a worst case of 37 ms, against 0.135 / 0.291 / 0.858 / 13.2 ms without AOF (about 7x at p50 and 9x at p95). The reference sandbox showed only about 4x and stayed sub-millisecond, so **the earlier claim that this cost is negligible does not hold in general**: it depends on the disk. A single call at p95 2.6 ms already uses about a quarter of the 10 ms p95 budget before any HTTP, authentication, or Postgres work. The test used one sequential client; at 200 rps requests arrive about 5 ms apart, so there is little chance for concurrent writes to share one fsync and each request will mostly pay its own.
3. **This is the leading candidate for the G-4 bottleneck**, but that is a hypothesis until measured through the real application with k6 (DESIGN section 21). If it is confirmed, the fallback is `appendfsync everysec` (D-11): it risks losing up to about a second of acknowledged writes on power or VM loss, while the `kill -9` test here shows no loss for a process crash under either setting (it cannot distinguish them, see 4.6). That trade-off should be decided with the mentor, not silently.
4. **Version mismatch to keep in mind.** The spike ran on Redis 8.0.2, but `docker-compose.yml` runs `redis:7` (7.4.12 in your log). The scripts use only long-standing commands, so exactness should not differ, but latency may; repeat the timing against the Compose Redis before drawing conclusions.
5. **Limits of this run.** The `kill -9` test ran once (the reference ran three times). Timing numbers come from a container inside Docker Desktop's VM with 18 visible CPUs, so they are not a measurement of the final deployment.

You should be able to explain, without notes: why the naive version overshoots (three steps with a gap between them), why a Lua script closes the gap (Redis runs it as one uninterruptible command), and why this experiment cannot prove fsync durability.