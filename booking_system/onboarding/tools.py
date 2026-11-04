"""The onboarding agent's write tools, bound to one turn. Each tool reads first through the module that owns the data, writes through
its public function, and records its outcome in turn["outcomes"]; the reply step speaks only from those outcomes. A tool whose
prerequisite (a signed-in person, a hotel) is missing refuses without writing."""

import json

from langchain_core.tools import tool

from booking_system.onboarding import importer, sops, state

# What each tool needs from the turn before it may write.
NEEDS = {
    "create_account": ("no_user",), "sign_in": ("no_user",), "create_hotel": ("user_id", "no_hotel"),
    "save_working_style": (), "set_section_status": (), "close_question": (),
    "save_definition": ("user_id",), "skip_definition": ("user_id",),
    "save_setting": ("hotel_id",), "save_routine": ("hotel_id",), "delete_routine": ("user_id",), "record_gap": ("hotel_id",),
    "submit_sop_text": ("hotel_id",), "confirm_sop": ("hotel_id",), "reject_sop": ("hotel_id",),
    "correct_import_mapping": ("hotel_id",), "preview_import": ("hotel_id",), "commit_import": ("hotel_id",), "rollback_import": ("hotel_id",),
    "invite_link": ("hotel_id", "admin_manage"),
}
_MISSING = {
    "no_user": lambda turn: "" if not turn["user_id"] else "the manager is already signed in",
    "no_hotel": lambda turn: "" if not turn["hotel_id"] else "this session already has its hotel",
    "user_id": lambda turn: "" if turn["user_id"] else "the account must exist first",
    "hotel_id": lambda turn: "" if turn["hotel_id"] else "the hotel must exist first",
    "admin_manage": lambda turn: "" if (turn["modules"] or {}).get("admin") == "manage" else "inviting staff needs admin manage access",
}


def _outcome(turn, name, result, said, section="", key=""):
    """Record the outcome; a success that stored something is noted as saved on the onboarding state."""
    ok = bool(result.get("ok"))
    line = ("Succeeded " if ok else "Failed ") + name + ": " + (said if ok else str(result.get("error") or said))
    turn["outcomes"].append({"tool": name, "ok": ok, "said": line})
    if ok and section and key and result.get("stored", True):
        state.note_saved(turn["onboarding_id"], section, key, said)
    return json.dumps(dict(result, said=line), default=str)


def _guarded(turn, name, run):
    def call(**kwargs):
        refused = [_MISSING[need](turn) for need in NEEDS[name] if _MISSING[need](turn)]
        if refused:
            return _outcome(turn, name, {"ok": False, "error": "; ".join(refused)}, "")
        try:
            return run(**kwargs)
        except Exception as error:
            return _outcome(turn, name, {"ok": False, "error": type(error).__name__ + ": " + str(error)[:300]}, "")
    return call


# ---------- account and hotel ----------

def _signed(turn, user_id, signed):
    from booking_system import access
    claims = signed.get("claims") or access.decode(signed.get("token")) or {}
    turn.update(user_id=user_id, token=signed.get("token") or "", hotel_id=claims.get("hotel_id") or turn["hotel_id"],
                hotel_name=claims.get("hotel_name") or "", modules=claims.get("modules") or {})
    state.bind(turn["onboarding_id"], user_id, claims.get("hotel_id"))


def _create_account(turn, full_name, phone, primary_language, nationality):
    from booking_system import db, user_manager
    if not turn["secret"]:
        return _outcome(turn, "create_account", {"ok": False, "error": "no password was typed in the password box"}, "")
    if db.transaction(lambda cur: user_manager.find_user(cur, "phone", phone)):
        return _outcome(turn, "create_account", {"ok": False, "error": "phone " + phone + " already has an account; sign in with its password"}, "")
    created = user_manager.signup(full_name, {"phone": phone}, turn["secret"], primary_language, nationality)
    if created.get("error"):
        return _outcome(turn, "create_account", {"ok": False, "error": created["error"]}, "")
    signed = user_manager.signin("phone", phone, turn["secret"])
    _signed(turn, created["user_id"], signed)
    turn["user_name"] = str(full_name).strip().upper()
    return _outcome(turn, "create_account", {"ok": True, "user_id": created["user_id"]},
                    "account created for " + turn["user_name"] + " on " + phone + ", signed in", "account", "account")


