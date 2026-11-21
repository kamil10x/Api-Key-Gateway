# Quota Gateway

Access-control service (organizations, plans, API keys, rate limit, monthly quota, usage) in front of a read-only quotes API.

**Status: Week 1, design only. No business features are implemented.**

Start with [DESIGN.md](DESIGN.md); the other Week 1 documents are linked from its header.

```bash
pip install -e ".[dev]"
make check          # ruff, mypy --strict, pytest on the empty app
docker compose up   # starts postgres, redis, app1, app2, nginx (/healthz via http://localhost:8080)
```

A full README (setup, API, operations) is a later deliverable.