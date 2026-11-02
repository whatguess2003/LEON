"""Guest desk routes: the message thread and desk brief, approvals and tasks, WhatsApp text, media and templates, and the guest's
own biography page (opened from a signed link). The hotel is the session's hotel_id."""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from booking_system import guest_manager as gm
from booking_system.access import session

router = APIRouter()

ACCESS = [
    ("GET /api/guests/thread", "front_desk", "view"), ("POST /api/guests/approval", "front_desk", "operate"),
    ("POST /api/guests/approval/clear", "front_desk", "operate"), ("POST /api/guests/tasks/done", "front_desk", "operate"),
    ("POST /api/guests/whatsapp", "front_desk", "operate"), ("POST /api/guests/whatsapp/simulate", "front_desk", "operate"),
    ("POST /api/guests/whatsapp/media", "front_desk", "operate"), ("GET /api/guests/whatsapp/media", "front_desk", "view"),
    ("GET /api/guests/whatsapp/templates", "front_desk", "view"), ("POST /api/guests/whatsapp/template", "front_desk", "operate"),
    ("POST /api/guests/messages", "front_desk", "operate"), ("POST /api/guests/summarize", "front_desk", "view"),
]
PUBLIC = ("GET /api/guest/biography", "POST /api/guest/biography", "GET /api/guest/onboard", "POST /api/guest/onboard/turn")
BIOGRAPHY = ("guest_name", "email", "country", "date_of_birth", "gender", "company", "address", "identity_type", "identity_number",
             "special_request")


def _error(text, status):
    return JSONResponse(content={"error": text}, status_code=status)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _guest(hotel_id, guest_phone):
    """The active guest of this hotel with this phone (columns plus document fields), or None."""
    guest = gm.read_guest(hotel_id, str(guest_phone or "").strip())
    return guest if guest and guest["status"] == "active" else None


def write_guest_needs(hotel_id, guest_phone):
    """Read the guest and save what the front desk needs to know; Leon runs the actions the hotel allows."""
    if not os.getenv("OPENAI_API_KEY"):
        return {"error": "ChatGPT is unavailable", "status": 503}
    from openai import OpenAI
    from booking_system.desk_actions import hotel_names, resolve_hotel
    from booking_system.guest_summary import summarize_guest
    from booking_system.hotel_registry import read_hotel_by_id
    from booking_system.leon_turn import finish_guest, prepare_guest
    hotel = read_hotel_by_id(hotel_id)
    record = gm.read_guest_with_bookings(hotel_id, guest_phone, hotel["timezone"]) if hotel else None
    if not record:
        return {"error": "Guest profile not found", "status": 404}
    record["hotel_name"] = hotel["hotel_name"]
    prepared = prepare_guest(guest_phone)
    guest = record["guest"]
    if prepared["stage"].get("ambiguous"):
        ask = resolve_hotel(prepared["stage"], False, "", hotel_names()).get("ask") or ""
        gm.write_guest_document(hotel_id, guest_phone, {"guest_reply": ask})
        return {"guest_phone": guest_phone, "guest_reply": ask, **{name: guest.get(name) or "" for name in
                ("guest_profile_summary", "guest_needs", "recommended_action")}}
    record["stage_text"] = prepared["stage_text"]
    record["phone_messages"] = prepared["phone_messages"]
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    try:
        brief = summarize_guest(client, record, gm.read_guest_media)
        brief = finish_guest(client, guest_phone, brief)["brief"]
    except Exception as model_error:
        detail = str(model_error)
        if "insufficient_quota" in detail:
            return {"error": "ChatGPT has no credits remaining", "status": 429}
        return {"error": detail, "status": 502}
    if not all(brief.get(name) for name in ("guest_profile_summary", "guest_needs", "recommended_action")):
        return {"error": "ChatGPT returned an empty summary", "status": 502}
    saved = {name: brief[name] for name in ("guest_profile_summary", "guest_needs", "recommended_action")}
    saved.update(ai_summary_at=_now(), ai_summary_for="brief", guest_reply=brief.get("guest_reply") or "")
    gm.write_guest_document(hotel_id, guest_phone, saved)
    return {"guest_phone": guest_phone, **saved}


