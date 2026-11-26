import hmac
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.keys import KEY_TAG

_bearer = HTTPBearer(auto_error=False)
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Static bearer-token auth for operator routes (DESIGN D-18).

    Fails closed: an empty ADMIN_TOKEN rejects everything. An API key presented
    here gets 403 (wrong credential type), never access.
    """
    if credentials is None:
        raise UnauthorizedError("unauthorized", "Admin credentials required", _WWW_AUTHENTICATE)

    expected = settings.admin_token.encode()
    if expected and hmac.compare_digest(credentials.credentials.encode(), expected):
        return
    if credentials.credentials.startswith(f"{KEY_TAG}_"):
        raise ForbiddenError("forbidden", "API keys are not accepted on admin routes")
    raise UnauthorizedError("unauthorized", "Invalid admin credentials", _WWW_AUTHENTICATE)
