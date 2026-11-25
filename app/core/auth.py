import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings


def require_admin(
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.admin_token.encode()
    provided = (x_admin_token or "").encode()
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin token",
        )


AdminAuth = Depends(require_admin)