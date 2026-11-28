"""G-1 smoke through nginx: quota 50, 200 concurrent requests -> exactly 50 successes."""

import collections
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("BASE_URL", "http://localhost:8080")
ADMIN = os.environ.get("ADMIN_TOKEN")
QUOTA, ATTEMPTS = 50, 200


def call(
    method: str, path: str, token: str, body: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def main() -> int:
    if not ADMIN:
        print("set ADMIN_TOKEN first")
        return 2
    suffix = uuid.uuid4().hex[:8]
    _, plan = call(
        "POST",
        "/admin/plans",
        ADMIN,
        {
            "name": f"smoke-{suffix}",
            "rate_limit_per_second": 1000,
            "burst": 1000,
            "monthly_quota": QUOTA,
        },
    )
    _, org = call("POST", "/admin/orgs", ADMIN, {"name": f"smoke-{suffix}", "plan_id": plan["id"]})
    _, key = call("POST", f"/admin/orgs/{org['id']}/keys", ADMIN, {"label": "smoke"})
    token = str(key["api_key"])

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: call("GET", "/v1/quote", token), range(ATTEMPTS)))
    codes = collections.Counter(status for status, _ in results)
    errors = collections.Counter(
        str(body.get("error")) for status, body in results if status != 200
    )
    print(f"status codes: {dict(codes)}  errors: {dict(errors)}")
    ok = codes[200] == QUOTA and errors["quota_exceeded"] == ATTEMPTS - QUOTA
    print("PASS: exactly quota admitted" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
