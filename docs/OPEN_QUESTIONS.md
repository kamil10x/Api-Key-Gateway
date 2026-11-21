# OPEN_QUESTIONS.md: Ambiguities, contradictions, and questions for the client

> Day 1 deliverable. Status date: 2026-11-20. Part of the Week 1 package; see [DESIGN.md](DESIGN.md).
>
> **How to read this file.** The brief is intentionally incomplete. For every silence or contradiction we (1) ask the question, (2) apply a **default assumption** so work can continue, and (3) link the default to a decision in [DECISIONS.md](DECISIONS.md). An assumption is *ours*, not the client's; it must be confirmed or changed when an answer arrives. All questions are currently **Open**.

---

## 1. What the brief actually states (client requirements)

Everything not in this table is an assumption of ours.

| ID | Requirement (from the brief) |
|---|---|
| R-01 | Operators create organizations and API keys; each organization is on a plan |
| R-02 | Developers call the API with a key |
| R-03 | Plans enforce a short-term rate limit **and** a monthly allowance |
| R-04 | Going over returns a clear response telling the client when to retry |
| R-05 | Operators rotate and revoke keys; rotation must not break a customer's running systems |
| R-06 | Operators **and** customers see usage over time and what is left this month |
| R-07 | Operators change a plan |
| R-08 | The service runs as several instances behind a load balancer |
| G-1 | Limits are correct with several instances running |
| G-2 | A stolen database dump reveals no usable keys |
| G-3 | Reported usage matches requests actually served, even across restarts and retries |
| G-4 | Limiter overhead stays under 10 ms p95 at 200 rps; show measurements, fix one real bottleneck, re-measure |
| C-1 | Fixed contract: `GET /v1/quote` (protected), an admin endpoint to create a key, `GET /usage` |
| S-1 | Stack: Python 3.11, FastAPI, SQLAlchemy 2 + Alembic, Postgres 15, Redis, pytest, ruff and mypy clean (no inline suppressions), `docker compose up` |

---

## 2. Contradictions and tensions inside the brief

| ID | Tension | Why it is a problem | Our resolution (default) | Decision |
|---|---|---|---|---|
| C-01 | R-04 asks for "when to retry" for **both** a short-term limit (seconds) and a monthly allowance (up to ~31 days) | One status/`Retry-After` convention must serve very different waits, and clients need to tell them apart | Both are HTTP 429 with a distinct `error` code (`rate_limited` / `quota_exceeded`); `Retry-After` is seconds to the next token, or to the next UTC month | D-17 |
| C-02 | G-3: usage "matches requests **actually served**" but also "across **retries**" | If a client retries and both are served, is that one request or two? Counting server-side truth (every served response) conflicts with de-duplicating retries | A client retry that is served is a **separate served request** and counts. Only **infrastructure** duplicates (the load balancer re-sending the same request to another instance; stream redelivery to the worker) are de-duplicated, using a request id assigned by the load balancer. Client-supplied idempotency keys are **not** trusted (a client could replay one key forever to bypass the quota) | D-10, D-14 |
| C-03 | R-05 "rotation must not break running systems" requires two keys valid at once; G-2/stolen-key thinking requires keys to die instantly | Overlap is a security window | Rotation grace window (default 24 h) for the **old** key; explicit revoke ignores the grace window and is immediate | D-08, D-09 |
| C-04 | R-06 says operators **and** customers see usage, yet the contract has one `GET /usage` | Two audiences, two scopes, one endpoint | Customer calls with an API key and sees only its organization; operator calls with the admin token plus `?org_id=` | D-16 |
| C-05 | G-4 says "limiter overhead under 10 ms p95" without defining overhead | Could mean total latency, or protected route minus an unprotected baseline | Overhead = p95 latency of `GET /v1/quote` minus p95 of an unprotected baseline route under identical load (200 rps constant) | DESIGN §21 |
| C-06 | R-08 and G-1 require shared limit state; nothing says whether that shared state may be a single point of failure | A single Redis makes limits exact but is one failure away from an outage | Single Redis node, fail **closed** when it is unreachable; documented as a limitation | D-12, D-20 |
| C-07 | R-04 says "clear response when to retry" but a monthly 429 may say "retry in 11 days" | A 429 normally implies "slow down", not "buy a bigger plan" | Keep 429 for both (clients already handle it) but give `quota_exceeded` its own code, message, and reset timestamp | D-17 |
| C-08 | "Expect one requirement change mid-month" | Unknown change; over-fitting the design to today's reading is a risk | Limits are data (plan rows), the limiter is generic over a scope key and window, usage is stored hourly so windows can change | DESIGN §22 |

