"""Scene manifest contract (schema_version 1): declarative JSON only. Validation is pure and renderer-independent.

A manifest positions illustrated art and hotspots. It never carries code, SQL, URLs to arbitrary hosts, guest data or PMS state.
allowed_action_keys are presentation intents; the server still decides availability and permission for every action."""

import re

SCHEMA_VERSION = 1
MAX_OBJECTS = 200
MAX_BYTES = 256 * 1024

OBJECT_TYPES = ("decorative", "hotspot", "room_hotspot", "facility_hotspot", "npc", "advisor")
BINDING_KINDS = ("room", "room_type", "facility")
AMBIENT_PROFILES = ("calm", "lively", "night", "none")
ANIMATIONS = ("none", "sway", "blink", "bob", "glow", "soft_attention_idle", "walk_loop", "flutter")
MEDIA_ROLES = ("actual_room_or_room_type_photo", "facility_photo", "none")
ACTION_KEYS = ("view_hotel_info", "request_help", "view_room", "review_room_actions", "view_facility", "open_calendar", "ask_leon")

_ID = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-\.]{0,63}$")
_ASSET = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,95}$")
# Text that reads as markup, script or a template must not reach a renderer.
_UNSAFE_TEXT = re.compile(r"[<>{}`]|javascript:|data:|\$\(", re.IGNORECASE)
_TOP_KEYS = {"schema_version", "world_id", "scene_id", "scene_version", "coordinate_space", "background_asset_id", "ambient_profile",
             "camera", "objects", "illustration_notice"}
_OBJECT_KEYS = {"id", "type", "bounds", "polygon", "z", "anchor", "label", "asset_id", "entity_binding", "preview_media_id",
                "preview_media_role", "allowed_action_keys", "decorative_animation"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text_ok(value, limit=80):
    return isinstance(value, str) and 0 < len(value) <= limit and not _UNSAFE_TEXT.search(value)


def validate(manifest):
    """[] when the manifest is publishable, else a list of problems ("path: reason")."""
    problems = []

    def bad(path, reason):
        problems.append(path + ": " + reason)

    if not isinstance(manifest, dict):
        return ["manifest: must be an object"]
    for key in sorted(set(manifest) - _TOP_KEYS):
        bad(key, "unknown field")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        bad("schema_version", "must be " + str(SCHEMA_VERSION))
    if not isinstance(manifest.get("scene_id"), str) or not _ID.match(manifest.get("scene_id") or ""):
        bad("scene_id", "lowercase id of letters, digits, - or _")
    space = manifest.get("coordinate_space")
    width = height = 0
    if not isinstance(space, dict) or not _number(space.get("width")) or not _number(space.get("height")):
        bad("coordinate_space", "needs numeric width and height")
    else:
        width, height = space["width"], space["height"]
        if not (64 <= width <= 8192 and 64 <= height <= 8192):
            bad("coordinate_space", "width and height must be 64..8192")
    if not isinstance(manifest.get("background_asset_id"), str) or not _ASSET.match(manifest.get("background_asset_id") or ""):
        bad("background_asset_id", "asset id required")
    if manifest.get("ambient_profile", "calm") not in AMBIENT_PROFILES:
        bad("ambient_profile", "one of " + ", ".join(AMBIENT_PROFILES))
    camera = manifest.get("camera")
    if camera is not None:
        if not isinstance(camera, dict) or not all(_number(camera.get(key)) for key in ("min_zoom", "max_zoom")):
            bad("camera", "needs numeric min_zoom and max_zoom")
        elif not (0.25 <= camera["min_zoom"] <= camera["max_zoom"] <= 4):
            bad("camera", "0.25 <= min_zoom <= max_zoom <= 4")
    objects = manifest.get("objects")
    if not isinstance(objects, list) or not objects:
        bad("objects", "a non-empty list")
        objects = []
    if len(objects) > MAX_OBJECTS:
        bad("objects", "at most " + str(MAX_OBJECTS))
    seen = set()
    for index, item in enumerate(objects[:MAX_OBJECTS]):
        path = "objects[" + str(index) + "]"
        if not isinstance(item, dict):
            bad(path, "must be an object")
            continue
        for key in sorted(set(item) - _OBJECT_KEYS):
            bad(path + "." + key, "unknown field")
        object_id = item.get("id")
        if not isinstance(object_id, str) or not _ID.match(object_id):
            bad(path + ".id", "lowercase id required")
        elif object_id in seen:
            bad(path + ".id", "duplicate id " + object_id)
        else:
            seen.add(object_id)
        kind = item.get("type")
        if kind not in OBJECT_TYPES:
            bad(path + ".type", "one of " + ", ".join(OBJECT_TYPES))
        bounds, polygon = item.get("bounds"), item.get("polygon")
        if bounds is None and polygon is None:
            bad(path, "needs bounds or polygon")
        if bounds is not None:
            if not isinstance(bounds, dict) or not all(_number(bounds.get(key)) for key in ("x", "y", "w", "h")):
                bad(path + ".bounds", "needs numeric x, y, w, h")
            elif bounds["w"] <= 0 or bounds["h"] <= 0 or bounds["x"] < 0 or bounds["y"] < 0 \
                    or (width and bounds["x"] + bounds["w"] > width) or (height and bounds["y"] + bounds["h"] > height):
                bad(path + ".bounds", "must be positive and inside coordinate_space")
        if polygon is not None:
            if not isinstance(polygon, list) or not 3 <= len(polygon) <= 32 or not all(
                    isinstance(point, list) and len(point) == 2 and all(_number(value) for value in point) for point in polygon):
                bad(path + ".polygon", "3..32 [x, y] points")
            elif width and height and not all(0 <= point[0] <= width and 0 <= point[1] <= height for point in polygon):
                bad(path + ".polygon", "points must be inside coordinate_space")
        if "z" in item and (not isinstance(item["z"], int) or isinstance(item["z"], bool) or not -100 <= item["z"] <= 100):
            bad(path + ".z", "integer -100..100")
        if "label" in item and not _text_ok(item["label"]):
            bad(path + ".label", "plain text up to 80 characters")
        if "asset_id" in item and (not isinstance(item["asset_id"], str) or not _ASSET.match(item["asset_id"])):
            bad(path + ".asset_id", "asset id")
        binding = item.get("entity_binding")
        if kind in ("room_hotspot", "facility_hotspot") and binding is None:
            bad(path + ".entity_binding", "required for " + str(kind))
        if binding is not None:
            if not isinstance(binding, dict) or binding.get("kind") not in BINDING_KINDS or not isinstance(binding.get("id"), str) \
                    or not _REF.match(binding.get("id") or ""):
                bad(path + ".entity_binding", "{kind: room|room_type|facility, id}")
        if "preview_media_id" in item and (not isinstance(item["preview_media_id"], str) or not item["preview_media_id"].startswith("wmd_")):
            bad(path + ".preview_media_id", "an authentic media id (wmd_...)")
        if item.get("preview_media_role", "none") not in MEDIA_ROLES:
            bad(path + ".preview_media_role", "one of " + ", ".join(MEDIA_ROLES))
        keys = item.get("allowed_action_keys", [])
        if not isinstance(keys, list) or any(key not in ACTION_KEYS for key in keys):
            bad(path + ".allowed_action_keys", "only " + ", ".join(ACTION_KEYS))
        if kind == "decorative" and keys:
            bad(path + ".allowed_action_keys", "decorative objects take no actions")
        if item.get("decorative_animation", "none") not in ANIMATIONS:
            bad(path + ".decorative_animation", "one of " + ", ".join(ANIMATIONS))
    return problems