def _sign_in(turn, phone):
    from booking_system import user_manager
    if not turn["secret"]:
        return _outcome(turn, "sign_in", {"ok": False, "error": "no password was typed in the password box"}, "")
    signed = user_manager.signin("phone", phone, turn["secret"])
    if signed.get("error"):
        return _outcome(turn, "sign_in", {"ok": False, "error": signed["error"]}, "")
    _signed(turn, signed["user_id"], signed)
    hotels = [row["hotel_name"] for row in signed.get("choose") or []]
    said = "signed in" + (" at " + turn["hotel_name"] if turn["hotel_name"] else "") + (" (hotels: " + ", ".join(hotels) + ")" if hotels else "")
    return _outcome(turn, "sign_in", {"ok": True, "hotels": hotels}, said, "account", "account")


def _create_hotel(turn, hotel_name, city, timezone, currency):
    from booking_system import user_manager
    created = user_manager.create_hotel(turn["user_id"], hotel_name, currency, timezone, city)
    if created.get("error"):
        return _outcome(turn, "create_hotel", {"ok": False, "error": created["error"]}, "")
    _signed(turn, turn["user_id"], user_manager.token_for(turn["user_id"], created["hotel_id"]) or {})
    return _outcome(turn, "create_hotel", {"ok": True, "hotel_id": created["hotel_id"]},
                    "hotel " + created["hotel_name"] + " created in " + city + " (" + timezone + ", " + currency + "), you are its general manager",
                    "hotel", "hotel")


# ---------- style and checklist ----------

def _save_working_style(turn, language, tone, pace, role_note):
    result = state.save_style(turn["onboarding_id"], language, tone, pace, role_note)
    changed = result.get("changed") or {}
    said = "working style noted: " + ", ".join(name + " " + value for name, value in changed.items()) if changed else "working style unchanged"
    return _outcome(turn, "save_working_style", dict(result, stored=bool(changed)), said, "account", "working_style")


def _set_section_status(turn, section, status, note):
    result = state.set_section(turn["onboarding_id"], section, status, note)
    return _outcome(turn, "set_section_status", dict(result, stored=False), "section " + section + " is " + status)


def _close_question(turn, key, status):
    result = state.close_item(turn["onboarding_id"], key, status)
    return _outcome(turn, "close_question", dict(result, stored=False), "question " + key + " " + status)


# ---------- definitions ----------

def _save_definition(turn, key, label, definition):
    from booking_system.semantic import definitions
    name = "_".join(str(key or "").lower().split())
    if name not in {item["key"] for item in definitions.known_keys(turn["user_id"])}:
        opened = definitions.open_request(turn["user_id"], name, label, "What do you count as " + str(label).lower() + "?")
        if opened.get("error"):
            return _outcome(turn, "save_definition", {"ok": False, "error": opened["error"]}, "")
    saved = definitions.save_definition(turn["user_id"], name, definition, "onboarding")
    if saved.get("error"):
        return _outcome(turn, "save_definition", {"ok": False, "error": saved["error"]}, "")
    state.reopen_skipped(turn["onboarding_id"], name)
    said = ("definition of " if saved["status"] == "answered" else "definition already on record for ") + str(label or name) + ": " + definition
    return _outcome(turn, "save_definition", dict(saved, ok=True, stored=saved["status"] == "answered"), said, "definitions", name)


def _skip_definition(turn, key):
    from booking_system.semantic import definitions
    skipped = definitions.skip_definition(turn["user_id"], key)
    if skipped.get("error"):
        return _outcome(turn, "skip_definition", {"ok": False, "error": skipped["error"]}, "")
    state.close_item(turn["onboarding_id"], skipped["key"], "skipped")
    return _outcome(turn, "skip_definition", dict(skipped, ok=True, stored=False), "definition " + skipped["key"] + " set aside for later")


