# Project Overview: Quota Gateway for the Quotes API

> A plain-language guide to what we are building. Diagrams use Mermaid (they render on GitHub, VS Code with a Mermaid extension, and most Markdown viewers).

---

## 1. The one-minute version

A small company sells **quotes data** through a **read-only API**. They need a **gatekeeper** in front of it that decides:

- **Who** may call it (API keys that belong to organizations)
- **How fast** they may call it (short-term rate limit)
- **How much** they may call it (monthly allowance)
- **What happened** (usage reports for the company and for customers)

The quotes themselves are dummy data. The real work is the **gatekeeper**: correctness under several servers, secure key handling, accurate usage, and speed.

---

## 2. Who uses it

```mermaid
flowchart LR
    OP([Operator<br/>company staff])
    DEV([Developer<br/>customer's app])
    OP -->|create orgs, keys<br/>rotate, revoke<br/>change plan<br/>view any usage| GW[Quota Gateway]
    DEV -->|GET /v1/quote<br/>GET /usage| GW
    GW --> Q[(Quotes data)]
```

| Actor | Can do |
|---|---|
| **Operator** | Create organizations and keys, rotate and revoke keys, change plans, see all usage |
| **Developer / customer** | Call the quotes API with a key, see their own usage and remaining quota |

---

## 3. System architecture

Several identical API instances sit behind a load balancer. They keep **no state** themselves. Everything shared lives in Redis (fast) or Postgres (permanent).

```mermaid
flowchart TB
    C([Clients]) --> LB[nginx<br/>load balancer]
    LB --> A1[API instance 1]
    LB --> A2[API instance 2]
    LB --> A3[API instance 3]

    A1 & A2 & A3 -->|atomic limit check<br/>key cache| R[(Redis)]
    A1 & A2 & A3 -->|key lookup on cache miss<br/>admin writes<br/>usage reads| P[(Postgres)]
    A1 & A2 & A3 -->|usage events| S[[Redis Stream]]

    S --> W[Usage worker]
    W -->|idempotent insert<br/>hourly rollups| P
    W -.->|rebuild counters<br/>after Redis loss| R
```

| Component | Job |
|---|---|
| **nginx** | Spreads traffic across API instances |
| **API instances** | Authenticate keys, enforce limits, serve quotes, expose admin and usage endpoints |
| **Redis** | Fast shared counters (rate and quota), key cache, usage event stream |
| **Worker** | Moves usage events into Postgres safely, rebuilds Redis counters if needed |
| **Postgres** | Source of truth: orgs, plans, keys (hashed), usage history |

---

## 4. What happens on one request

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as API instance
    participant R as Redis
    participant P as Postgres
    participant S as Usage stream
    participant W as Worker

    C->>A: GET /v1/quote (X-API-Key)
    A->>R: look up key by hash (cache)
    alt cache miss
        A->>P: fetch key by public id
        A->>R: store in cache (short TTL)
    end
    alt key invalid, revoked or expired
        A-->>C: 401 Unauthorized
    else key valid
        A->>R: ONE atomic Lua script:<br/>check rate AND monthly quota
        alt over rate limit
            A-->>C: 429 rate_limited + Retry-After
        else over monthly quota
            A-->>C: 429 quota_exceeded + Retry-After
        else allowed
            A->>A: serve the quote
            A->>S: append usage event (request_id)
            A-->>C: 200 + X-RateLimit-* headers
        end
    end
    W->>S: read events
    W->>P: INSERT ... ON CONFLICT DO NOTHING
    W->>P: update hourly rollup
```

**Why one atomic script?** If two instances each checked and then incremented separately, two requests could both slip through at the last unit of allowance. One script in Redis runs as a single indivisible step, so limits stay exact no matter how many instances run.

---

## 5. Decision flow inside the limiter

```mermaid
flowchart TD
    Start([Request arrives with valid key]) --> Bucket{Token bucket<br/>has a token?}
    Bucket -- no --> RL[429 rate_limited<br/>Retry-After = seconds until next token]
    Bucket -- yes --> Quota{Monthly used<br/>&lt; allowance?}
    Quota -- no --> QE[429 quota_exceeded<br/>Retry-After = seconds until next month UTC]
    Quota -- yes --> Take[Consume 1 token and 1 quota unit<br/>in the same atomic step]
    Take --> Serve([Serve quote])
```

---

## 6. API key lifecycle

```mermaid
stateDiagram-v2
    [*] --> Active: operator creates key<br/>(shown once)
    Active --> Rotating: operator rotates<br/>(new key issued)
    Rotating --> Expired: grace window ends
    Rotating --> Revoked: operator revokes
    Active --> Revoked: operator revokes<br/>(immediate)
    Expired --> [*]
    Revoked --> [*]
