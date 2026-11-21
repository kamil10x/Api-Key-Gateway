# WEEK1_CHECKLIST.md: Completion checklist and review preparation

> Day 7 deliverable. Status date: 2026-11-20. "Drafted" means the assistant wrote it; **you must still read, understand, and own it** before the mentor sees it. "Pending (you)" means it cannot be completed without you.

## 1. Deliverables

| Deliverable | File | Status |
|---|---|---|
| Open questions | `OPEN_QUESTIONS.md` | Drafted. Pending (you): check against the exact wording of your brief |
| Decision table | `DECISIONS.md` | Drafted |
| Database design and ER diagram | `DATABASE_DESIGN.md` | Drafted. DDL **not executed** against Postgres yet |
| Spike results | `SPIKE_RESULTS.md` + `spike/` | Reference run by the assistant and **your own run (2026-11-21) both recorded**; exactness results match |
| Threat model | `THREAT_MODEL.md` | Drafted. No application tests exist yet; proofs are planned |
| Design document | `DESIGN.md` (22 sections + self-review) | Drafted, review-ready |
| Architecture diagram | Mermaid in `DESIGN.md` section 9 (also sequence, state, failure flow) | Drafted. Render-check on GitHub or a Mermaid viewer (not render-tested here) |
| Repo skeleton | `app/`, `tests/`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `nginx/` | Skeleton checks pass (below). `docker compose up` **verified to start on your machine** (section 2); requests through nginx still to be checked |
| Lint, types, tests configured | `pyproject.toml`, `Makefile`, `.github/workflows/ci.yml` | Verified locally (below). CI workflow itself has not run on GitHub |
| This checklist | `WEEK1_CHECKLIST.md` | Done |

## 2. What was actually verified, and what was not

| Check | Result |
|---|---|
| `ruff check .` and `ruff format --check .` | **Passed** (authoring sandbox, Python 3.12.3; project targets 3.11) |
| `mypy` (strict, no inline suppressions) | **Passed**, 10 source files |
| `pytest -q` | **Passed**, 1 test (`/healthz` on the empty app) |
| `nginx -t` on `nginx/nginx.conf` | **Passed** (syntax only, with `app1`/`app2` mapped in `/etc/hosts`) |
| `docker-compose.yml` parsed as YAML | **Passed** in the authoring sandbox (no Docker there) |
| `docker compose up --build` on your machine (Windows, Docker Desktop), log of 2026-11-21 | **Verified from your log:** image build succeeded; Postgres 15.20 and Redis 7.4.12 reached healthy; Redis loaded its AOF files again after `down`/`up` (persistence volume works); `app1` and `app2` started and passed their health checks; nginx started with the mounted config and reported "ready for start up" with no errors |
| `curl.exe -i http://localhost:8080/healthz` through nginx (2026-11-21, your output) | **Verified:** `HTTP/1.1 200 OK`, `Server: nginx/1.27.5`, body `{"status":"ok"}` |
| `curl.exe http://localhost:8000/healthz` directly to an instance | **Verified blocked:** connection failed, so the instances are reachable only through nginx (DESIGN A-12) |
| Redis settings inside the running container (your output) | **Verified:** `appendfsync` = `always`, `maxmemory-policy` = `noeviction` |
| Load balancing across `app1` and `app2` (your ten-request run, 2026-11-21) | **Verified:** ten requests through `localhost:8080` all returned `{"status":"ok"}`; the logs show 5 proxied requests (source 172.20.0.6, the nginx container, `HTTP/1.0`) on `app1` and 5 on `app2`, i.e. an even round-robin split |
| Lua limiter spike | **Passed** all scenarios in the reference run; Redis 7.0.15 on a 1 vCPU sandbox |
| Mermaid diagrams | Written by hand; **not render-tested** |
| DDL in `DATABASE_DESIGN.md` | **Not executed**; validated by reading only |

`spike/` is excluded from ruff in `pyproject.toml` (a config entry, not an inline suppression) because it is throwaway code. Say so if asked.

## 3. Your actions before submitting

- [ ] Read all documents end to end; mark anything you cannot explain and ask.
- [x] Run the spike yourself and fill in the "Your run" column (done 2026-11-21, `SPIKE_RESULTS.md` section 6).
- [ ] Be ready to explain why your AOF `always` latency (p95 2.6 ms) differs from the reference run (0.5 ms) and what you will do about it (see section 6 point 2 and 3 of `SPIKE_RESULTS.md`).
- [x] `docker compose up --build` starts all five services (your log, 2026-11-21).
- [ ] With the stack running (`docker compose up -d`), run in PowerShell and record the output:
  - ~~`curl.exe -i http://localhost:8080/healthz` returns 200~~ (done, verified).
  - [x] (done, verified) Balancing check. That loop is PowerShell syntax and fails in `cmd` (your prompt is `cmd`). In **cmd** run `for /L %i in (1,1,10) do @curl.exe -s http://localhost:8080/healthz`, then `docker compose logs app1 app2 | findstr "172."` (in PowerShell: `1..10 | ForEach-Object { curl.exe -s http://localhost:8080/healthz }` then `docker compose logs app1 app2 | Select-String "172."`). Lines with a 172.x address are requests that came through nginx; the 127.0.0.1 lines are health checks. Both `app1` and `app2` should appear.
  - ~~Redis `appendfsync` shows `always` and `maxmemory-policy` shows `noeviction`~~ (done, verified).
  - ~~`curl.exe http://localhost:8000/healthz` should fail~~ (done, verified).
