"""World Mode integration tests against a running LEON server and its LOCAL development database.

Opt-in, because they create two test logins and a second test hotel, and change a sandbox room's readiness:

    LEON_WORLD_IT=1 DATABASE_URL=postgresql://...localhost.../leon JWT_SECRET=<the server's> \
        python -m pytest tests/world/test_world_integration.py -q

Needs: the server running with WORLD_MODE_ENABLED=all (LEON_TEST_API, default http://localhost:8002), the sandbox hotel
(python -m fixtures.sandbox seed) and the action recipes published (booking_system.semantic.seed_general). They refuse to run when
DATABASE_URL is not on this machine unless LEON_WORLD_IT_ALLOW_REMOTE=1."""

import os
import struct
import zlib
from datetime import date, timedelta
from urllib.parse import urlparse

import pytest

if os.getenv("LEON_WORLD_IT") != "1":
    pytest.skip("integration tests are opt-in (LEON_WORLD_IT=1)", allow_module_level=True)
_host = urlparse(os.getenv("DATABASE_URL", "")).hostname or ""
if _host not in ("localhost", "127.0.0.1", "::1") and os.getenv("LEON_WORLD_IT_ALLOW_REMOTE") != "1":
    pytest.skip("DATABASE_URL is not local (" + _host + "); refusing to write test data there", allow_module_level=True)

import httpx  # noqa: E402

API = os.getenv("LEON_TEST_API", "http://localhost:8002")
PASSWORD = "world-test-pass-2026"


def _signin(username, password=PASSWORD):
    r = httpx.post(API + "/api/auth/signin", json={"auth_type": "username", "auth_value": username, "password": password}, timeout=30)
    return (r.json() or {}).get("token")


class Client:
    def __init__(self, token):
        self.h = {"Authorization": "Bearer " + token}

    def get(self, path, **params):
        return httpx.get(API + path, params=params, headers=self.h, timeout=60)

    def post(self, path, json=None, **kw):
        return httpx.post(API + path, json=json, headers=self.h, timeout=60, **kw)


@pytest.fixture(scope="module")
def manager():
    from fixtures.sandbox import PASSWORD as SANDBOX_PASSWORD
    token = _signin("sandbox_manager", SANDBOX_PASSWORD)
    assert token, "sign-in to the sandbox hotel failed; seed it with python -m fixtures.sandbox seed"
    return Client(token)


@pytest.fixture(scope="module")
def housekeeper(manager):
    """A HOUSEKEEPING member of the sandbox hotel (front_desk view, reservations none, housekeeping operate)."""
    token = _signin("world_test_housekeeper")
    if not token:
        from booking_system import user_manager
        from booking_system.hotel_registry import read_hotel
        hotel = read_hotel("SANDBOX HOTEL")
        added = user_manager.add_user(hotel["hotel_id"], None, "World Test Housekeeper", {"username": "world_test_housekeeper"}, "HOUSEKEEPING",
                                      PASSWORD, "en", None)
        assert not added.get("error"), added
        token = _signin("world_test_housekeeper")
    return Client(token)


@pytest.fixture(scope="module")
def other_hotel():
    """A general manager of a second, empty test hotel."""
    token = _signin("world_test_other_gm")
    if not token:
        from booking_system import user_manager
        created = user_manager.signup("World Test Other GM", {"username": "world_test_other_gm"}, PASSWORD, "en", None)
        assert not created.get("error"), created
        hotel = user_manager.create_hotel(created["user_id"], "WORLD TEST OTHER HOTEL", "MYR", "Asia/Kuala_Lumpur", "Kuala Lumpur")
        assert not hotel.get("error"), hotel
        token = _signin("world_test_other_gm")
    return Client(token)


@pytest.fixture(scope="module")
def window(manager):
    cfg = manager.get("/api/world/config").json()
    assert cfg["enabled"], "start the server with WORLD_MODE_ENABLED=all"
    today = date.fromisoformat(cfg["today"])
    return (today - timedelta(days=3)).isoformat(), (today + timedelta(days=10)).isoformat(), cfg["today"]