def _answer_simulated(hotel_id, guest_phone):
    """Write the brief, then save the assistant reply on the thread. Meta is not called."""
    written = write_guest_needs(hotel_id, guest_phone)
    reply = str(written.get("guest_reply") or "").strip()
    if written.get("error") or not reply:
        return written
    sent_at = _now()
    gm.save_whatsapp_event("assistant-" + uuid.uuid4().hex, guest_phone, hotel_id, sent_at, reply, "out", "assistant")
    gm.write_guest_document(hotel_id, guest_phone, {"ai_summary_at": sent_at})
    return dict(written, ai_summary_at=sent_at)


@router.get("/api/guests/thread")
def guests_thread(guest_phone: str = Query(...), s: dict = Depends(session)):
    """Messages, the saved desk brief, held approvals and open tasks for the open guest."""
    from booking_system.desk_actions import action_names, approval_names
    guest = _guest(s["hotel_id"], guest_phone)
    if not guest:
        return _error("Guest profile not found", 404)
    messages = gm.list_guest_messages(s["hotel_id"], guest["guest_phone"])
    tasks = [{"task": row["request"], "request_id": row["request_id"], "status": "open"} for row in gm.open_requests(s["hotel_id"], guest["guest_id"])]
    return {
        "guest_phone": guest["guest_phone"],
        "messages": messages,
        "whatsapp_window": gm.whatsapp_text_window(messages),
        **{name: guest.get(name) or "" for name in ("guest_profile_summary", "guest_needs", "recommended_action", "ai_summary_at", "ai_summary_for")},
        "executable_actions": guest.get("executable_actions") or [],
        "approval_actions": guest.get("approval_actions") or [],
        "desk_tasks": tasks,
        "desk_require_approval": approval_names(s["hotel_id"]),
        "executional_names": action_names(),
    }


