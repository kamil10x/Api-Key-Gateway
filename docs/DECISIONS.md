# DECISIONS.md: Technical and business decisions

> Day 2 deliverable. Status date: 2026-11-20. Every decision is **proposed** until the client or mentor confirms it. "Source" says whether it follows from the brief or is our assumption. Question IDs refer to [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md); threat IDs to [THREAT_MODEL.md](THREAT_MODEL.md). Evidence from the spike is in [SPIKE_RESULTS.md](SPIKE_RESULTS.md); nothing below is claimed as *proven* until the matching test in DESIGN §20 exists and passes.

## Summary table

| ID | Decision | Selected approach | Source |
|---|---|---|---|
| D-01 | Scope of limits | Per organization | Assumption (Q-01) |
| D-02 | Monthly window | UTC calendar month | Assumption (Q-05) |
| D-03 | What counts as usage | Admitted and ending 2xx; otherwise voided | Assumption (Q-02, Q-03) |
| D-04 | Quota exhaustion | Hard cutoff, no overage, no rollover | Assumption (Q-06, Q-07) |
| D-05 | Short-term limiter | Token bucket (rate + burst) | Assumption (Q-04) |
| D-06 | Limiter state and atomicity | Redis, one Lua script for the whole decision | Follows R-08, G-1 |
| D-07 | API key format and storage | `qk_<public_id>_<secret>`, HMAC-SHA256 with a pepper, shown once | Follows G-2 |
| D-08 | Rotation | Overlap with a grace window; only `active` keys rotate | Follows R-05 |
| D-09 | Revocation | Immediate; auth state in Redis, no per-instance cache | Follows R-05 |
| D-10 | Usage pipeline | Event written inside admission, then stream, worker, Postgres | Follows G-3 |
| D-11 | Redis durability | AOF `appendfsync always`, `noeviction`, rebuild after wipe | Follows G-3 |
| D-12 | Redis unavailable | Fail closed (503) | Assumption (Q-21) |
| D-13 | Plan change | Immediate, no reset, no proration | Assumption (Q-08) |
| D-14 | Duplicate requests | De-duplicate on a load-balancer request id; ignore client idempotency keys | Follows G-3 |
| D-15 | Usage granularity and retention | Hourly rollup; raw events 14 days; rollup 13 months | Assumption (Q-16, Q-18) |
| D-16 | `GET /usage` access | Own org via API key, or admin token plus `org_id` | Assumption (Q-20) |
| D-17 | Over-limit response | HTTP 429, two error codes, `Retry-After` | Follows R-04 |
| D-18 | Admin authentication | One static token from the environment | Assumption (Q-14, Q-26) |
| D-19 | Time source | Redis `TIME` for the bucket; instance UTC clock for the month label | Our design |
| D-20 | Topology | nginx, N stateless instances, single Redis, single Postgres, separate worker | Follows R-08 |
| D-21 | Tenant data isolation | `org_id` on every tenant row, enforced in the data-access layer | Our design |
| D-22 | Identifiers | UUID primary keys; random `public_id` for keys | Our design |
| D-23 | Quote data | Static in-code list, no parameters | Assumption (Q-30) |

---

## D-01 Scope of limits
- **Selected:** limits (rate and monthly) belong to the organization and are shared by all of its keys.
- **Reason:** the plan belongs to the organization (R-01). Per-key limits would let a customer multiply capacity by minting keys.
- **Alternatives:** per key; per key with an org-wide cap on top.
- **Trade-offs:** one noisy key can starve the others in the same org. Mitigation is operational (revoke it). A per-key override is a likely mid-month change, so the limiter takes a *scope key* argument (DESIGN §22). Max 20 active keys per org (Q-13).

## D-02 Monthly window
- **Selected:** UTC calendar month. Counter key includes `YYYYMM`; there is no reset job.
- **Reason:** predictable for clients, no per-org billing anchor to store, resets by the key simply changing.
- **Alternatives:** per-org billing cycle (anniversary); rolling 30 days.
- **Trade-offs:** all organizations reset at the same instant; customers in other timezones see a "midnight UTC" reset. A rolling window needs a different data structure (sorted set or day buckets) and is listed as a likely change.