- [ ] Run `make check` on your machine with Python 3.11 (`pip install -e ".[dev]"`).
- [ ] Push to GitHub and confirm the CI workflow goes green.
- [ ] Open the Mermaid diagrams in GitHub's preview; fix any that do not render.
- [ ] Check `OPEN_QUESTIONS.md` section 2 and the contradictions against the exact words of your brief; add anything missed.
- [ ] Decide which of the proposed defaults you personally disagree with (fail closed, `appendfsync always`, retention numbers) and be ready to defend or change them.
- [ ] Send the **H** priority questions (`OPEN_QUESTIONS.md` section 5) to your mentor.

## 4. Day-by-day: what, why, how to validate, common mistakes

### Day 1: Understand the requirements
- **What / why:** list every silence and contradiction so that the design does not rest on hidden guesses.
- **Output:** `OPEN_QUESTIONS.md` (8 contradictions, 30 questions, each with a default and a decision link).
- **Validate:** every sentence of the brief maps to an R-, G-, or C- item; every ambiguity has a Q- and an applied default.
- **Common mistakes:** silently picking an answer; writing questions with no default (work stalls); not separating what the client said from what you assumed.
- **Be ready to explain:** why "retries" in G-3 is ambiguous (C-02) and how you resolved it.

### Day 2: Decisions and database design
- **What / why:** a decision without alternatives and trade-offs cannot be reviewed.
- **Output:** `DECISIONS.md` (D-01 to D-23), `DATABASE_DESIGN.md` with ER diagram.
- **Validate:** every Q- default appears as a D-; every table has a primary key, foreign keys, constraints, and at least one index that serves a named query; retention and isolation are stated.
- **Common mistakes:** a plaintext key column; an index nobody queries; no retention (tables grow forever); "isolation" that is only a hope.
- **Be ready to explain:** why `usage_events` is keyed by `stream_id`, not by a request id.

### Day 3: Redis Lua spike
- **What / why:** prove the central idea (atomic admission) before building on it.
- **Output:** `spike/*`, `SPIKE_RESULTS.md`.
- **Validate:** rerun it yourself; exactness results must be identical on your machine, timings will differ.
- **Common mistakes:** testing with one thread (races never appear); claiming durability from a `kill -9` test; reporting the spike as proof of the real guarantee.
- **Be ready to explain:** why naive GET/INCR overshoots (three steps with a gap), why one Lua script cannot be interleaved, and what the spike does **not** prove.

### Day 4: Threat model
- **What / why:** a guarantee you cannot test is a wish.
- **Output:** `THREAT_MODEL.md` (T-01 to T-14, each with attack, impact, mitigation, expected behavior, planned proof).
- **Validate:** each threat names a test; no threat is marked "proven".
- **Common mistakes:** listing threats without a test; fail-open by accident; forgetting that a stolen key and a stolen database are different problems.
- **Be ready to explain:** why HMAC with a pepper and not bcrypt for API keys.

### Day 5: Design document and skeleton
- **What / why:** the single document a reviewer reads; an empty app that is already lint-, type-, and test-clean so later work stays clean.
- **Output:** `DESIGN.md` (22 sections), skeleton, Compose structure.
- **Validate:** `make check`; `docker compose up` on your machine.
- **Common mistakes:** implementing features early; adding `# noqa` or `# type: ignore` to get green; real secrets in `docker-compose.yml`.
- **Be ready to explain:** the request path in section 12, step by step.

### Day 6: Self-review
- **What / why:** find the flaws before the mentor does.
- **Output:** `DESIGN.md` Appendix A (14 findings).
- **Validate:** each finding has a resolution you can point to; the "not fixed" list is honest.
- **Common mistakes:** a review that only praises; fixing a flaw in one document and leaving the others inconsistent.
- **Be ready to explain:** finding 1 (client idempotency keys let a client bypass quota) in your own words. It is the best example of a security hole caught by design review.

### Day 7: Submission
- **What / why:** one coherent package.
- **Validate:** section 3 above is fully ticked.
- **Common mistakes:** submitting numbers you did not measure; claiming `docker compose up` works without running it.

## 5. Questions to rehearse for the mentor review

1. Walk through `GET /v1/quote` from the client to the response. Where could it fail, and what does the client see each time?
2. Why is the usage event written inside the admission script? What goes wrong if it is written after serving?
3. What exactly does "usage matches requests served" guarantee after an instance is killed? Where is it **not** exact?
4. Why do you ignore a client `Idempotency-Key`?
5. Why fail closed when Redis is down? What would change your mind?
6. How does a stolen database dump fail to reveal keys, and what protects the keys if the pepper is also stolen?
7. A customer upgrades mid-month: what happens to their counter and bucket?
8. What does the spike prove, and what does it not?
9. The brief changes mid-month to per-key limits. Which files change?
10. What is the first thing you expect to find when you profile for the 10 ms budget?

## 6. Out of scope for Week 1 (do not start)

Alembic migrations, any endpoint beyond `/healthz`, the limiter wrapper, the worker, k6 scripts, `LIMITATIONS.md`, and the full README. Starting them before the design review is explicitly discouraged by the brief.