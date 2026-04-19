from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from cptv.config import get_settings
from cptv.services.valkey import health_check as valkey_health_check

router = APIRouter()


async def _check_mtr_packet() -> str:
    """Verify mtr-packet binary is executable."""
    settings = get_settings()
    mtr_path = settings.mtr_path
    try:
        proc = await asyncio.create_subprocess_exec(
            mtr_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return "ok" if proc.returncode == 0 else "error"
    except (FileNotFoundError, OSError, TimeoutError):
        return "error"


def _check_mtr_capability() -> str:
    """Check if mtr-packet has cap_net_raw (Linux only, best-effort)."""
    # On non-Linux or when getcap isn't available, assume ok.
    import shutil

    mtr_packet = shutil.which("mtr-packet")
    if mtr_packet is None:
        return "unknown"

    if not os.path.exists("/usr/sbin/getcap"):
        return "unknown"

    try:
        import subprocess  # noqa: S404

        result = subprocess.run(  # noqa: S603
            ["/usr/sbin/getcap", mtr_packet],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "cap_net_raw" in result.stdout:
            return "ok"
        return "missing"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


@router.get("/health", include_in_schema=True)
async def health() -> JSONResponse:
    mtr_status = await _check_mtr_packet()
    cap_status = _check_mtr_capability()
    valkey_status = await valkey_health_check()

    checks = {
        "geoip_db": "ok",
        "valkey": valkey_status,
        "mtr_packet": mtr_status,
        "mtr_capability": cap_status,
    }
    all_ok = all(v == "ok" for v in checks.values() if v != "unknown")
    overall = "ok" if all_ok else "degraded"
    status_code = 200 if overall == "ok" else 503
    return JSONResponse({"status": overall, "checks": checks}, status_code=status_code)