# ---------- settings ----------

def _catalog_row(key):
    from booking_system.settings import service
    return next((row for row in service.catalog_rows() if row["setting_key"] == key), None)


def _save_setting(turn, setting_key, value, sop_text):
    from booking_system.settings import service
    row = _catalog_row(setting_key)
    if not row:
        return _outcome(turn, "save_setting", {"ok": False, "error": "unknown setting " + setting_key}, "")
    try:
        parsed = service.FROM_TEXT[row["value_type"]](value)
    except ValueError:
        return _outcome(turn, "save_setting", {"ok": False, "error": setting_key + " cannot be " + str(value)}, "")
    waiting_ids, waiting_text = sops.waiting(turn["hotel_id"], setting_key)
    procedure = str(sop_text or "").strip() or waiting_text
    on = service.earliest(turn["hotel_id"], row["effective_basis"]).isoformat()
    saved = service.set_setting(turn["hotel_id"], turn["user_id"], setting_key, parsed, on, "onboarding", "", procedure or None)
    if not saved.get("ok"):
        return _outcome(turn, "save_setting", saved, "")
    if saved["sop_saved"] and not str(sop_text or "").strip():
        sops.attached(waiting_ids, setting_key)
    section = "modules" if setting_key == row["module_name"] + ".enabled" else "settings"
    shown = saved["meaning"] or json.dumps(saved["value"])
    said = (setting_key + " = " + shown + " from " + saved["effective_from"] if saved["result"] == "saved" else setting_key + " was already " + shown)
    said += " with its procedure" if saved["sop_saved"] else ""
    said += ("; " + saved["module"] + " " + saved["refusal"]) if saved["refusal"] else ""
    stored = saved["result"] == "saved" or saved["sop_saved"]
    return _outcome(turn, "save_setting", dict(saved, stored=stored), said, section, setting_key)


# ---------- routines and gaps ----------

def _gap(turn, figure, description, day_offset):
    """A figure no recipe answers stays with the desk. It does not open a catalog investigation."""
    return {"figure": figure, "description": str(description or ""), "gate": "desk", "trace_id": None}


GATE_SAID = {
    "desk": "kept on the desk; a shared method is investigated only after a user complaint",
}


def _save_routine(turn, name, figures_request):
    from booking_system.semantic import personal
    items, seen = personal.learn(turn["user_id"], name, figures_request)
    if not items:
        return _outcome(turn, "save_routine", {"ok": False, "error": "no figures were named"}, "")
    saved = personal.save_personal(turn["user_id"], name, items, seen)
    if saved.get("error"):
        return _outcome(turn, "save_routine", {"ok": False, "error": saved["error"]}, "")
    recipe = saved["recipe"]
    needed = personal.needed_definitions(turn["user_id"], recipe)
    waiting = {item["key"] for item in needed}
    gaps = [_gap(turn, item["label"], "From the routine report " + recipe["name"] + ".", item.get("day_offset") or 0)
            for item in recipe["items"] if item.get("status") == "pending" and (item.get("definition") or {}).get("key", "") not in waiting]
    parts = [item["label"] + (" ready" if item.get("status") == "ready" else " pending") for item in recipe["items"]]
    said = "routine report " + recipe["name"] + " " + saved["status"] + " (" + ", ".join(parts) + ")"
    said += "".join("; " + gap["figure"] + ": " + GATE_SAID.get(gap["gate"], "not queued (" + gap["gate"] + ")") for gap in gaps)
    said += "".join("; " + item["label"] + " needs your definition: " + item["question"] for item in needed)
    return _outcome(turn, "save_routine", {"ok": True, "key": recipe["key"], "figures": recipe["items"], "gaps": gaps, "needs_definitions": needed},
                    said, "routines", recipe["key"])


