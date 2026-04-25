from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from cptv.negotiation import add_public_cors, respond
from cptv.services import geoip as geoip_service
from cptv.services import ip as ip_service

router = APIRouter()


def _register(templates: Jinja2Templates) -> APIRouter:
    @router.get("/geoip")
    @router.get("/api/v1/geoip")
    def geoip(request: Request) -> Response:
        address = ip_service.client_ip(request)
        result = geoip_service.lookup(address)

        json_data: dict[str, Any]
        if result is None:
            json_data = {
                "country_code": None,
                "country": None,
                "region": None,
                "city": None,
                "latitude": None,
                "longitude": None,
            }
            text = "geolocation unavailable (private IP or GeoLite2 DB missing)"
        else:
            json_data = {
                "country_code": result.country_code,
                "country": result.country,
                "region": result.region,
                "city": result.city,
                "latitude": result.latitude,
                "longitude": result.longitude,
            }
            coords = (
                f"{result.latitude:.4f}, {result.longitude:.4f}"
                if result.latitude is not None and result.longitude is not None
                else "—"
            )
            text = "\n".join(
                [
                    f"🌍 Country:   {result.country_code or '?'}  {result.country or ''}".rstrip(),
                    f"   Region:    {result.region or '—'}",
                    f"   City:      {result.city or '—'}",
                    f"   Coords:    {coords}",
                ]
            )

        return add_public_cors(
            respond(
                request,
                templates=templates,
                html_template="section_stub.html",
                html_context={"heading": "GeoIP", "data": json_data, "present": result is not None},
                json_data=json_data,
                text=text,
            )
        )

    return router
