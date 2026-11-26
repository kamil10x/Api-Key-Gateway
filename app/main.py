from fastapi import FastAPI

from app.core.errors import register_error_handlers
# from app.api.admin import router as admin_router

app = FastAPI(title="API-key Gateway")
register_error_handlers(app)

# The admin router is intentionally not mounted yet. To try it by hand, add:
#   from app.api.admin import router as admin_router
#   app.include_router(admin_router)

# app.include_router(admin_router)

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