def _delete_routine(turn, key):
    from booking_system.semantic import personal
    deleted = personal.delete_personal(turn["user_id"], key)
    return _outcome(turn, "delete_routine", dict(deleted, ok=not deleted.get("error")), "routine report " + str(key) + " deleted", "routines", "delete:" + str(key))


def _record_gap(turn, figure, description, day_offset):
    gap = _gap(turn, figure, description, day_offset)
    ok = gap["gate"] in GATE_SAID
    return _outcome(turn, "record_gap", dict(gap, ok=ok, error="not recorded: " + gap["gate"]),
                    figure + ": " + GATE_SAID.get(gap["gate"], ""), "routines", "gap:" + "_".join(figure.lower().split()))


# ---------- SOPs ----------

def _submit_sop_text(turn, title, text):
    from booking_system.onboarding import files
    result = sops.submit(turn["hotel_id"], turn["user_id"], turn["onboarding_id"], title, "chat", "", files.text_document(text))
    links = "; ".join(link["setting_key"] + ": " + link["summary"] for link in result.get("links") or [])
    said = "SOP " + str(result.get("sop_id")) + " read, waiting for the manager's confirmation. " + str(result.get("summary") or "") + (" Parts: " + links if links else "")
    return _outcome(turn, "submit_sop_text", dict(result, stored=False), said)


def _confirm_sop(turn, sop_id, setting_keys):
    result = sops.confirm(turn["hotel_id"], turn["user_id"], sop_id, setting_keys)
    parts = "; ".join(link["setting_key"] + " " + link["status"].replace("_", " ") + (" (" + link["detail"] + ")" if link["detail"] else "")
                      for link in result.get("links") or [])
    return _outcome(turn, "confirm_sop", result, "SOP " + str(result.get("title")) + " confirmed: " + (parts or "kept as a hotel document"),
                    "sops", "sop:" + sop_id)


def _reject_sop(turn, sop_id):
    return _outcome(turn, "reject_sop", dict(sops.reject(turn["hotel_id"], sop_id), stored=False), "SOP " + sop_id + " set aside")


# ---------- imports ----------

def _batch_of(turn, file_id):
    from booking_system import db
    found = db.row("SELECT batch_id FROM import_file WHERE file_id = %s AND hotel_id = %s", (file_id, turn["hotel_id"]))
    return found["batch_id"] if found else ""


def _correct_import_mapping(turn, file_id, entity, field, source_column, fixed_value, field_date_format, source_value, mapped_value,
                            date_format, phone_country_code, phone_trunk_prefix):
    formats = {key: value for key, value in (("date_format", date_format), ("phone_country_code", phone_country_code),
                                             ("phone_trunk_prefix", phone_trunk_prefix)) if value}
    given = {key: value for key, value in (("source", source_column), ("fixed", fixed_value), ("format", field_date_format)) if value}
    fields = {field: given} if field and given else {}
    value_maps = [{"field": field, "source_value": source_value, "value": mapped_value}] if field and source_value else []
    result = importer.set_mapping(turn["hotel_id"], _batch_of(turn, file_id), file_id, entity, fields, formats, value_maps)
    return _outcome(turn, "correct_import_mapping", dict(result, stored=False), "mapping of " + file_id + " corrected; preview it again")


def _invite_link(turn, invite_id):
    from booking_system.onboarding import invites
    result = invites.reissue(turn["hotel_id"], invite_id)
    said = "sign-up link for " + str(result.get("user_name")) + ": " + str(result.get("link")) + " (valid until " + str(result.get("expires_at")) + ")"
    return _outcome(turn, "invite_link", dict(result, stored=False), said)


def _counts(result):
    return "; ".join(item["file_name"] + " (" + item["label"] + "): " + str(item["row_count"]) + " rows, " + str(item["valid_count"]) + " ready, "
                     + str(item["error_count"]) + " with errors, " + str(item["duplicate_count"]) + " duplicates" for item in result.get("files") or [])


def _preview_import(turn, batch_id):
    result = importer.preview(turn["hotel_id"], turn["user_id"], batch_id)
    return _outcome(turn, "preview_import", dict(result, stored=False), "preview of " + batch_id + ": " + _counts(result))