@router.post("/api/guests/approval")
def guests_approval(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Remember whether one desk action waits for a person at this hotel."""
    from booking_system.desk_actions import save_approval
    saved = save_approval(s["hotel_id"], s["user_id"], payload.get("action"), bool(payload.get("require_approval")))
    if saved == "unknown":
        return _error("Unknown action", 400)
    return {"hotel_name": s["hotel_name"], "desk_require_approval": saved}


@router.post("/api/guests/approval/clear")
def guests_approval_clear(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Remove one held action after the desk approved it."""
    from booking_system.desk_actions import drop_approval
    if not _guest(s["hotel_id"], payload.get("guest_phone")):
        return _error("Guest profile not found", 404)
    if drop_approval(s["hotel_id"], str(payload.get("guest_phone") or "").strip(), payload.get("label")) == "missing":
        return _error("Approval not found", 404)
    return {"success": True}


@router.post("/api/guests/tasks/done")
def guests_task_done(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Mark one open task (by text or request_id) done."""
    from booking_system.desk_actions import mark_task_done
    if not _guest(s["hotel_id"], payload.get("guest_phone")):
        return _error("Guest profile not found", 404)
    if mark_task_done(s["hotel_id"], s["user_id"], str(payload.get("guest_phone") or "").strip(), payload.get("task")) == "missing":
        return _error("Task not found", 404)
    return {"success": True}


def _open_guest(s, guest_phone):
    """(guest, None) when the guest exists and free text is open; else (None, error response)."""
    guest = _guest(s["hotel_id"], guest_phone)
    if not guest:
        return None, _error("Guest profile not found", 404)
    if not gm.whatsapp_text_window(gm.list_guest_messages(s["hotel_id"], guest["guest_phone"]))["open"]:
        return None, _error("Free text is closed", 400)
    return guest, None


@router.post("/api/guests/whatsapp")
def guests_whatsapp(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Send one WhatsApp text to a guest and save it on the thread."""
    from booking_system.front_desk_manager import intelligent_front_desk
    text = str(payload.get("body") or "").strip()
    if not payload.get("guest_phone") or not text:
        return _error("guest_phone and body are required", 400)
    guest, denied = _open_guest(s, payload["guest_phone"])
    if denied:
        return denied
    sent = intelligent_front_desk.send_whatsapp_text(guest["guest_phone"], text)
    if sent.get("error"):
        return _error(sent["error"], 400)
    sent_at = _now()
    gm.save_whatsapp_event(sent["message_id"], guest["guest_phone"], s["hotel_id"], sent_at, text, "out")
    return {"success": True, "guest_phone": guest["guest_phone"], "body": text, "sent_at": sent_at, "source_ref": sent["message_id"],
            "direction": "out", "channel": "whatsapp"}


@router.post("/api/guests/whatsapp/simulate")
async def guests_whatsapp_simulate(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Save an inbound WhatsApp message on the guest without Meta, then answer it."""
    text = str(payload.get("body") or "").strip()
    if not payload.get("guest_phone") or not text:
        return _error("guest_phone and body are required", 400)
    guest = await run_in_threadpool(_guest, s["hotel_id"], payload["guest_phone"])
    if not guest:
        return _error("Guest profile not found", 404)
    sent_at = _now()
    source_ref = "sim-" + uuid.uuid4().hex
    await run_in_threadpool(gm.save_whatsapp_event, source_ref, guest["guest_phone"], s["hotel_id"], sent_at, text, "in", "simulated")
    written = await run_in_threadpool(_answer_simulated, s["hotel_id"], guest["guest_phone"])
    saved = {"success": True, "guest_phone": guest["guest_phone"], "body": text, "sent_at": sent_at, "source_ref": source_ref,
             "direction": "in", "channel": "whatsapp", "status": "simulated"}
    if written.get("error"):
        return dict(saved, reply_error=written["error"])
    return dict(saved, guest_reply=written.get("guest_reply") or "", executable_actions=written.get("executable_actions") or [])


@router.post("/api/guests/whatsapp/media")
async def guests_whatsapp_media(guest_phone: str = Form(""), caption: str = Form(""), file: UploadFile = File(...),
                                s: dict = Depends(session)):
    """Upload a file to WhatsApp and send it. Free text must be open."""
    from booking_system.front_desk_manager import intelligent_front_desk
    from booking_system.whatsapp_media import upload_media
    if not guest_phone:
        return _error("guest_phone is required", 400)
    guest, denied = await run_in_threadpool(_open_guest, s, guest_phone)
    if denied:
        return denied
    data = await file.read()
    uploaded = await run_in_threadpool(upload_media, file.filename or "file", file.content_type or "", data)
    if uploaded.get("error"):
        return _error(uploaded["error"], 400)
    text = str(caption or "").strip()
    sent = await run_in_threadpool(intelligent_front_desk.send_whatsapp_media, guest["guest_phone"], uploaded["kind"], uploaded["id"], text)
    if sent.get("error"):
        return _error(sent["error"], 400)
    sent_at = _now()
    body = text or uploaded["filename"]
    await run_in_threadpool(gm.save_whatsapp_event, sent["message_id"], guest["guest_phone"], s["hotel_id"], sent_at, body, "out", "",
                            uploaded["mime"], uploaded["filename"])
    await run_in_threadpool(gm.save_guest_media, sent["message_id"], data, uploaded["mime"], uploaded["filename"])
    return {"success": True, "guest_phone": guest["guest_phone"], "body": body, "sent_at": sent_at, "source_ref": sent["message_id"],
            "direction": "out", "channel": "whatsapp", "media_mime": uploaded["mime"], "media_name": uploaded["filename"]}


@router.get("/api/guests/whatsapp/media")
def guests_whatsapp_media_file(source_ref: str = Query(""), guest_phone: str = Query(""), s: dict = Depends(session)):
    """The file saved for one WhatsApp message of this hotel's guest."""
    phone, ref = str(guest_phone or "").strip(), str(source_ref or "").strip()
    if not phone or not ref:
        return _error("guest_phone and source_ref are required", 400)
    row = gm.whatsapp_message(s["hotel_id"], ref)
    if not row or row["guest_phone"] != phone:
        return _error("Message not found", 404)
    stored = gm.read_guest_media(ref)
    if not stored:
        return _error("File not found", 404)
    filename = "".join(ch for ch in str(stored.get("filename") or "file") if ch not in "\"\r\n\\") or "file"
    return Response(content=stored["data"], media_type=stored["mime"],
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)})


@router.get("/api/guests/whatsapp/templates")
def guests_whatsapp_templates(guest_phone: str = Query(""), s: dict = Depends(session)):
    """Active WhatsApp templates with the guest and hotel names filled into the sentence."""
    from booking_system.whatsapp_templates import active_templates
    if not guest_phone:
        return _error("guest_phone is required", 400)
    guest = _guest(s["hotel_id"], guest_phone)
    if not guest:
        return _error("Guest profile not found", 404)
    rows = active_templates({"guest_name": guest["guest_name"], "hotel_name": s["hotel_name"]})
    if isinstance(rows, dict):
        return _error(rows.get("error") or "WhatsApp templates are not available", 400)
    return {"templates": rows}


@router.post("/api/guests/whatsapp/template")
def guests_whatsapp_template(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Send the template the staff selected. Fields are filled from the guest, not from typed text."""
    from booking_system.front_desk_manager import intelligent_front_desk
    from booking_system.guest_link import guest_link
    from booking_system.whatsapp_templates import render_template
    template_key = str(payload.get("template") or "").strip()
    if not payload.get("guest_phone") or not template_key:
        return _error("guest_phone and template are required", 400)
    guest = _guest(s["hotel_id"], payload["guest_phone"])
    if not guest:
        return _error("Guest profile not found", 404)
    phone = guest["guest_phone"]
    rendered = render_template(template_key, {"guest_name": guest["guest_name"], "hotel_name": s["hotel_name"],
                                              "guest_link": guest_link(s["hotel_id"], phone)})
    if rendered.get("error"):
        return _error(rendered["error"], 400)
    sent = intelligent_front_desk.send_whatsapp_template(phone, rendered["template_name"], rendered["language"], rendered["values"],
                                                         rendered["button"], rendered["sentence"], rendered.get("names"), rendered.get("named"))
    if sent.get("error"):
        return _error(sent["error"], 400)
    sent_at = _now()
    gm.save_whatsapp_event(sent["message_id"], phone, s["hotel_id"], sent_at, sent["body"], "out")
    return {"success": True, "guest_phone": phone, "body": sent["body"], "sent_at": sent_at, "source_ref": sent["message_id"],
            "direction": "out", "channel": "whatsapp"}


@router.post("/api/guests/messages")
def guests_message(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Save one guest message, keyed by channel and source_ref (a desk-entered message without one gets a new ref)."""
    channel = str(payload.get("channel") or "").strip().lower()
    direction = str(payload.get("direction") or "").strip().lower()
    sent_at = str(payload.get("sent_at") or "").strip()
    body = str(payload.get("body") or "")
    if channel not in ("email", "whatsapp", "voice"):
        return _error("channel must be email, whatsapp, or voice", 400)
    if direction not in ("in", "out"):
        return _error("direction must be in or out", 400)
    if not payload.get("guest_phone") or not sent_at or not body:
        return _error("guest_phone, sent_at, and body are required", 400)
    guest = _guest(s["hotel_id"], payload["guest_phone"])
    if not guest:
        return _error("Guest profile not found", 404)
    source_ref = str(payload.get("source_ref") or "").strip() or "desk-" + uuid.uuid4().hex
    try:
        result = gm.save_guest_message(s["hotel_id"], guest["guest_phone"], channel, sent_at, body, direction, source_ref)
    except ValueError:
        return _error("sent_at must be an ISO date-time", 400)
    if isinstance(result, dict) and result.get("error"):
        return _error(result["error"], 400)
    return {"result": result, "guest_phone": guest["guest_phone"], "channel": channel, "sent_at": sent_at, "source_ref": source_ref}


@router.post("/api/guests/summarize")
async def guests_summarize(payload: Dict[str, Any] = Body(...), s: dict = Depends(session)):
    """Write a guest summary from stays, messages, and saved files."""
    phone = str(payload.get("guest_phone") or "").strip()
    if not phone:
        return _error("guest_phone is required", 400)
    written = await run_in_threadpool(write_guest_needs, s["hotel_id"], phone)
    if written.get("error"):
        return _error(written["error"], written.get("status") or 502)
    return written


def _guest_from_link(token):
    from booking_system.guest_link import read_guest_link
    opened = read_guest_link(token)
    if opened.get("error"):
        return None, None, _error(opened["error"], 400)
    guest = _guest(opened["hotel_id"], opened["guest_phone"])
    if not guest:
        return None, None, _error("Guest profile not found", 404)
    return opened["hotel_id"], guest, None


@router.get("/api/guest/biography")
def guest_biography(token: str = Query("")):
    """Biography for the guest who opened the WhatsApp verify link."""
    from booking_system.hotel_registry import read_hotel_by_id
    hotel_id, guest, denied = _guest_from_link(token)
    if denied:
        return denied
    record = {name: guest.get(name) or "" for name in BIOGRAPHY}
    record.update(guest_phone=guest["guest_phone"], email=guest["guest_email"], nationality=guest.get("nationality") or "",
                  hotel_name=read_hotel_by_id(hotel_id)["hotel_name"])
    record["country"] = record["country"] or record["nationality"]
    return record


@router.post("/api/guest/biography")
def guest_biography_save(payload: Dict[str, Any] = Body(...)):
    """Save the biography the guest typed. The link is the login."""
    from booking_system.front_desk_manager import intelligent_front_desk
    hotel_id, guest, denied = _guest_from_link(str(payload.get("token") or ""))
    if denied:
        return denied
    fields = {name: str(payload.get(name) or "").strip() for name in BIOGRAPHY if name in payload}
    if "guest_name" in fields and not fields["guest_name"]:
        return _error("Name is required", 400)
    if fields.get("country"):
        fields["nationality"] = fields["country"]
    saved = gm.update_guest_profile(hotel_id, None, guest["guest_phone"], fields)
    if saved.get("error"):
        return _error(saved["error"], 400)
    return {"success": True, "whatsapp_url": intelligent_front_desk.whatsapp_click_url(fields.get("guest_name") or guest["guest_name"])}


@router.get("/api/guest/onboard")
def guest_onboard_open(token: str = Query("")):
    """Ella's opening for the guest who opened the portal link."""
    from booking_system.guest_onboard import guest_open
    opened = guest_open(token)
    if opened.get("error"):
        return _error(opened["error"], 400)
    return opened


@router.post("/api/guest/onboard/turn")
async def guest_onboard_turn(payload: Dict[str, Any] = Body(default={})):
    """One chat turn with Ella. The link token is the guest's login."""
    from booking_system.guest_onboard import guest_turn
    body = payload or {}
    result = await run_in_threadpool(guest_turn, str(body.get("token") or ""), str(body.get("message") or ""), body.get("history") or [])
    if result.get("error") and not result.get("reply"):
        return _error(result["error"], 400)
    return result
