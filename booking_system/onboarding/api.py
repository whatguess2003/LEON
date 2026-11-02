"""Onboarding routes. The turn and the state are public routes (a visitor has no account yet); they verify the leon_jwt token
themselves (cookie or bearer, valid, membership active at the token's access_version) and act only for that person. Uploads,
mappings, previews, commits and rollbacks need the hotel session (access.guard) and check each entity's module level."""

import urllib.parse
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from booking_system import access, db
from booking_system.onboarding import agent, attachments, files, importer, invites, sops

router = APIRouter()
PUBLIC = {"POST /api/onboarding/turn", "GET /api/onboarding/state", "GET /api/onboarding/invites/accept",
          "POST /api/onboarding/invites/accept"}
ACCESS = [
    ("POST /api/onboarding/sops", "front_desk", "manage"),
    ("POST /api/onboarding/sops/{sop_id}/confirm", "front_desk", "manage"),
    ("POST /api/onboarding/sops/{sop_id}/reject", "front_desk", "manage"),
    ("POST /api/onboarding/attachments", "front_desk", "view"),
    ("POST /api/onboarding/imports/files", "front_desk", "view"),
    ("GET /api/onboarding/imports/{batch_id}", "front_desk", "view"),
    ("PUT /api/onboarding/imports/{batch_id}/files/{file_id}", "front_desk", "view"),
    ("DELETE /api/onboarding/imports/{batch_id}/files/{file_id}", "front_desk", "view"),
    ("POST /api/onboarding/imports/{batch_id}/preview", "front_desk", "view"),
    ("POST /api/onboarding/imports/{batch_id}/commit", "front_desk", "view"),
    ("POST /api/onboarding/imports/{batch_id}/rollback", "front_desk", "view"),
    ("GET /api/onboarding/invites", "admin", "manage"),
    ("POST /api/onboarding/invites/{invite_id}/link", "admin", "manage"),
]


def _token(req):
    marker = "bearer "
    header = req.headers.get("authorization", "")
    return req.cookies.get("leon_jwt") or (header[len(marker):].strip() if header.lower().startswith(marker) else "")


def _verified(req):
    """The token's claims when it is valid and, for a hotel token, its membership is active at the token's access_version."""
    claims = access.decode(_token(req)) or {}
    if not claims.get("hotel_id"):
        return {"user_id": claims.get("user_id")} if claims.get("user_id") else {}
    member = db.transaction(lambda cur: access.membership(cur, claims["hotel_id"], claims["user_id"]))
    return claims if member and member["access_version"] == claims.get("access_version") else {}


def _cookies(req, response, token, hotel_name):
    host = req.headers.get("host", "")
    local = "localhost" in host or "127.0.0.1" in host
    attrs = {"secure": not local, "samesite": "lax" if local else "none"}
    response.set_cookie(key="leon_jwt", value=token, httponly=True, max_age=access.TOKEN_HOURS * 3600, **attrs)
    if hotel_name:
        response.set_cookie(key="leon_hotel", value=urllib.parse.quote(hotel_name, safe=""), httponly=False, **attrs)
    for index, (key, value) in enumerate(response.raw_headers):
        if key.lower() == b"set-cookie" and b"samesite=none" in value.lower() and b"partitioned" not in value.lower():
            response.raw_headers[index] = (key, value + b"; Partitioned")
    return response


def _reply(result):
    return result if result.get("ok", True) else JSONResponse(content=jsonable(result), status_code=400)


def jsonable(value):
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(value)


@router.post("/api/onboarding/turn")
async def onboarding_turn(payload: Dict[str, Any] = Body(default={}), req: Request = None):
    """{onboarding_id, message, password}. The password (typed in the page's password box) is used by the account tools only and is
    never sent to the model or stored in the transcript."""
    claims = await run_in_threadpool(_verified, req)
    result = await run_in_threadpool(agent.run_turn, str(payload.get("onboarding_id") or ""), str(payload.get("message") or ""),
                                     str(payload.get("password") or ""), claims)
    response = JSONResponse(content=jsonable(result))
    return _cookies(req, response, result["token"], result["hotel_name"]) if result["token"] else response


@router.get("/api/onboarding/state")
async def onboarding_state(onboarding_id: str = "", req: Request = None):
    claims = await run_in_threadpool(_verified, req)
    return jsonable(await run_in_threadpool(agent.read_state, onboarding_id, claims))


def _event(s, payload, text, section="", key="", summary=""):
    agent.note_event(str(payload.get("onboarding_id") or ""), s["user_id"], s["hotel_id"], text, section, key, summary)


