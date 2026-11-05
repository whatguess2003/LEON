"""World Mode pure tests: no database, no server.  python -m pytest tests/world -q"""

import struct
import zlib
from datetime import date

import pytest

from booking_system.world import manifest, media_check
from booking_system.world.spans import build_blocks, build_spans, build_unassigned, floor_of, span_state

TODAY = date(2026, 11, 5)


def night(booking, status="assigned", stay_status="assigned", name="AISHAH TAN"):
    return {"booking_id": booking, "status": status, "stay_status": stay_status, "guest_name": name, "channel": "DIRECT"}


def grid(**rooms):
    return {"rooms": [{"room_number": number, "room_type": "DELUXE", "entries": entries} for number, entries in rooms.items()]}


# ---------- spans ----------

def test_consecutive_nights_merge_and_checkout_is_the_morning_after():
    cal = grid(r101={"2026-11-05": night("b1"), "2026-11-06": night("b1"), "2026-11-07": night("b2")})
    spans = build_spans(cal, "2026-11-05", "2026-11-10", TODAY)
    assert [(s["booking_id"], s["first_night"], s["last_night"], s["check_out"], s["nights"]) for s in spans] == [
        ("b1", "2026-11-05", "2026-11-06", "2026-11-07", 2), ("b2", "2026-11-07", "2026-11-07", "2026-11-08", 1)]


def test_a_gap_or_a_block_splits_a_booking_into_two_runs():
    cal = grid(r101={"2026-11-05": night("b1"), "2026-11-07": night("b1"),
                     "2026-11-08": {"status": "out-of-order", "booking_id": None}, "2026-11-09": night("b1")})
    spans = build_spans(cal, "2026-11-05", "2026-11-10", TODAY)
    assert [s["first_night"] for s in spans] == ["2026-11-05", "2026-11-07", "2026-11-09"]


def test_room_move_and_group_bookings_count_their_rooms():
    cal = grid(r101={"2026-11-05": night("move"), "2026-11-06": night("group")},
               r102={"2026-11-06": night("move"), "2026-11-07": night("move"), "2026-11-05": night("solo")},
               r103={"2026-11-06": night("group")})
    spans = build_spans(cal, "2026-11-05", "2026-11-10", TODAY)
    by = {(s["booking_id"], s["room_number"]): s for s in spans}
    assert by[("move", "r101")]["rooms_in_booking"] == 2 and by[("move", "r102")]["first_night"] == "2026-11-06"
    assert by[("group", "r101")]["rooms_in_booking"] == 2
    assert by[("solo", "r102")]["rooms_in_booking"] == 1


def test_window_clipping_and_continuation_flags():
    cal = grid(r101={"2026-11-04": night("b1"), "2026-11-05": night("b1"), "2026-11-11": night("b2"), "2026-11-12": night("b3")})
    spans = build_spans(cal, "2026-11-05", "2026-11-11", TODAY)
    assert [s["booking_id"] for s in spans] == ["b1", "b2"], "b3 starts after the window"
    assert spans[0]["continues_before"] and not spans[0]["continues_after"]
    assert not spans[1]["continues_before"]


def test_cross_month_and_cross_year_stays():
    cal = grid(r101={"2026-12-30": night("ny"), "2026-12-31": night("ny"), "2027-01-01": night("ny")})
    span = build_spans(cal, "2026-12-28", "2027-01-03", TODAY)[0]
    assert (span["first_night"], span["check_out"], span["nights"]) == ("2026-12-30", "2027-01-02", 3)


@pytest.mark.parametrize("statuses,first,last,expected", [
    (["checked_in"], date(2026, 11, 4), date(2026, 11, 5), "in_house"),
    (["checked_in"], date(2026, 11, 3), date(2026, 11, 4), "due_out"),
    (["assigned"], date(2026, 11, 5), date(2026, 11, 6), "arriving"),
    (["assigned"], date(2026, 11, 3), date(2026, 11, 6), "late_arrival"),
    (["assigned"], date(2026, 11, 8), date(2026, 11, 9), "upcoming"),
    (["checked_out", "checked_out"], date(2026, 11, 1), date(2026, 11, 2), "departed"),
    (["checked_out", "checked_in"], date(2026, 11, 4), date(2026, 11, 5), "in_house"),
])
def test_span_states(statuses, first, last, expected):
    assert span_state(statuses, first, last, TODAY) == expected


