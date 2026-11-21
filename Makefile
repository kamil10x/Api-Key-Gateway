.PHONY: check lint types test up down
check: lint types test
lint:
	ruff check .
	ruff format --check .
types:
	mypy
test:
	pytest -q
up:
	docker compose up --build
down:
	docker compose down