## D-03 What counts as usage
- **Selected:** a request is counted when it is **admitted** (passes the limiter) and the handler returns **2xx**. If the handler ends in anything else, a `void` reverses the counter and the usage row. 401, 403, and 429 are never admitted. `GET /usage` and admin calls consume nothing.
- **Reason:** matches "requests actually served" (G-3) while keeping the admission decision atomic with the limit check.
- **Alternatives:** count on arrival (charges failures); count after the response (leaves a crash window that undercounts and cannot be made atomic with the limit).
- **Trade-offs:** a crash between admit and response counts a request that may not have been served (overcount, bounded by in-flight requests). Never undercounts. The void adds one extra Redis call on the failure path only.

## D-04 Quota exhaustion
- **Selected:** hard cutoff at quota; no overage; unused quota does not roll over.
- **Reason:** simplest behavior that satisfies "going over returns a clear response". Billing is out of scope.
- **Alternatives:** soft limit with overage tracking; rollover.
- **Trade-offs:** no graceful degradation for customers near the limit. Adding overage later is a plan column plus a flag in the script.

## D-05 Short-term limiter algorithm
- **Selected:** token bucket: `rate_per_sec` refill, `burst` capacity, one token per request.
- **Reason:** allows legitimate bursts, O(1) state per org, gives an exact wait time for `Retry-After`.
- **Alternatives:** fixed window (up to 2x burst at window edges); sliding window log (memory grows with request rate); sliding window counter (approximate).
- **Trade-offs:** two parameters per plan to explain to customers. Float arithmetic in Lua; the spike measured refill accuracy (SPIKE_RESULTS §4.4).

## D-06 Limiter state and atomicity
- **Selected:** all limit state in Redis; the whole decision (replay check, quota check, bucket refill and take, counter increment, event append) runs as **one Lua script**.
- **Reason:** several instances (R-08) need shared state, and a read-then-write sequence loses updates. The spike reproduced this: a naive GET/INCR admitted 106 to 125 requests against a limit of 100 while the script admitted exactly 100 in every run.
- **Alternatives:** Redis `INCR` + `EXPIRE` (fixed window only); `WATCH/MULTI` optimistic transactions (retries under contention); Postgres counters (hot row, too slow); per-instance in-memory limits (wrong with N instances); distributed lock (slower, failure-prone).
- **Trade-offs:** Lua runs on Redis's single thread, so script cost bounds throughput; the script must stay small. Redis becomes a single point of failure (D-12, D-20). Cluster would need all keys of one org in one slot (hash tag `{org:<id>}`).

## D-07 API key format and storage
- **Selected:** key = `qk_<public_id>_<secret>`; `secret = secrets.token_urlsafe(32)` (256 bits); store `HMAC-SHA256(pepper, secret)`; compare with `hmac.compare_digest`; the pepper is held outside the database; `api_keys.hash_version` records which pepper version produced each hash so the pepper can be rotated (T-12); the full key is shown once.
- **Reason:** G-2. A 256-bit random secret cannot be guessed, so a slow hash (bcrypt, argon2) adds cost without security and would consume the latency budget. The pepper means a database dump alone cannot be used to verify guesses.
- **Alternatives:** bcrypt/argon2 (tens of ms per request); plain SHA-256 without a pepper (safe for high-entropy secrets but no extra layer); storing keys encrypted (reversible, so a dump plus the encryption key reveals keys); JWT as API keys (hard to revoke).
- **Trade-offs:** losing the pepper invalidates all keys (rotation procedure documented in LIMITATIONS later). `public_id` is stored in clear to find the row; it is not a secret.

## D-08 Rotation
- **Selected:** rotation creates a new `active` key and moves the old key to `rotating_out` with `grace_until = now + grace` (default 24 h, configurable, max 7 days). Both work during the window. Only `active` keys can be rotated (otherwise 409). The transition runs in one transaction with a row lock.
- **Reason:** R-05, customers need time to deploy the new key.
- **Alternatives:** in-place secret swap (breaks running clients); two permanent key slots per organization.
- **Trade-offs:** during the window a leaked old key stays usable unless revoked explicitly. Row lock prevents two concurrent rotations of one key from creating two replacements.

## D-09 Revocation
- **Selected:** revoke writes Postgres, then the Redis auth entry in the same request; if the Redis write fails the request returns 500 and the operator retries (the operation is idempotent). There is no per-instance cache, so the next request on any instance sees it. Target <= 1 s. Redis holds **exactly the keys that may authenticate** (`active` and `rotating_out`); a revoked key is removed, and a Redis miss means 401 with no Postgres lookup (so an invalid-key flood cannot reach the database, T-13).
- **Reason:** a stolen key must die quickly (T-02) and the delay must be bounded and testable.
- **Alternatives:** short-TTL in-process cache (delay = TTL on every instance); pub/sub invalidation (lost messages leave stale caches); checking Postgres per request (latency).
- **Trade-offs:** every request reads the key entry from Redis (one extra round trip, candidate for the performance work). Redis must always be fully populated, which is why a wipe forces a rebuild before serving (D-11).

