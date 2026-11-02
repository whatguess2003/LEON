"""Platform-team routes for desk complaints. Hotel users never reach these: every route needs the platform admin token."""

import hmac
import os
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field


class LabelKind(BaseModel):
    label: str
    description: str = ""
    answer_correct: bool
    system_flaw: bool = False
    needs_cause: bool = False
    desired_trend: int = Field(0, ge=-1, le=1)


class LabelCause(BaseModel):
    label: str
    cause: str
    description: str = ""


class LabelTarget(BaseModel):
    label: str
    expected_share: float = Field(..., ge=0, le=1)
    effective_from: date
    note: str = ""


class AnswerLabel(BaseModel):
    label: str
    cause: Optional[str] = None
    note: str = ""
    catalog_id: Optional[str] = None


def require_platform_admin(x_leon_admin_token: str = Header(""), x_leon_admin_user: str = Header("")):
    """The resolver's name when the token is the platform admin token; refused otherwise, and always when none is configured."""
    expected = os.getenv("LEON_PLATFORM_ADMIN_TOKEN") or ""
    if not expected or not hmac.compare_digest(str(x_leon_admin_token), expected):
        raise HTTPException(status_code=403, detail="platform admin only")
    if not str(x_leon_admin_user).strip():
        raise HTTPException(status_code=400, detail="X-Leon-Admin-User is required")
    return str(x_leon_admin_user).strip()


router = APIRouter(prefix="/api/admin/complaints", tags=["admin-complaints"])


def _result(found):
    if found.get("error"):
        raise HTTPException(status_code=409, detail=found["error"])
    return found


@router.get("")
async def complaints(status: str = "open", admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.complaints import STATUSES, list_complaints

    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="status must be one of " + ", ".join(STATUSES))
    return {"complaints": await run_in_threadpool(list_complaints, status)}


@router.post("/{complaint_id}/republish")
async def republish_recipe(complaint_id: str, payload: dict = Body(...), admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.complaints import republish

    return _result(await run_in_threadpool(
        republish, complaint_id, str(payload.get("catalog_id") or ""), payload.get("body") or {}, payload.get("note"), admin,
    ))


@router.post("/{complaint_id}/retire")
async def retire_recipe(complaint_id: str, payload: dict = Body(...), admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.complaints import retire

    return _result(await run_in_threadpool(retire, complaint_id, str(payload.get("catalog_id") or ""), payload.get("note"), admin))


@router.post("/{complaint_id}/close")
async def close_complaint(complaint_id: str, payload: dict = Body(...), admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.complaints import close

    return _result(await run_in_threadpool(close, complaint_id, str(payload.get("status") or ""), payload.get("note"), admin))


@router.get("/study")
async def study(status: str = "open", admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.answer_labels import study_complaints
    from booking_system.semantic.complaints import STATUSES

    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="status must be one of " + ", ".join(STATUSES))
    return {"complaints": await run_in_threadpool(study_complaints, status)}


@router.get("/health")
async def health(period: str = "week", since: Optional[datetime] = None, until: Optional[datetime] = None,
                 admin: str = Depends(require_platform_admin)):
    """Label shares. flawed is a flaw label above its stored flaw threshold; still_fixing is that label above its expected share."""
    from booking_system.semantic.answer_labels import health_report

    found = await run_in_threadpool(health_report, period, since, until)
    if found.get("error"):
        raise HTTPException(status_code=400, detail=found["error"])
    return found


@router.get("/taxonomy")
async def label_taxonomy(admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.answer_labels import taxonomy

    return {"labels": await run_in_threadpool(taxonomy)}


@router.post("/taxonomy/labels")
async def add_label_kind(item: LabelKind, admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.answer_labels import add_kind

    return _result(await run_in_threadpool(
        add_kind, item.label, item.description, item.answer_correct, item.system_flaw, item.needs_cause, item.desired_trend, admin,
    ))


@router.post("/taxonomy/causes")
async def add_label_cause(item: LabelCause, admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.answer_labels import add_cause

    return _result(await run_in_threadpool(add_cause, item.label, item.cause, item.description, admin))


@router.post("/taxonomy/targets")
async def add_label_target(item: LabelTarget, admin: str = Depends(require_platform_admin)):
    from booking_system.semantic.answer_labels import set_target

    return _result(await run_in_threadpool(set_target, item.label, item.expected_share, item.effective_from, item.note, admin))


async def _label(subject, key, item, admin):
    from booking_system.semantic.answer_labels import label_answer

    return _result(await run_in_threadpool(label_answer, subject, key, item.label, item.cause, item.note, item.catalog_id, admin))


async def _history(subject, key):
    from booking_system.semantic.answer_labels import label_history

    return {"labels": await run_in_threadpool(label_history, subject, key)}


@router.post("/{complaint_id}/label")
async def label_complaint(complaint_id: str, item: AnswerLabel, admin: str = Depends(require_platform_admin)):
    return await _label("complaint", complaint_id, item, admin)


@router.get("/{complaint_id}/labels")
async def complaint_labels(complaint_id: str, admin: str = Depends(require_platform_admin)):
    return await _history("complaint", complaint_id)


@router.post("/runs/{run_id}/label")
async def label_run(run_id: int, item: AnswerLabel, admin: str = Depends(require_platform_admin)):
    return await _label("run", run_id, item, admin)


@router.get("/runs/{run_id}/labels")
async def run_labels(run_id: int, admin: str = Depends(require_platform_admin)):
    return await _history("run", run_id)


@router.post("/tasks/{task_id}/label")
async def label_task(task_id: int, item: AnswerLabel, admin: str = Depends(require_platform_admin)):
    return await _label("task", task_id, item, admin)


@router.get("/tasks/{task_id}/labels")
async def task_labels(task_id: int, admin: str = Depends(require_platform_admin)):
    return await _history("task", task_id)
