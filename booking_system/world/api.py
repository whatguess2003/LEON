"""World Mode routes. Every point is registered with the access guard (module + level); the handler takes the hotel from the session.

Reads are front_desk.view like the classic Stay View; media upload, retirement and scene publishing are admin.manage.
There is no write route for PMS state here: POST /api/world/actions/prepare only stores a pending approval card through the existing
action recipe flow, and the existing POST /api/leon/actions/{approval_id}/approve runs it."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from booking_system.access import session
from booking_system.world import service

router = APIRouter()

ACCESS = [
    ("GET /api/world/config", "front_desk", "view"),
    ("GET /api/world/calendar", "front_desk", "view"),
    ("GET /api/world/reservations/{booking_id}/presentation", "front_desk", "view"),
    ("GET /api/world/rooms/{room_number}/presentation", "front_desk", "view"),
    ("GET /api/world/media", "front_desk", "view"),
    ("GET /api/world/media/{media_id}", "front_desk", "view"),
    ("POST /api/world/media", "admin", "manage"),
    ("POST /api/world/media/{media_id}/retire", "admin", "manage"),
    ("GET /api/world/manifest", "front_desk", "view"),
    ("POST /api/world/scenes", "admin", "manage"),
    ("POST /api/world/scenes/{scene_id}/rollback", "admin", "manage"),
    ("POST /api/world/actions/prepare", "front_desk", "view"),
]


def _call(fn, *args, gate=True, **kwargs):
    try:
        if gate:
            service.require_enabled(args[0])
        return JSONResponse(content=fn(*args, **kwargs))
    except service.WorldError as refusal:
        return JSONResponse(status_code=refusal.status, content={"state": refusal.state, "error": refusal.message})


@router.get("/api/world/config")
def world_config(s: dict = Depends(session)):
    return _call(service.config, s, gate=False)


@router.get("/api/world/calendar")
def world_calendar(start_date: str = Query(...), end_date: str = Query(...), s: dict = Depends(session)):
    return _call(service.calendar, s, start_date, end_date)


@router.get("/api/world/reservations/{booking_id}/presentation")
def world_reservation(booking_id: str, stay_date: Optional[str] = Query(None), s: dict = Depends(session)):
    return _call(service.reservation, s, booking_id, stay_date)


@router.get("/api/world/rooms/{room_number}/presentation")
def world_room(room_number: str, s: dict = Depends(session)):
    return _call(service.room, s, room_number)


@router.get("/api/world/media")
def world_media_list(binding_kind: Optional[str] = Query(None), binding_ref: Optional[str] = Query(None), s: dict = Depends(session)):
    return _call(service.list_media, s, binding_kind, (binding_ref or "").strip().upper() or None)


@router.get("/api/world/media/{media_id}")
def world_media(media_id: str, variant: str = Query("full"), s: dict = Depends(session)):
    try:
        service.require_enabled(s)
        mime, data, tag = service.media_bytes(s, media_id, variant)
    except service.WorldError as refusal:
        return JSONResponse(status_code=refusal.status, content={"state": refusal.state, "error": refusal.message})
    # Private: hotel media is served to the signed-in member only and never to a shared cache.
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=3600", "ETag": '"' + tag + '"',
                                                          "X-Content-Type-Options": "nosniff"})


@router.post("/api/world/media")
async def world_media_add(binding_kind: str = Form(...), binding_ref: str = Form(...), caption: str = Form(""),
                          authentic: str = Form(""), photo: UploadFile = File(...), thumb: Optional[UploadFile] = File(None),
                          s: dict = Depends(session)):
    full = await photo.read(service.media_check.MAX_FULL_BYTES + 1)
    small = await thumb.read(service.media_check.MAX_THUMB_BYTES + 1) if thumb is not None else None
    return _call(service.add_media, s, binding_kind, binding_ref, caption, authentic.lower() in ("1", "true", "yes", "on"), full, small)


@router.post("/api/world/media/{media_id}/retire")
def world_media_retire(media_id: str, s: dict = Depends(session)):
    return _call(service.retire_media, s, media_id)


@router.get("/api/world/manifest")
def world_manifest(scene: str = Query(...), s: dict = Depends(session)):
    return _call(service.active_manifest, s, scene)


@router.post("/api/world/scenes")
def world_scene_publish(manifest: dict = Body(...), s: dict = Depends(session)):
    return _call(service.publish_manifest, s, manifest)


class RollbackPayload(BaseModel):
    scene_version: int


@router.post("/api/world/scenes/{scene_id}/rollback")
def world_scene_rollback(scene_id: str, payload: RollbackPayload, s: dict = Depends(session)):
    return _call(service.rollback_manifest, s, scene_id, payload.scene_version)


class PreparePayload(BaseModel):
    intent: str
    booking_id: Optional[str] = None
    room_number: Optional[str] = None
    new_status: Optional[str] = None


@router.post("/api/world/actions/prepare")
def world_prepare(payload: PreparePayload, s: dict = Depends(session)):
    return _call(service.prepare, s, payload.intent, payload.booking_id, payload.room_number, payload.new_status)
