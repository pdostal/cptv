from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from cptv.middleware import RequestTimingMiddleware, SubdomainMiddleware
from cptv.routes import asn as asn_routes
from cptv.routes import dns as dns_routes
from cptv.routes import geoip as geoip_routes
from cptv.routes import health as health_routes
from cptv.routes import help as help_routes
from cptv.routes import index as index_routes
from cptv.routes import ip as ip_routes
from cptv.routes import protocol as protocol_routes
from cptv.routes import traceroute as traceroute_routes
from cptv.services.valkey import close_valkey

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def create_app() -> FastAPI:
    STATIC_DIR.mkdir(exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await close_valkey()

    application = FastAPI(
        title="cptv",
        description="CaPTiVe — self-hosted network diagnostics. See PLAN.md.",
        version="0.2.4",
        lifespan=lifespan,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Order: timing first (so it brackets everything), subdomain second.
    application.add_middleware(SubdomainMiddleware)
    application.add_middleware(RequestTimingMiddleware)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    application.include_router(health_routes.router)
    application.include_router(ip_routes._register(templates))
    application.include_router(geoip_routes._register(templates))
    application.include_router(asn_routes._register(templates))
    application.include_router(dns_routes._register(templates))
    application.include_router(help_routes._register(templates))
    application.include_router(protocol_routes._register(templates))
    application.include_router(index_routes._register(templates))
    application.include_router(traceroute_routes._register(templates))

    # Custom HTTPException handler that appends a trailing newline so
    # error bodies don't trigger zsh's reverse-video '%' indicator on
    # missing-newline responses, and so curl prints them cleanly.
    # Hooked on Starlette's base HTTPException so 404s and validation
    # errors raised by the framework also get the newline.
    @application.exception_handler(StarletteHTTPException)
    async def _http_exception_with_newline(_request: Request, exc: StarletteHTTPException):
        body = json.dumps({"detail": exc.detail}, separators=(",", ":")) + "\n"
        return Response(
            content=body,
            status_code=exc.status_code,
            media_type="application/json",
            headers=getattr(exc, "headers", None) or {},
        )

    return application


app = create_app()
