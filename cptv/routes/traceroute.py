from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import respond

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/traceroute")
    @router.get("/traceroute.json")
    @router.get("/traceroute.txt")
    @router.get("/api/v1/traceroute")
    def traceroute(request: Request) -> Response:
        text = "Traceroute is not yet implemented in this deployment."
        return respond(
            request,
            templates=templates,
            html_template="section_stub.html",
            html_context={"heading": "Traceroute", "present": False, "data": {}},
            json_data={"status": "unavailable"},
            text=text,
        )

    return router
