import os

# Must run before anything imports app.core.config (Settings() is built at import time).
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KEY_PEPPER", "test-pepper")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
