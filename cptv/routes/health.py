from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health", include_in_schema=True)
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "checks": {}})
