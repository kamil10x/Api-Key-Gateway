from fastapi import FastAPI

from app.api import quotes
from app.api.admin import router as admin_router
from app.core.errors import register_error_handlers

app = FastAPI(title="API-key Gateway")
register_error_handlers(app)

app.include_router(admin_router)
app.include_router(quotes.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