def _png(width=320, height=200):
    raw = b"".join(b"\x00" + b"\x20\x60\xa0" * width for _ in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


# ---------- correctness: same truth as the classic Stay View ----------

def test_world_spans_cover_exactly_the_classic_calendar_nights(manager, window):
    start, end, _ = window
    classic = manager.get("/api/assignments/calendar", start_date=start, end_date=end).json()
    world = manager.get("/api/world/calendar", start_date=start, end_date=end).json()
    assert world["state"] == "ok"
    classic_nights = set()
    for room in classic["rooms"]:
        for day, entry in room["entries"].items():
            if start <= day <= end and entry.get("status") == "assigned" and entry.get("booking_id"):
                classic_nights.add((entry["booking_id"], room["room_number"], day))
    world_nights = set()
    for span in world["stays"]:
        day = date.fromisoformat(span["first_night"])
        while day.isoformat() <= span["last_night"]:
            if start <= day.isoformat() <= end:
                world_nights.add((span["booking_id"], span["room_number"], day.isoformat()))
            day += timedelta(days=1)
    assert world_nights == classic_nights
    assert [r["room_number"] for r in world["rooms"]] == [r["room_number"] for r in classic["rooms"]]


def test_reservation_panel_matches_the_classic_booking_detail(manager, window):
    start, end, today = window
    world = manager.get("/api/world/calendar", start_date=start, end_date=end).json()
    for span in world["stays"][:6]:
        stay_date = max(span["first_night"], start)
        mine = manager.get("/api/world/reservations/" + span["booking_id"] + "/presentation", stay_date=stay_date).json()
        classic = manager.get("/api/assignments/detail", booking_id=span["booking_id"], stay_date=stay_date).json()
        assert (mine["check_in_date"], mine["check_out_date"], mine["booking_status"]) == (
            classic["check_in_date"], classic["check_out_date"], classic["booking"]["booking_status"])
        assert mine["guest"]["name"] == classic["guest"]["guest_name"]
        assert mine["payment"]["balance_due"] == classic["payment"]["remaining_balance"]
        assert [r["room_number"] for r in mine["rooms_tonight"]] == classic["checkin"]["today_rooms"]


def test_window_limits(manager):
    assert manager.get("/api/world/calendar", start_date="2026-01-10", end_date="2026-01-01").status_code == 400
    assert manager.get("/api/world/calendar", start_date="2026-01-01", end_date="2026-06-01").status_code == 400
    assert manager.get("/api/world/calendar", start_date="nope", end_date="2026-01-01").status_code == 400


# ---------- security: hotel isolation and role trimming ----------

def test_another_hotel_cannot_see_or_act_on_this_hotels_records(manager, other_hotel, window):
    start, end, _ = window
    span = manager.get("/api/world/calendar", start_date=start, end_date=end).json()["stays"][0]
    assert other_hotel.get("/api/world/reservations/" + span["booking_id"] + "/presentation").status_code == 404
    assert other_hotel.get("/api/world/rooms/" + span["room_number"] + "/presentation").status_code == 404
    theirs = other_hotel.get("/api/world/calendar", start_date=start, end_date=end).json()
    assert theirs["stays"] == [] and theirs["rooms"] == []
    refused = other_hotel.post("/api/world/actions/prepare", {"intent": "check_out", "booking_id": span["booking_id"], "room_number": span["room_number"]})
    assert refused.status_code in (403, 409) and refused.json()["state"] in ("forbidden", "refused")
    # A caller-supplied hotel id is never trusted: the guard refuses one that is not the session's.
    assert manager.get("/api/world/calendar", start_date=start, end_date=end, hotel_id="htl_someone_else").status_code == 403


def test_housekeeping_sees_operations_but_not_guest_contact_or_money(housekeeper, manager, window):
    start, end, _ = window
    cal = housekeeper.get("/api/world/calendar", start_date=start, end_date=end).json()
    assert cal["redacted"]["guest_names"] is True
    full = {s["booking_id"]: s["guest_name"] for s in manager.get("/api/world/calendar", start_date=start, end_date=end).json()["stays"]}
    for span in cal["stays"]:
        assert span["guest_name"] != full[span["booking_id"]] and span["guest_name"].endswith(".")
    res = housekeeper.get("/api/world/reservations/" + cal["stays"][0]["booking_id"] + "/presentation", stay_date=max(cal["stays"][0]["first_night"], start)).json()
    assert res["payment"] is None and res["guest"]["phone"] is None and res["redacted"] == {"contact": True, "amounts": True}
    assert all(not a["available"] and a["reason"] == "outside your access" for a in res["actions"])
    forbidden = housekeeper.post("/api/world/actions/prepare", {"intent": "check_in", "booking_id": res["booking_id"], "room_number": "101"})
    assert forbidden.status_code == 403
    assert housekeeper.post("/api/world/media", files={"photo": ("a.png", _png(), "image/png")},
                            data={"binding_kind": "room", "binding_ref": "101", "authentic": "true"}).status_code == 403


# ---------- actions: pending is not done; only a confirmed success changes the world ----------

def _readiness(client, room):
    return client.get("/api/world/rooms/" + room + "/presentation").json()["readiness"]


def test_reject_changes_nothing_and_approve_runs_once(manager):
    room = "103"
    before = _readiness(manager, room)
    target = "dirty" if before != "dirty" else "clean"
    card = manager.post("/api/world/actions/prepare", {"intent": "set_readiness", "room_number": room, "new_status": target}).json()
    assert card["state"] == "pending" and card["status"] == "pending" and card["backend_action"] == "room.set_readiness"
    assert _readiness(manager, room) == before, "preparing changes nothing"
    assert manager.post(card["reject_path"]).json()["status"] == "rejected"
    assert _readiness(manager, room) == before, "rejecting changes nothing"
    assert manager.post(card["approve_path"]).json()["status"] == "rejected", "a rejected card cannot be approved later"

    card = manager.post("/api/world/actions/prepare", {"intent": "set_readiness", "room_number": room, "new_status": target}).json()
    done = manager.post(card["approve_path"]).json()
    assert done["status"] == "approved" and done["result"]["ok"] is True
    assert _readiness(manager, room) == target
    again = manager.post(card["approve_path"]).json()
    assert again["done"] is True and again["status"] == "approved", "a second approval returns the stored result and runs nothing"
    # put it back through the same flow
    back = manager.post("/api/world/actions/prepare", {"intent": "set_readiness", "room_number": room, "new_status": before or "clean"}).json()
    assert manager.post(back["approve_path"]).json()["status"] == "approved"


def test_a_refused_operation_is_reported_as_failed_not_done(manager, window):
    start, end, _ = window
    cal = manager.get("/api/world/calendar", start_date=start, end_date=end).json()
    unpaid = None
    for span in cal["stays"]:
        if span["state"] in ("in_house", "due_out"):
            res = manager.get("/api/world/reservations/" + span["booking_id"] + "/presentation", stay_date=span["last_night"]).json()
            if res["payment"]["balance_due"] and res["payment"]["balance_due"] > 0:
                unpaid = span
                break
    if not unpaid:
        pytest.skip("no in-house stay with a balance due in the sandbox today")
    card = manager.post("/api/world/actions/prepare", {"intent": "check_out", "booking_id": unpaid["booking_id"], "room_number": unpaid["room_number"]}).json()
    outcome = manager.post(card["approve_path"]).json()
    assert outcome["status"] == "failed" and outcome["result"]["ok"] is False and outcome["result"]["detail"]
    after = manager.get("/api/world/calendar", start_date=start, end_date=end).json()
    same = [s for s in after["stays"] if s["booking_id"] == unpaid["booking_id"] and s["room_number"] == unpaid["room_number"]]
    assert same and same[0]["state"] == unpaid["state"], "a failed check-out leaves the stay as it was"


# ---------- media: authentic photo or an honest no_photo ----------

def test_no_photo_then_publish_then_retire(manager, other_hotel):
    room = manager.get("/api/world/rooms/202/presentation").json()
    assert room["media"]["state"] in ("no_photo", "ok")
    refused = manager.post("/api/world/media", files={"photo": ("a.png", _png(), "image/png")},
                           data={"binding_kind": "room_type", "binding_ref": room["room_type"], "authentic": ""})
    assert refused.status_code == 400 and "authentic" in refused.json()["error"]
    fake = manager.post("/api/world/media", files={"photo": ("a.png", b"GIF89a" + b"\x00" * 64, "image/png")},
                        data={"binding_kind": "room_type", "binding_ref": room["room_type"], "authentic": "true"})
    assert fake.status_code == 400
    added = manager.post("/api/world/media", files={"photo": ("t.png", _png(321, 201), "image/png")},
                         data={"binding_kind": "room_type", "binding_ref": room["room_type"], "caption": "integration test image", "authentic": "true"}).json()
    try:
        assert added["state"] == "ok"
        shown = manager.get("/api/world/rooms/202/presentation").json()["media"]
        item = next(i for i in shown["items"] if i["media_id"] == added["media_id"])
        assert item["exact"] is False and item["label"].startswith("Example of room type")
        got = manager.get("/api/world/media/" + added["media_id"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.headers["cache-control"].startswith("private")
        assert other_hotel.get("/api/world/media/" + added["media_id"]).status_code == 404
    finally:
        manager.post("/api/world/media/" + added["media_id"] + "/retire")
    assert manager.get("/api/world/media/" + added["media_id"]).status_code == 404


# ---------- scene manifests: validated, versioned, rollback ----------

def test_publish_validate_and_rollback_a_scene(manager):
    scene = {"schema_version": 1, "scene_id": "it-lobby", "coordinate_space": {"width": 800, "height": 450},
             "background_asset_id": "bg_test", "objects": [{"id": "desk", "type": "hotspot", "bounds": {"x": 10, "y": 10, "w": 100, "h": 50},
                                                            "label": "Reception", "allowed_action_keys": ["view_hotel_info"]}]}
    first = manager.post("/api/world/scenes", scene).json()
    scene["objects"][0]["label"] = "Front desk"
    second = manager.post("/api/world/scenes", scene).json()
    assert second["scene_version"] == first["scene_version"] + 1
    assert manager.get("/api/world/manifest", scene="it-lobby").json()["manifest"]["objects"][0]["label"] == "Front desk"
    bad = dict(scene, objects=[dict(scene["objects"][0], label="<script>")])
    assert manager.post("/api/world/scenes", bad).status_code == 400
    assert manager.get("/api/world/manifest", scene="it-lobby").json()["scene_version"] == second["scene_version"], "a bad publish changes nothing"
    manager.post("/api/world/scenes/it-lobby/rollback", {"scene_version": first["scene_version"]})
    assert manager.get("/api/world/manifest", scene="it-lobby").json()["manifest"]["objects"][0]["label"] == "Reception"
    assert manager.get("/api/world/manifest", scene="nope").status_code == 404