@router.post("/api/onboarding/sops")
async def submit_sop(payload: Dict[str, Any] = Body(...), s: dict = Depends(access.session)):
    """{onboarding_id, title, text} or {onboarding_id, title, file_name, content_base64}."""
    def run():
        original = b""
        given = files.text_document(payload.get("text"))
        try:
            if payload.get("file_name"):
                original = files.decode(payload.get("content_base64"))
                given = files.read_document(payload["file_name"], original)
        except files.Unreadable as reason:
            if payload.get("file_name"):
                _event(s, payload, "The manager uploaded the SOP file " + str(payload["file_name"]) + " but it could not be read: " + str(reason)
                       + ". Nothing was stored; ask them for another format.")
            return {"ok": False, "error": str(reason)}
        source = "upload" if payload.get("file_name") else "chat"
        result = sops.submit(s["hotel_id"], s["user_id"], payload.get("onboarding_id") or None, payload.get("title") or "", source,
                             payload.get("file_name") or "", given, original)
        if result.get("ok"):
            _event(s, payload, "The manager uploaded the SOP " + result["title"] + " (" + result["sop_id"] + ", " + str(len(result["sections"]))
                   + " sections); Leon read it as: " + result["summary"] + " Sections: "
                   + "; ".join(str(item["seq"]) + ". " + (item["title"] or "untitled") + ": " + item["summary"]
                               + (" [" + ", ".join(item["setting_keys"]) + "]" if item["setting_keys"] else "") for item in result["sections"])
                   + ". Linked settings: " + "; ".join(link["setting_key"] + ": " + link["summary"] for link in result["links"])
                   + ". Nothing is saved until the manager confirms.")
        return result
    return _reply(await run_in_threadpool(run))


@router.post("/api/onboarding/sops/{sop_id}/confirm")
async def confirm_sop(sop_id: str, payload: Dict[str, Any] = Body(default={}), s: dict = Depends(access.session)):
    def run():
        result = sops.confirm(s["hotel_id"], s["user_id"], sop_id)
        if result.get("ok"):
            said = "SOP " + result["title"] + " confirmed: " + "; ".join(link["setting_key"] + " " + link["status"] for link in result["links"])
            _event(s, payload, "The manager confirmed in the panel. " + said, "sops", "sop:" + sop_id, said)
        return result
    return _reply(await run_in_threadpool(run))


@router.post("/api/onboarding/sops/{sop_id}/reject")
async def reject_sop(sop_id: str, s: dict = Depends(access.session)):
    return _reply(await run_in_threadpool(sops.reject, s["hotel_id"], sop_id))


@router.post("/api/onboarding/attachments")
async def attach_file(payload: Dict[str, Any] = Body(...), s: dict = Depends(access.session)):
    """{onboarding_id, file_name, content_base64}. The extension chooses import, SOP, or image. Leon is told in the same chat."""
    def run():
        result = attachments.receive(s["hotel_id"], s["user_id"], payload.get("onboarding_id") or None, s.get("modules"),
                                     payload.get("file_name"), payload.get("content_base64"))
        if result.get("ok"):
            _event(s, payload, "The manager attached " + str(payload.get("file_name") or "") + " in the chat. " + result.get("summary", ""))
        return result
    return _reply(await run_in_threadpool(run))


@router.post("/api/onboarding/imports/files")
async def upload_import(payload: Dict[str, Any] = Body(...), s: dict = Depends(access.session)):
    """{onboarding_id, file_name, content_base64, hint}: the file joins the session's open batch with its proposed mapping."""
    def run():
        result = importer.add_file(s["hotel_id"], s["user_id"], payload.get("onboarding_id") or None, s.get("modules"), payload.get("file_name"),
                                   payload.get("content_base64"), str(payload.get("hint") or ""))
        if result.get("ok"):
            mapped = ", ".join(field + " <- " + (item["source"] or ("'" + item["fixed"] + "'" if item["fixed"] else "nothing"))
                               for field, item in result["mapping"]["fields"].items())
            _event(s, payload, "The manager uploaded " + result["file_name"] + " (" + str(result["rows"]) + " rows) to import batch " + result["batch_id"]
                   + ", file " + result["file_id"] + ". Proposed: " + result["entity"] + "; " + mapped + ". " + result["mapping"]["explanation"])
        return result
    return _reply(await run_in_threadpool(run))