---

## 3. Questions for the client (and mentor)

Priority: **H** blocks a design decision, **M** changes behavior but has a safe default, **L** polish.
"Applied default" is what the design currently assumes.

### 3.1 Limits and quota

| ID | Question | Why it matters | Applied default | Pri | Decision |
|---|---|---|---|---|---|
| Q-01 | Are limits per **key** or per **organization**? | Limiter scope, schema, and whether minting keys multiplies capacity | Per organization, shared by all its keys | H | D-01 |
| Q-02 | Does `GET /usage` (and admin calls) count against rate limit and quota? | Defines what "usage" is and whether usage polling can exhaust the quota | No: only `GET /v1/quote` consumes tokens and quota | M | D-03 |
| Q-03 | What is a "served" request: HTTP 2xx only? What about 4xx caused by the caller (bad parameter) and 5xx? | Defines the usage number the client is billed on | Counted only if admitted **and** the handler returns 2xx. Anything else is voided (counter and usage row reversed). 401/403/429 are never admitted | H | D-03 |
| Q-04 | Is the short-term limit a burst cap or a sustained rate ("N per second" vs "N per minute")? | Algorithm choice | Token bucket with a sustained rate and a burst size per plan | M | D-05 |
| Q-05 | Which timezone and anchor define "month": UTC calendar month, or a per-organization billing cycle? | Reset moment, `Retry-After`, counter key design | UTC calendar month | H | D-02 |
| Q-06 | At 100% of the allowance: hard cutoff or overage billing? | Product behavior; billing is out of scope | Hard cutoff | M | D-04 |
| Q-07 | Does unused allowance roll over? | Counter design | No | L | D-04 |
| Q-08 | Plan change mid-month: reset usage, prorate quota, or neither? What if a downgrade leaves the org above the new quota? | Customer-visible and billing-visible | Applies immediately; usage is not reset; quota is not prorated; an org above the new quota is rejected until the next month; the change is allowed | H | D-13 |
| Q-09 | For an exhausted monthly allowance, is HTTP 429 acceptable or should it be 402/403? | Client integration | 429 with `error=quota_exceeded` | M | D-17 |

### 3.2 API keys

| ID | Question | Why it matters | Applied default | Pri | Decision |
|---|---|---|---|---|---|
| Q-10 | How long must an old key stay valid after rotation, and can an operator choose it? | Security window vs. customer safety | 24 h default, configurable per rotation (max 7 days); revoke ends it at once | M | D-08 |
| Q-11 | Is a lost key recoverable? | Whether the secret may be stored reversibly | No. Shown once at creation; a lost key means rotation | H | D-07 |
| Q-12 | Do keys expire on their own (TTL)? | Auth check and schema | No expiry except through rotation grace | L | D-08 |
| Q-13 | Maximum active keys per organization? | Abuse and cache size | 20, configurable | L | D-01 |
| Q-14 | Who may rotate and revoke: operators only, or also customers? | Admin API surface | Operators only (the brief says operators) | M | D-18 |
| Q-15 | Must revocation take effect immediately everywhere? What delay is acceptable? | Rules out per-instance caching | Immediate; target <= 1 s on every instance | H | D-09 |