## D-10 Usage pipeline
- **Selected:** the `admit` script appends a usage event to a Redis Stream **atomically** with the counter increment. A worker reads the stream through a consumer group, inserts into Postgres with `ON CONFLICT (stream_id) DO NOTHING`, updates the hourly rollup only for rows actually inserted, then acknowledges. Voids are events that reference the admit event.
- **Reason:** G-3. There is no window between "limit consumed" and "event recorded". At-least-once delivery plus an idempotent write gives an exactly-once effect in Postgres.
- **Alternatives:** write to Postgres synchronously per request (latency, DB in the hot path); emit the event after serving (crash undercounts); batch counters flushed periodically (loses data on crash).
- **Trade-offs:** reports from the series lag by the worker lag. The guarantee is "never undercount; overcount at most in-flight requests", not literal equality (Q-19).

## D-11 Redis durability
- **Selected:** `appendonly yes`, `appendfsync always`, `maxmemory-policy noeviction`. At startup a missing `meta:epoch` key means Redis lost its data; the service reports not-ready, rebuilds each current-month counter from `SUM(usage_hourly)`, reloads **all** usable keys and plan data from Postgres, sets the epoch, then serves (a single instance does the rebuild under a lock).
- **Reason:** a Redis restart must not lose acknowledged admissions (G-3). `noeviction` because eviction would silently delete counters and keys.
- **Alternatives:** `everysec` (up to about one second of acknowledged writes lost on power failure); RDB snapshots only; Redis as non-durable cache with Postgres rebuild only.
- **Trade-offs:** the spike measured admit p50 0.056 ms without AOF and 0.229 ms with `always` on the sandbox disk (SPIKE_RESULTS §4.3), so a real cost on that machine. On the student's machine the same call had p50 0.888 ms and p95 2.603 ms with `always` (0.135 / 0.291 without), about a quarter of the 10 ms budget for the Redis call alone, so the cost depends heavily on the disk and **must be re-measured under k6**; `everysec` is the fallback if it threatens G-4 (risk: up to about a second of acknowledged writes lost on power or VM loss). With `noeviction`, a full Redis rejects writes and the service fails closed.

## D-12 Redis unavailable
- **Selected:** fail closed. `GET /v1/quote` returns 503 with `Retry-After`. Admin endpoints keep working for Postgres-only operations but cannot update Redis, so key and plan changes that need write-through return 500.
- **Reason:** without Redis neither the limit nor the usage record can be trusted. Serving unmetered traffic breaks G-1 and G-3.
- **Alternatives:** fail open; fail open with a conservative local limit per instance.
- **Trade-offs:** Redis outage means total outage of the protected endpoint. Accepted and documented (Q-21, Q-22).

## D-13 Plan change
- **Selected:** immediate; no reset and no proration of current-month usage. A downgrade below current usage leaves the org over quota until the next month. Write-through to Redis and an audit entry.
- **Reason:** least surprising and simplest; every request reads current plan data.
- **Alternatives:** apply at next period; prorate quota; refuse a downgrade that leaves the org over quota.
- **Trade-offs:** a downgrade can cut a customer off instantly. The operator is told in the response (current usage vs new quota).

## D-14 Duplicate requests
- **Selected:** the load balancer assigns a unique `X-Request-ID` to every request (and overwrites any client-sent value). The `admit` script stores a short-lived marker (TTL 60 s) per request id; a second admission with the same id is a **replay**: allowed, nothing consumed, no event. If the first attempt was voided, the marker is `void` and the retry is treated as fresh. Client-supplied `Idempotency-Key` headers are **not** used.
- **Reason:** nginx retries a failed upstream attempt on another instance, which can execute one client request twice. A trusted LB-assigned id identifies exactly that case.
- **Alternatives:** client `Idempotency-Key` (a client could replay one key forever and use the API without consuming quota; this was in an earlier draft and was rejected in self-review); no de-duplication (infra retries double count); deduplicate in the worker only (does not fix the counter).
- **Trade-offs:** relies on the instances being reachable only through the LB (Q-28). Client retries that are served are separate requests and count. About 200 rps x 60 s = 12,000 marker keys, negligible memory.