@router.get("/api/onboarding/imports/{batch_id}")
async def read_import(batch_id: str, s: dict = Depends(access.session)):
    return jsonable(await run_in_threadpool(importer.read_batch, s["hotel_id"], batch_id))


@router.put("/api/onboarding/imports/{batch_id}/files/{file_id}")
async def correct_mapping(batch_id: str, file_id: str, payload: Dict[str, Any] = Body(...), s: dict = Depends(access.session)):
    """{entity, fields: {field: {source, fixed, format}}, formats: {date_format, phone_country_code, phone_trunk_prefix},
    value_maps: [{field, source_value, value}]}."""
    return _reply(await run_in_threadpool(importer.set_mapping, s["hotel_id"], batch_id, file_id, payload.get("entity") or "",
                                          payload.get("fields") or {}, payload.get("formats") or {}, payload.get("value_maps") or []))


@router.delete("/api/onboarding/imports/{batch_id}/files/{file_id}")
async def remove_file(batch_id: str, file_id: str, s: dict = Depends(access.session)):
    return _reply(await run_in_threadpool(importer.remove_file, s["hotel_id"], batch_id, file_id))


def _counts(result):
    return "; ".join(item["file_name"] + ": " + str(item["valid_count"]) + " ready, " + str(item["error_count"]) + " with errors, "
                     + str(item["duplicate_count"]) + " duplicates" for item in result.get("files") or [])


@router.post("/api/onboarding/imports/{batch_id}/preview")
async def preview_import(batch_id: str, payload: Dict[str, Any] = Body(default={}), s: dict = Depends(access.session)):
    def run():
        result = importer.preview(s["hotel_id"], s["user_id"], batch_id)
        if result.get("ok"):
            _event(s, payload, "The manager confirmed the mapping and previewed batch " + batch_id + ": " + _counts(result))
        return result
    return _reply(await run_in_threadpool(run))


@router.post("/api/onboarding/imports/{batch_id}/commit")
async def commit_import(batch_id: str, payload: Dict[str, Any] = Body(default={}), s: dict = Depends(access.session)):
    def run():
        result = importer.commit(s["hotel_id"], s["user_id"], batch_id, s.get("modules"))
        if result.get("ok"):
            said = "import " + batch_id + " committed: " + ", ".join(str(n) + " " + e for e, n in result["created"].items())
            _event(s, payload, "The manager imported in the panel. " + said, "migration", batch_id, said)
        return result
    return _reply(await run_in_threadpool(run))


@router.post("/api/onboarding/imports/{batch_id}/rollback")
async def rollback_import(batch_id: str, payload: Dict[str, Any] = Body(default={}), s: dict = Depends(access.session)):
    def run():
        result = importer.rollback(s["hotel_id"], s["user_id"], batch_id)
        if result.get("ok"):
            said = "import " + batch_id + " rolled back: removed " + ", ".join(str(n) + " " + t for t, n in result["removed"].items())
            _event(s, payload, "The manager rolled back in the panel. " + said, "migration", batch_id + ":rollback", said)
        return result
    return _reply(await run_in_threadpool(run))


@router.get("/api/onboarding/invites")
async def list_invites(s: dict = Depends(access.session)):
    return jsonable({"ok": True, "invites": await run_in_threadpool(invites.listing, s["hotel_id"])})


@router.post("/api/onboarding/invites/{invite_id}/link")
async def invite_link(invite_id: str, s: dict = Depends(access.session)):
    """A fresh one-time link for an imported person; the previous link stops working."""
    return _reply(await run_in_threadpool(invites.reissue, s["hotel_id"], invite_id))


@router.get("/api/onboarding/invites/accept")
async def read_invite(token: str = ""):
    found = await run_in_threadpool(invites.read, token)
    return jsonable({"ok": True, "invite": found}) if found else JSONResponse(
        content={"ok": False, "error": "this invite link is not valid any more; ask your manager for a new one"}, status_code=400)


@router.post("/api/onboarding/invites/accept")
async def accept_invite(payload: Dict[str, Any] = Body(...), req: Request = None):
    """{token, password}: sets the password, activates the membership and signs the person in."""
    result = await run_in_threadpool(invites.accept, str(payload.get("token") or ""), str(payload.get("password") or ""))
    if not result.get("ok"):
        return _reply(result)
    response = JSONResponse(content=jsonable({"ok": True, "token": result["token"], "hotel_id": result["hotel_id"],
                                              "hotel_name": result["hotel_name"]}))
    return _cookies(req, response, result["token"], result["hotel_name"])