### 3.3 Usage and reporting

| ID | Question | Why it matters | Applied default | Pri | Decision |
|---|---|---|---|---|---|
| Q-16 | What granularity does "usage over time" need: minute, hour, day? | Storage cost and rollup design | Stored hourly (UTC); API serves hour or day | M | D-15 |
| Q-17 | Must reported usage be real time or may it lag? | Whether reporting reads Redis or Postgres | `used`/`remaining` are real time (Redis counter). The time series may lag by the worker lag (target < 1 s, alert at 30 s) | M | D-10 |
| Q-18 | How long is usage history kept? | Table growth | Raw events 14 days; hourly rollup 13 months | M | D-15 |
| Q-19 | G-3 "matches requests served": is a bounded difference after a crash acceptable? | Exact equality cannot be guaranteed when an instance dies mid-request | Never undercount; may overcount by at most the requests in flight when an instance dies | H | D-10 |
| Q-20 | Do operators also need cross-organization totals? | Extra endpoint and queries | No; per-organization only | L | D-16 |

### 3.4 Reliability and operations

| ID | Question | Why it matters | Applied default | Pri | Decision |
|---|---|---|---|---|---|
| Q-21 | If Redis is unavailable, may the protected endpoint serve requests unmetered (fail open), or must it refuse (fail closed)? | Availability vs. control and the correctness of G-1 and G-3 | Fail closed: 503 with `Retry-After` | H | D-12 |
| Q-22 | Is Redis high availability (replicas, Sentinel) required? | Complexity and cost | No; single node, documented limitation | M | D-20 |
| Q-23 | After a total Redis disk loss, how much usage may be lost? | Defines durability settings | Bounded by worker lag; Redis uses AOF `appendfsync always` so a process crash loses nothing | M | D-11 |
| Q-24 | Is `appendfsync always` (extra Redis write latency, measured in SPIKE_RESULTS) acceptable operationally? | Latency budget vs. durability | Yes at the 200 rps target | M | D-11 |

### 3.5 Scale, security, and scope

| ID | Question | Why it matters | Applied default | Pri | Decision |
|---|---|---|---|---|---|
| Q-25 | Expected scale: organizations, keys, average and peak rps, number of instances? | Sizing, retention math | 1,000 orgs, 10,000 keys, 200 rps peak (the G-4 target), 2 to 4 instances | M | DATABASE_DESIGN §8 |
| Q-26 | Is one shared admin token acceptable, or are named operator accounts needed? | Admin auth and audit | One static token from the environment; audit actor is "admin" | M | D-18 |
| Q-27 | Should repeated invalid keys from one address be throttled (brute force / DoS)? | Extra limiter and trust of `X-Forwarded-For` | Not in v1; high-entropy keys make guessing infeasible; flooding is a listed limitation | M | THREAT_MODEL T-13 |
| Q-28 | Is the load balancer the only way to reach the instances (private network)? | The request-id de-duplication trusts the LB-assigned id | Yes; instances publish no ports | H | D-14 |
| Q-29 | Any hint about the expected mid-month requirement change? | Where to leave flexibility | None known; keep limits, window, and scope generic | M | DESIGN §22 |
| Q-30 | Quote data: any structure, filters, or parameters? | Handler and 4xx behavior | Static dummy list; `GET /v1/quote` takes no parameters | L | D-23 |

---

## 4. Conflicts the design resolves by itself (no client answer needed)

- Redis `TIME` is used for token-bucket timing, so instance clock skew cannot corrupt limits.
- `GET /usage` returns an identical `401` body for unknown, revoked, and expired keys so valid key ids cannot be enumerated.

## 5. Next step

Send the **H** priority questions first (Q-01, Q-03, Q-05, Q-08, Q-11, Q-15, Q-19, Q-21, Q-28). Until answers arrive the defaults stay in force and every default is traceable to a decision, so a changed answer maps to a known set of edits.