def _commit_import(turn, batch_id):
    result = importer.commit(turn["hotel_id"], turn["user_id"], batch_id, turn["modules"])
    created = ", ".join(str(count) + " " + entity for entity, count in (result.get("created") or {}).items())
    return _outcome(turn, "commit_import", result, "import " + batch_id + " committed: " + created + " (" + str(result.get("skipped_rows")) + " rows skipped)",
                    "migration", batch_id)


def _rollback_import(turn, batch_id):
    result = importer.rollback(turn["hotel_id"], turn["user_id"], batch_id)
    removed = ", ".join(str(count) + " " + table for table, count in (result.get("removed") or {}).items())
    return _outcome(turn, "rollback_import", result, "import " + batch_id + " rolled back: removed " + removed, "migration", batch_id + ":rollback")


def build(turn):
    """The tools for one turn, with their argument schemas and descriptions for the model."""
    def bound(name, run):
        return _guarded(turn, name, lambda **kwargs: run(turn, **kwargs))

    @tool
    def create_account(full_name: str, phone: str, primary_language: str, nationality: str) -> str:
        """Create the manager's account and sign them in. phone in international form (+ and country code). The password is the one
        they typed in the password box; never ask for it in chat."""
        return bound("create_account", _create_account)(full_name=full_name, phone=phone, primary_language=primary_language, nationality=nationality)

    @tool
    def sign_in(phone: str) -> str:
        """Sign in a manager whose phone already has an account, with the password typed in the password box."""
        return bound("sign_in", _sign_in)(phone=phone)

    @tool
    def create_hotel(hotel_name: str, city: str, timezone: str, currency: str) -> str:
        """Create the hotel with the signed-in manager as its general manager. timezone is an IANA name (Asia/Kuala_Lumpur); currency
        is the three-letter code (MYR)."""
        return bound("create_hotel", _create_hotel)(hotel_name=hotel_name, city=city, timezone=timezone, currency=currency)

    @tool
    def save_working_style(language: str, tone: str, pace: str, role_note: str) -> str:
        """Note how this manager likes to work. language: the language to speak; tone: warm, brief, formal or playful; pace:
        one_at_a_time or several_at_once; role_note: their role and anything about how they run the hotel. Empty keeps what is noted."""
        return bound("save_working_style", _save_working_style)(language=language, tone=tone, pace=pace, role_note=role_note)

    @tool
    def set_section_status(section: str, status: str, note: str) -> str:
        """Mark a checklist section: account, hotel, modules, settings, definitions, routines, sops, migration; status not_started,
        in_progress, skipped or done. note: why, in a few words (for example 'manager will send SOPs next week')."""
        return bound("set_section_status", _set_section_status)(section=section, status=status, note=note)

    @tool
    def close_question(key: str, status: str) -> str:
        """Close an open question by its key: answered (settled some other way) or skipped (the manager set it aside)."""
        return bound("close_question", _close_question)(key=key, status=status)

    @tool
    def save_definition(key: str, label: str, definition: str) -> str:
        """Save what this manager means by a phrase. key: a standard key (arrivals, in_house, departures, reservations), an open
        request's key, or a new snake_case key (occupancy, revenue); label: the phrase in two or three words; definition: one short
        statement of which bookings, guests or rooms count, in their meaning (for example 'Bookings scheduled to arrive today,
        excluding cancelled')."""
        return bound("save_definition", _save_definition)(key=key, label=label, definition=definition)

    @tool
    def skip_definition(key: str) -> str:
        """The manager does not want to define this phrase now."""
        return bound("skip_definition", _skip_definition)(key=key)

    @tool
    def save_setting(setting_key: str, value: str, sop_text: str) -> str:
        """Record an operating setting the manager stated, from today on. value: an option value, true or false, HH:MM, an amount, or a
        comma-separated list, as the setting's value_type needs. sop_text: their procedure for it in their own words, or empty."""
        return bound("save_setting", _save_setting)(setting_key=setting_key, value=value, sop_text=sop_text)

    @tool
    def save_routine(name: str, figures_request: str) -> str:
        """Save a report the manager checks routinely as their personal report. name: what they call it ('yesterday report');
        figures_request: the figures and the day as they said them ('total sales and the number of bookings made, for yesterday').
        Figures no recipe covers stay on the desk until a user complaint."""
        return bound("save_routine", _save_routine)(name=name, figures_request=figures_request)

    @tool
    def delete_routine(key: str) -> str:
        """Delete one of the manager's saved routine reports by its key."""
        return bound("delete_routine", _delete_routine)(key=key)

    @tool
    def record_gap(figure: str, description: str, day_offset: int) -> str:
        """Note a figure the manager checks routinely that no report covers. It is not sent to the catalog."""
        return bound("record_gap", _record_gap)(figure=figure, description=description, day_offset=day_offset)

    @tool
    def submit_sop_text(title: str, text: str) -> str:
        """Read a procedure the manager pasted in chat; returns a summary and the settings it goes with, to confirm before saving."""
        return bound("submit_sop_text", _submit_sop_text)(title=title, text=text)

    @tool
    def confirm_sop(sop_id: str, setting_keys: list[str]) -> str:
        """The manager confirmed the summary: store each linked part, in the document's own words, as its setting's procedure.
        setting_keys: the linked settings they confirmed when they confirmed only some (the rest are dropped); empty for all."""
        return bound("confirm_sop", _confirm_sop)(sop_id=sop_id, setting_keys=setting_keys)

    @tool
    def reject_sop(sop_id: str) -> str:
        """The manager said the summary is wrong or does not want it kept."""
        return bound("reject_sop", _reject_sop)(sop_id=sop_id)

    @tool
    def correct_import_mapping(file_id: str, entity: str, field: str, source_column: str, fixed_value: str, field_date_format: str,
                               source_value: str, mapped_value: str, date_format: str, phone_country_code: str, phone_trunk_prefix: str) -> str:
        """Correct an uploaded file's mapping as the manager said. entity: the LEON entity, or empty to keep it. field: the LEON field
        to change (empty for none), with source_column (a file column) or fixed_value (one value for every row), field_date_format (a
        Python strptime format for this field only), or source_value with mapped_value (a value in the file and the LEON value it
        means, e.g. 'Chkd Out' -> 'checked_out'). date_format: the file's strptime format; phone_country_code and phone_trunk_prefix
        digits. Empty keeps each."""
        return bound("correct_import_mapping", _correct_import_mapping)(
            file_id=file_id, entity=entity, field=field, source_column=source_column, fixed_value=fixed_value,
            field_date_format=field_date_format, source_value=source_value, mapped_value=mapped_value, date_format=date_format,
            phone_country_code=phone_country_code, phone_trunk_prefix=phone_trunk_prefix)

    @tool
    def invite_link(invite_id: str) -> str:
        """A fresh one-time sign-up link for a person imported from the staff accounts file (their invite_id is in the import's invites).
        The manager shares it with that person; it replaces any earlier link."""
        return bound("invite_link", _invite_link)(invite_id=invite_id)

    @tool
    def preview_import(batch_id: str) -> str:
        """The manager agreed with the mapping: check every row and report counts, errors and duplicates."""
        return bound("preview_import", _preview_import)(batch_id=batch_id)

    @tool
    def commit_import(batch_id: str) -> str:
        """The manager said to import the previewed batch: write every valid row in one transaction."""
        return bound("commit_import", _commit_import)(batch_id=batch_id)

    @tool
    def rollback_import(batch_id: str) -> str:
        """The manager asked to undo a committed import: remove everything it created."""
        return bound("rollback_import", _rollback_import)(batch_id=batch_id)

    return [create_account, sign_in, create_hotel, save_working_style, set_section_status, close_question, save_definition, skip_definition,
            save_setting, save_routine, delete_routine, record_gap, submit_sop_text, confirm_sop, reject_sop, correct_import_mapping,
            preview_import, commit_import, rollback_import, invite_link]
