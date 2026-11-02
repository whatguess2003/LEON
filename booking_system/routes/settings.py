"""Hotel operating settings routes (access.MEMBER_ROUTES). Any member may read them; a change is checked per setting against the
catalog's set_module / set_level by the settings service."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from booking_system.access import session
from booking_system.settings import service

router = APIRouter()

ACCESS = []


def _reply(result):
    return result if result.get("ok") else JSONResponse(content=result, status_code=400)


@router.get("/api/settings")
async def read_settings(module: str = Query(""), day: str = Query(""), s: dict = Depends(session)):
    return _reply(await run_in_threadpool(service.read_settings, s["hotel_id"], module or None, None, day or None))


@router.get("/api/settings/pending")
async def read_pending(s: dict = Depends(session)):
    return _reply(await run_in_threadpool(service.pending, s["hotel_id"]))


@router.get("/api/settings/{setting_key}/history")
async def read_history(setting_key: str, s: dict = Depends(session)):
    return _reply(await run_in_threadpool(service.history, s["hotel_id"], setting_key))


@router.post("/api/settings")
async def save_setting(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """{setting_key, value, effective_from (YYYY-MM-DD, the operating date or later), note, sop_text}."""
    return _reply(await run_in_threadpool(service.set_setting, s["hotel_id"], s["user_id"], payload.get("setting_key"), payload.get("value"),
                                          payload.get("effective_from"), "settings", payload.get("note", ""), payload.get("sop_text")))