def test_floor_blocks_and_unassigned():
    assert (floor_of("301"), floor_of("1205"), floor_of("PH1"), floor_of("12")) == (3, 12, None, None)
    cal = {"rooms": [{"room_number": "101", "ooo_linear": [{"start": "2026-11-05", "end": "2026-11-06", "reason": "leak"}]}],
           "unassigned_by_type": {"DELUXE": {"2026-11-05": [{"booking_id": "a"}, {"booking_id": "b"}], "2026-12-01": [{"booking_id": "c"}]}}}
    assert build_blocks(cal) == [{"room_number": "101", "first_night": "2026-11-05", "last_night": "2026-11-06", "reason": "leak"}]
    assert build_unassigned(cal, "2026-11-05", "2026-11-10") == [{"room_type": "DELUXE", "night": "2026-11-05", "count": 2, "booking_ids": ["a", "b"]}]


# ---------- scene manifest ----------

def good_manifest():
    return {
        "schema_version": 1, "scene_id": "lobby-v1", "coordinate_space": {"width": 1600, "height": 900},
        "background_asset_id": "bg_lobby_approved", "ambient_profile": "calm", "camera": {"min_zoom": 0.5, "max_zoom": 2},
        "objects": [
            {"id": "reception-desk", "type": "hotspot", "bounds": {"x": 180, "y": 380, "w": 270, "h": 180}, "label": "Reception",
             "preview_media_id": "wmd_123", "allowed_action_keys": ["view_hotel_info", "request_help"], "decorative_animation": "soft_attention_idle"},
            {"id": "room-301-door", "type": "room_hotspot", "polygon": [[1060, 320], [1180, 320], [1180, 540], [1060, 540]],
             "entity_binding": {"kind": "room", "id": "301"}, "preview_media_role": "actual_room_or_room_type_photo",
             "allowed_action_keys": ["view_room", "review_room_actions"]},
            {"id": "plant", "type": "decorative", "bounds": {"x": 10, "y": 10, "w": 40, "h": 80}, "decorative_animation": "sway"},
        ],
    }


def test_the_brief_example_manifest_validates():
    assert manifest.validate(good_manifest()) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda m: m.update(script="alert(1)"), "script: unknown field"),
    (lambda m: m["objects"][0].update(label="<img src=x onerror=alert(1)>"), "label"),
    (lambda m: m["objects"][0].update(label="{{guest.name}}"), "label"),
    (lambda m: m["objects"][0]["bounds"].update(x=1500), "bounds"),
    (lambda m: m["objects"][2].update(allowed_action_keys=["view_room"]), "decorative objects take no actions"),
    (lambda m: m["objects"][1].pop("entity_binding"), "entity_binding"),
    (lambda m: m["objects"][0].update(preview_media_id="https://evil.example/x.jpg"), "preview_media_id"),
    (lambda m: m["objects"][0].update(allowed_action_keys=["run_sql"]), "allowed_action_keys"),
    (lambda m: m["objects"].append(dict(m["objects"][2])), "duplicate id"),
    (lambda m: m.update(schema_version=2), "schema_version"),
    (lambda m: m.update(objects=[]), "objects"),
    (lambda m: m["objects"][1].update(polygon=[[0, 0], [1, 1]]), "polygon"),
])
def test_manifest_rejections(mutate, needle):
    m = good_manifest()
    mutate(m)
    problems = manifest.validate(m)
    assert any(needle in p for p in problems), problems


# ---------- uploaded photos ----------

def png(width, height):
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg_header(width, height):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\x00" * 32


def webp_vp8x(width, height):
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def test_photo_type_and_size_come_from_the_bytes():
    assert media_check.check(png(320, 200)) == ({"mime": "image/png", "width": 320, "height": 200, "bytes": len(png(320, 200))}, "")
    info, _ = media_check.check(jpeg_header(1600, 1067))
    assert (info["mime"], info["width"], info["height"]) == ("image/jpeg", 1600, 1067)
    info, _ = media_check.check(webp_vp8x(800, 600))
    assert (info["mime"], info["width"], info["height"]) == ("image/webp", 800, 600)


