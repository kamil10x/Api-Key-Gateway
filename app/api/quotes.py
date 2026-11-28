import random
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.admission import admit_request
from app.core.limiter import Admission

router = APIRouter(prefix="/v1", tags=["quotes"])

_QUOTES = [
    {"text": "Simplicity is prerequisite for reliability.", "author": "Edsger Dijkstra"},
    {"text": "Premature optimization is the root of all evil.", "author": "Donald Knuth"},
    {"text": "Make it work, make it right, make it fast.", "author": "Kent Beck"},
]


@router.get("/quote")
def get_quote(_adm: Annotated[Admission, Depends(admit_request)]) -> dict[str, str]:
    return random.choice(_QUOTES)  # noqa: S311
