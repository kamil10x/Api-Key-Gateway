import os
import statistics
import time

import httpx

BASE = os.environ.get("BASE_URL", "http://localhost:8080")
ADMIN = {"Authorization": f"Bearer {os.environ['ADMIN_TOKEN']}"}
N = int(os.environ.get("N", "500"))

with httpx.Client(base_url=BASE, timeout=10) as c:
    suffix = str(int(time.time()))
    plan = c.post(
        "/admin/plans",
        headers=ADMIN,
        json={
            "name": f"smoke-{suffix}",
            "rate_limit_per_second": 20,
            "burst": 40,
            "monthly_quota": 100000,
        },
    ).json()
    org = c.post(
        "/admin/orgs", headers=ADMIN, json={"name": f"smoke-{suffix}", "plan_id": plan["id"]}
    ).json()
    key = c.post(f"/admin/orgs/{org['id']}/keys", headers=ADMIN).json()["api_key"]
    good = {"Authorization": f"Bearer {key}"}

    r = c.get("/v1/quote", headers=good)
    print("valid key     ->", r.status_code, r.json())
    r = c.get("/v1/quote", headers={"Authorization": "Bearer qk_bad_key"})
    print("bad key       ->", r.status_code, r.json())
    r = c.get("/v1/quote")
    print("no header     ->", r.status_code, r.json())

    times = []
    for _ in range(N):
        t = time.perf_counter()
        c.get("/v1/quote", headers=good)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    total = sum(times) / 1000
    print(
        f"baseline ({N} sequential): p50={statistics.median(times):.1f}ms "
        f"p95={times[int(N * 0.95)]:.1f}ms p99={times[int(N * 0.99)]:.1f}ms "
        f"~{N / total:.0f} req/s"
    )
