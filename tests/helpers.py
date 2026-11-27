from typing import Any

from fastapi.testclient import TestClient

VALID_PLAN: dict[str, Any] = {
    "name": "pro",
    "rate_limit_per_second": 20,
    "burst": 40,
    "monthly_quota": 100_000,
}
UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def make_plan(client: TestClient, auth: dict[str, str], **overrides: Any) -> dict[str, Any]:
    resp = client.post("/admin/plans", headers=auth, json={**VALID_PLAN, **overrides})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def make_org(
    client: TestClient, auth: dict[str, str], plan_id: str, name: str = "acme"
) -> dict[str, Any]:
    resp = client.post("/admin/orgs", headers=auth, json={"name": name, "plan_id": plan_id})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body