## D-15 Usage granularity and retention
- **Selected:** usage is stored per organization per UTC hour in `usage_hourly`. `GET /usage` serves `hour` (default) or `day` (summed). Range capped at 366 days. Raw `usage_events` kept 14 days; `usage_hourly` kept 13 months.
- **Reason:** answers "usage over time" with bounded cost. Raw events are only needed to match voids, audit, and debugging, so they can be short-lived. Rebuilding counters uses the rollup, not the raw table.
- **Alternatives:** per-minute buckets (60x rows); raw events only (reporting scans a huge table); daily only (cannot serve hourly views).
- **Trade-offs:** no per-request history beyond 14 days. Day boundaries are UTC.

## D-16 `GET /usage` access
- **Selected:** with an API key, the response covers that key's organization; with the admin token plus `org_id`, any organization. `used` and `remaining` come from the Redis counter; the series comes from Postgres and carries an `as_of` timestamp.
- **Reason:** one fixed endpoint, two audiences, no way for a customer to name another organization.
- **Alternatives:** customer portal with separate auth; separate admin usage endpoint.
- **Trade-offs:** `used` can be briefly ahead of the series.

## D-17 Over-limit response
- **Selected:** HTTP 429 for both limits. Body has `error` (`rate_limited` or `quota_exceeded`), `message`, `retry_after_seconds`, `limit`, `remaining`, `reset_at`; header `Retry-After`. For `rate_limited` the wait is time to the next token (at least 1 s, rounded up). For `quota_exceeded` it is seconds until 00:00 UTC on the first of next month.
- **Reason:** R-04; one status clients already understand, distinct codes for logic.
- **Alternatives:** 402/403 for quota; 503 for rate limit.
- **Trade-offs:** a very long `Retry-After` is unusual; the body explains it.

## D-18 Admin authentication
- **Selected:** one static admin token from the environment, compared in constant time; every admin action is written to `audit_log` with actor "admin".
- **Reason:** enough for the project and the stated scope.
- **Alternatives:** named operator accounts with passwords or SSO; mTLS.
- **Trade-offs:** no per-operator accountability; leak of the token is full control (T-11, limitations).

## D-19 Time source
- **Selected:** token-bucket timing uses Redis `TIME`. The month label in the counter key comes from the instance's UTC clock.
- **Reason:** Redis Lua cannot format dates; instance skew must not affect bucket math.
- **Alternatives:** compute the month label inside Lua with integer calendar arithmetic (keys built inside a script, awkward for Cluster); fetch Redis time first (extra round trip).
- **Trade-offs:** requests within the NTP skew (milliseconds) of 00:00 UTC on the first can land in the adjacent month counter. Accepted, listed as residual risk.

## D-20 Topology
- **Selected:** nginx load balancer, N stateless FastAPI instances, one Redis, one Postgres, one worker process (more can run; consumer groups allow it).
- **Reason:** the simplest topology that satisfies R-08 and G-1; every instance is interchangeable.
- **Alternatives:** Redis Cluster/Sentinel; per-region deployments; sidecar limiter.
- **Trade-offs:** Redis and Postgres are single points of failure (limitations).

## D-21 Tenant data isolation
- **Selected:** `org_id` on every tenant-owned row; all queries go through data-access functions that require an `org_id`; `GET /usage` derives the organization from the authenticated key, never from a customer-supplied parameter. A test checks that one organization cannot read another's usage.
- **Reason:** simple, testable, no per-request database session state.
- **Alternatives:** Postgres row-level security (needs per-request `SET` on pooled connections, harder to test and slower); schema per tenant (migration overhead).
- **Trade-offs:** isolation depends on code discipline, backed by tests and review.

## D-22 Identifiers
- **Selected:** UUID primary keys for organizations, plans, and keys; keys also have a random `public_id` embedded in the key string; usage events use the Redis stream id as primary key.
- **Reason:** non-guessable ids in URLs; the stream id gives free idempotency in the worker.
- **Alternatives:** serial integers (guessable, easy to enumerate); ULIDs.
- **Trade-offs:** UUIDs are larger indexes. Insignificant at this scale.

## D-23 Quote data
- **Selected:** a small static list of dummy quotes in code; `GET /v1/quote` takes no parameters and returns one quote.
- **Reason:** the brief says any dummy data; the project is about access control.
- **Alternatives:** a quotes table; an external dataset.
- **Trade-offs:** none relevant to the guarantees.