@pytest.mark.parametrize("data,thumb,reason", [
    (b"GIF89a" + b"\x00" * 40, False, "only JPEG, PNG or WebP"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", False, "only JPEG, PNG or WebP"),
    (b"", False, "empty"),
    (png(32, 32), False, "pixels"),
    (png(800, 800), True, "pixels"),
    (b"\xff\xd8\xff" + b"\x00" * (media_check.MAX_FULL_BYTES + 1), False, "larger than"),
])
def test_photo_rejections(data, thumb, reason):
    info, problem = media_check.check(data, thumb=thumb)
    assert info is None and reason in problem


# ---------- service rules that need no database ----------

def session(**modules):
    levels = {"front_desk": "view", "reservations": "none", "housekeeping": "none", "finance": "none", "admin": "none"}
    levels.update(modules)
    return {"hotel_id": "htl_a", "user_id": "usr_a", "hotel_name": "A", "user_name": "U", "role_name": "R",
            "hotel_timezone": "Asia/Kuala_Lumpur", "hotel_currency": "MYR", "modules": levels}


def test_platform_switch(monkeypatch):
    from booking_system.world import service
    for value, expected in (("", "off"), ("0", "off"), ("false", "off"), ("1", "hotel"), ("true", "hotel"), ("all", "all")):
        monkeypatch.setenv("WORLD_MODE_ENABLED", value)
        assert service.platform_switch() == expected
    monkeypatch.setenv("WORLD_MODE_ENABLED", "")
    with pytest.raises(service.WorldError) as refused:
        service.require_enabled(session())
    assert refused.value.state == "disabled"


def test_config_capabilities_follow_module_levels(monkeypatch):
    from booking_system.world import service
    monkeypatch.setenv("WORLD_MODE_ENABLED", "all")
    housekeeper = service.config(session(housekeeping="operate"))["can"]
    assert housekeeper == {"see_guest_contact": False, "see_amounts": False, "manage_media": False,
                           "intents": {"check_in": False, "check_out": False, "set_readiness": True}}
    manager = service.config(session(reservations="manage", housekeeping="manage", finance="manage", admin="manage"))["can"]
    assert all(manager["intents"].values()) and manager["manage_media"] and manager["see_amounts"]
    accountant = service.config(session(finance="manage"))["can"]
    assert accountant["see_amounts"] and not accountant["see_guest_contact"]


def test_intents_offer_only_what_the_stay_allows():
    from booking_system.world import service
    desk = session(reservations="operate")
    nights = [{"stay_date": "2026-11-05", "room_number": "101", "night_status": "booked"},
              {"stay_date": "2026-11-06", "room_number": "101", "night_status": "booked"}]
    actions = {a["intent"]: a for a in service._reservation_intents(desk, TODAY, TODAY, nights, [], "confirmed")}
    assert actions["check_in"]["available"] and actions["check_in"]["room_number"] == "101"
    assert not actions["check_out"]["available"] and actions["check_out"]["reason"] == "guest is not checked in"
    unassigned = [{"stay_date": "2026-11-05", "room_number": None, "night_status": "booked"}]
    actions = {a["intent"]: a for a in service._reservation_intents(desk, TODAY, TODAY, unassigned, [], "confirmed")}
    assert actions["check_in"]["reason"] == "assign a room for tonight first"
    actions = {a["intent"]: a for a in service._reservation_intents(desk, TODAY, TODAY, nights, [], "cancelled")}
    assert not actions["check_in"]["available"]
    viewer = {a["intent"]: a for a in service._reservation_intents(session(), TODAY, TODAY, nights, [], "confirmed")}
    assert viewer["check_in"]["reason"] == "outside your access"


def test_prepare_refuses_unknown_intents_and_missing_parameters_before_touching_the_pms():
    from booking_system.world import service
    with pytest.raises(service.WorldError, match="unknown intent"):
        service.prepare(session(), "run_sql")
    with pytest.raises(service.WorldError, match="missing room_number"):
        service.prepare(session(), "check_in", booking_id="bkg_1")


def test_initials_never_leak_a_full_name():
    from booking_system.world.service import _initials
    assert _initials("AISHAH BINTI TAN") == "A. B. T."
    assert _initials("") == "Guest"