```

- **Rotation without breaking customers:** the new key works immediately; the old key keeps working for a grace window (default 24h) so running systems can switch over. Both keys share the same org quota.
- **Revocation:** immediate. Used when a key is stolen.

### How keys are stored (stolen database protection)

```mermaid
flowchart LR
    G[Generate key<br/>qk_PUBLICID_SECRET] --> Show[Show full key to operator<br/>ONCE]
    G --> H[HMAC-SHA256<br/>secret + server pepper]
    H --> DB[(Database stores<br/>public id + hash only)]
    Pepper[[Pepper in env / secret store<br/>NOT in the database]] -.-> H
```

A database dump contains only hashes. Without the pepper (stored outside the DB) the hashes cannot be turned into usable keys, and the original secret is never stored.

---

## 7. Keeping usage numbers honest

The hardest guarantee: **reported usage must equal requests actually served**, even across crashes, restarts and client retries.

```mermaid
flowchart LR
    Serve[Request served] --> Ev[Usage event with unique request_id]
    Ev --> St[[Redis Stream]]
    St --> Wk[Worker reads batch]
    Wk --> Ins[(Postgres<br/>unique request_id)]
    Ins -->|duplicate? ignored| Ins
    Ins --> Roll[Hourly rollup]
    Roll --> Rep[Usage reports<br/>and remaining quota]

    Retry([Client retry with<br/>same Idempotency-Key]) -.->|not counted twice| Ev
    Crash([Worker crashes mid-batch]) -.->|unacknowledged events replayed,<br/>duplicates ignored| Wk
    RedisLoss([Redis restarts]) -.->|counters rebuilt from Postgres| Ins
```

Idea in one line: **at-least-once delivery + idempotent writes = exactly-once effect.**

---

## 8. Data model

```mermaid
erDiagram
    PLAN ||--o{ ORGANIZATION : "assigned to"
    ORGANIZATION ||--o{ API_KEY : owns
    ORGANIZATION ||--o{ USAGE_EVENT : generates
    ORGANIZATION ||--o{ USAGE_HOURLY : rolls_up_to
    ORGANIZATION ||--o{ AUDIT_LOG : "changes recorded in"

    PLAN {
        uuid id PK
        string name
        int rate_per_second
        int burst
        bigint monthly_quota
    }
    ORGANIZATION {
        uuid id PK
        string name
        uuid plan_id FK
    }
    API_KEY {
        uuid id PK
        string public_id
        string key_hash
        string status
        timestamp expires_at
        uuid org_id FK
    }
    USAGE_EVENT {
        string request_id PK
        uuid org_id FK
        uuid key_id
        timestamp served_at
    }
    USAGE_HOURLY {
        uuid org_id FK
        timestamp hour
        bigint requests
    }
    AUDIT_LOG {
        uuid id PK
        string action
        uuid org_id FK
        timestamp at
    }
```

---

## 9. The guarantees and how each is proven

```mermaid
flowchart LR
    G1[Limits correct with<br/>several instances] --> T1[Concurrent requests through LB<br/>exactly L succeed]
    G2[Stolen DB reveals<br/>no usable keys] --> T2[Dump table, try hashes as keys<br/>all rejected]
    G3[Usage matches<br/>requests served] --> T3[Load + kill instance + restart Redis<br/>client 200s == Postgres rows]
    G4[Limiter under 10ms p95<br/>at 200 rps] --> T4[k6 before / fix bottleneck / after]
    G5[Rotation does not<br/>break clients] --> T5[Old key works in grace window<br/>fails after]
```

---

## 10. Deliverables checklist

| # | Deliverable | When |
|---|---|---|
| 1 | `DESIGN.md` (assumptions, open questions, rejected alternatives) | End of week 1, reviewed before building |
| 2 | Tests proving every guarantee | Weeks 3 to 4 |
| 3 | `README.md` and CI workflow | Week 4 |
| 4 | `LIMITATIONS.md` (how it can still fail) | Week 4 |
| 5 | Live walkthrough with an on-the-spot change | End of month |

Also needed throughout: Python 3.11, FastAPI, SQLAlchemy 2 + Alembic, Postgres 15, Redis, pytest, ruff and mypy clean with **no inline suppressions**, and everything runs with `docker compose up`.

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **Organization (org)** | A customer account. Owns keys and sits on a plan |
| **Plan** | Defines rate limit, burst and monthly allowance |
| **Rate limit** | Max speed of requests over a short window (for example per second) |
| **Monthly quota** | Total requests allowed per calendar month |
| **Token bucket** | Rate-limit method: tokens refill steadily, each request spends one, bursts allowed up to bucket size |
| **Rotation** | Replacing a key with a new one while the old one still works briefly |
| **Revocation** | Killing a key immediately |
| **Idempotency key** | Client-supplied id so a retried request is counted once |
| **Pepper** | Server-side secret mixed into key hashing, kept outside the database |
| **p95** | 95% of requests are faster than this latency |
| **Lua script (Redis)** | Small program Redis runs as one indivisible step |