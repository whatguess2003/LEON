"""SOP submissions. The manager pastes or uploads a procedure (PDF, DOCX, Markdown or text, read on the server into sections). Each
section is summarized by the model and its parts that carry out a catalog setting are linked to that setting_key, in the hotel's own
words; then the whole document is summarized. Nothing is stored as a setting's SOP until the manager confirms: then each linked
setting's parts are recorded through the settings layer (a versioned hotel_sop document on the value in effect). A setting with no
value yet waits and is attached when the value is saved. The extracted text, its sections and the original file are kept."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor

from booking_system import db
from booking_system.content_store import client
from booking_system.hotel_registry import new_id
from booking_system.onboarding import llm

sop_texts = client["leon_onboarding"]["sop_text"]
sop_files = client["leon_onboarding"]["sop_file"]
PARALLEL = 6
_SECTION = (
    "You read one section of a hotel's standard operating procedure. summary says in one or two short sentences what the staff do "
    "in this section. links names the listed hotel settings this section carries out: a part about room release and inspection "
    "belongs to the setting whose sop_prompt asks for that procedure. excerpt copies the hotel's own words for that part, complete, "
    "without rewording. Leave out a setting the section does not cover; links may be empty."
)
_DOCUMENT = (
    "You read the section summaries of a hotel's standard operating procedure. title is a short name for the whole document (keep "
    "title_given when one is given); summary is two or three sentences on the whole document, written for the manager to confirm."
)
_DOCUMENT_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
                    "required": ["title", "summary"], "additionalProperties": False}


def _section_schema(keys):
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "links": {"type": "array", "items": {
                "type": "object",
                "properties": {"setting_key": {"type": "string", "enum": keys}, "summary": {"type": "string"}, "excerpt": {"type": "string"}},
                "required": ["setting_key", "summary", "excerpt"], "additionalProperties": False}},
        },
        "required": ["summary", "links"],
        "additionalProperties": False,
    }


def _catalog():
    from booking_system.settings import service
    return {row["setting_key"]: row for row in service.catalog_rows()}


def _read_sections(sections, rows):
    listed = [{"setting_key": key, "question": row["question"], "sop_prompt": row["sop_prompt"]} for key, row in rows.items()]
    schema = _section_schema(list(rows))

    def one(section):
        return llm.structured(_SECTION, {"settings": listed, "section_title": section["title"], "section_text": section["text"]},
                              "sop_section", schema)
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        return list(pool.map(one, sections))


def submit(hotel_id, user_id, onboarding_id, title, source, file_name, document, original=b""):
    """document is files.read_document / files.text_document output; original the uploaded bytes. Summarizes and links each section,
    stores the proposal, and returns what the manager is asked to confirm."""
    sections = document["sections"]
    if not sections:
        return {"ok": False, "error": "the SOP is empty"}
    rows = _catalog()
    read = _read_sections(sections, rows)
    whole = llm.structured(_DOCUMENT, {"title_given": title, "file_name": file_name,
                                       "sections": [{"title": s["title"], "summary": r["summary"]} for s, r in zip(sections, read)]},
                           "sop_document", _DOCUMENT_SCHEMA)
    links, excerpts = {}, {}
    for seq, parsed in enumerate(read, 1):
        for link in parsed["links"]:
            if link["excerpt"].strip():
                links.setdefault(link["setting_key"], {"summaries": [], "sections": []})
                links[link["setting_key"]]["summaries"].append(link["summary"])
                links[link["setting_key"]]["sections"].append(seq)
                excerpts.setdefault(link["setting_key"], []).append(link["excerpt"].strip())
    sop_id = new_id("osp")
    named = str(title or "").strip() or whole["title"].strip()

    def run(cur):
        cur.execute("""INSERT INTO onboarding_sop (sop_id, hotel_id, onboarding_id, user_id, title, source, file_name, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed')""",
                    (sop_id, hotel_id, onboarding_id, user_id, named[:255], source, str(file_name or "")[:255]))
        for seq, (section, parsed) in enumerate(zip(sections, read), 1):
            keys = sorted({link["setting_key"] for link in parsed["links"] if link["excerpt"].strip()})
            cur.execute("INSERT INTO onboarding_sop_section (sop_id, hotel_id, seq, title, summary, setting_keys) VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
                        (sop_id, hotel_id, seq, section["title"][:255], parsed["summary"], json.dumps(keys)))
        for key, link in links.items():
            cur.execute("INSERT INTO onboarding_sop_link (sop_id, hotel_id, setting_key, summary, status) VALUES (%s, %s, %s, %s, 'proposed')",
                        (sop_id, hotel_id, key, " ".join(link["summaries"])))
        sop_texts.write_in(cur, sop_id, {"_id": sop_id, "sop_id": sop_id, "hotel_id": hotel_id, "title": named, "text": document["text"],
                                         "summary": whole["summary"], "sections": sections,
                                         "excerpts": {key: "\n\n".join(parts) for key, parts in excerpts.items()}})
        if original:
            sop_files.write_in(cur, sop_id, {"_id": sop_id, "sop_id": sop_id, "hotel_id": hotel_id, "file_name": file_name,
                                             "mime": document["mime"], "size": len(original), "text": document["text"],
                                             "content_base64": base64.b64encode(original).decode("ascii")})
    db.transaction(run)
    return {"ok": True, "sop_id": sop_id, "title": named, "summary": whole["summary"],
            "sections": [{"seq": seq, "title": s["title"], "summary": r["summary"],
                          "setting_keys": sorted({link["setting_key"] for link in r["links"] if link["excerpt"].strip()})}
                         for seq, (s, r) in enumerate(zip(sections, read), 1)],
            "links": [{"setting_key": key, "summary": " ".join(link["summaries"]), "sections": link["sections"]} for key, link in links.items()]}


def _text(sop_id):
    found = db.row("SELECT body FROM content_document WHERE database_name = %s AND collection_name = %s AND document_key = %s",
                   (sop_texts.database_name, sop_texts.collection_name, sop_id))
    return found["body"] if found else {}


def _save_part(hotel_id, user_id, key, row, value, excerpt):
    from booking_system.settings import service
    return service.set_setting(hotel_id, user_id, key, value, service.earliest(hotel_id, row["effective_basis"]).isoformat(), "onboarding",
                               "", excerpt)


def confirm(hotel_id, user_id, sop_id, setting_keys=()):
    """READ the proposal, its links and each setting's value in effect. A linked part is recorded as that setting's SOP on the value in
    effect; a setting with no value yet waits for it. setting_keys, when given, are the links the manager confirmed: the others are
    dropped (the document and its sections are kept). Returns each link's outcome."""
    from booking_system.settings import service
    sop = db.row("SELECT sop_id, title, status FROM onboarding_sop WHERE sop_id = %s AND hotel_id = %s", (sop_id, hotel_id))
    if not sop:
        return {"ok": False, "error": "no SOP " + str(sop_id) + " at this hotel"}
    if sop["status"] != "proposed":
        return {"ok": False, "error": "SOP " + sop_id + " is already " + sop["status"]}
    chosen = [key for key in setting_keys or () if key]
    proposed = {row["setting_key"] for row in db.rows("SELECT setting_key FROM onboarding_sop_link WHERE sop_id = %s", (sop_id,))}
    if set(chosen) - proposed:
        return {"ok": False, "error": "SOP " + sop_id + " links only " + ", ".join(sorted(proposed))}
    if chosen:
        db.execute("DELETE FROM onboarding_sop_link WHERE sop_id = %s AND NOT (setting_key = ANY(%s))", (sop_id, chosen))
    excerpts = _text(sop_id).get("excerpts") or {}
    rows = _catalog()
    values = service.values_in_effect(hotel_id)
    outcomes = []
    for link in db.rows("SELECT setting_key FROM onboarding_sop_link WHERE sop_id = %s AND status = 'proposed'", (sop_id,)):
        key = link["setting_key"]
        done = _save_part(hotel_id, user_id, key, rows[key], values[key], excerpts.get(key, "")) if key in values else None
        status = "waiting_value" if done is None else ("saved" if done.get("ok") else "failed")
        detail = "" if done is None else str(done.get("error") or ("stored" if done.get("sop_saved") else "already the SOP in effect"))
        db.execute("UPDATE onboarding_sop_link SET status = %s, detail = %s, saved_at = CASE WHEN %s = 'saved' THEN now() END "
                   "WHERE sop_id = %s AND setting_key = %s", (status, detail, status, sop_id, key))
        outcomes.append({"setting_key": key, "status": status, "detail": detail})
    db.execute("UPDATE onboarding_sop SET status = 'confirmed', confirmed_at = now() WHERE sop_id = %s", (sop_id,))
    return {"ok": True, "sop_id": sop_id, "title": sop["title"], "links": outcomes}


def reject(hotel_id, sop_id):
    changed = db.execute("UPDATE onboarding_sop SET status = 'rejected' WHERE sop_id = %s AND hotel_id = %s AND status = 'proposed'",
                         (sop_id, hotel_id))
    return {"ok": bool(changed), "sop_id": sop_id, "error": "" if changed else "no proposed SOP " + str(sop_id)}


def waiting(hotel_id, key):
    """(sop_ids, text) of confirmed parts waiting for this setting's value."""
    found = db.rows("SELECT l.sop_id FROM onboarding_sop_link l JOIN onboarding_sop s ON s.sop_id = l.sop_id "
                    "WHERE l.hotel_id = %s AND l.setting_key = %s AND l.status = 'waiting_value' ORDER BY s.confirmed_at", (hotel_id, key))
    ids = [row["sop_id"] for row in found]
    return ids, "\n\n".join((_text(sop_id).get("excerpts") or {}).get(key, "") for sop_id in ids).strip()


def attached(sop_ids, key):
    if sop_ids:
        db.execute("UPDATE onboarding_sop_link SET status = 'saved', detail = 'stored with the value', saved_at = now() "
                   "WHERE sop_id = ANY(%s) AND setting_key = %s", (list(sop_ids), key))


def listing(hotel_id):
    sops = db.rows("SELECT sop_id, title, source, file_name, status, created_at, confirmed_at FROM onboarding_sop WHERE hotel_id = %s "
                   "ORDER BY created_at", (hotel_id,))
    links = db.rows("SELECT sop_id, setting_key, summary, status, detail FROM onboarding_sop_link WHERE hotel_id = %s", (hotel_id,))
    sections = db.rows("SELECT sop_id, seq, title, summary, setting_keys FROM onboarding_sop_section WHERE hotel_id = %s ORDER BY seq",
                       (hotel_id,))
    return [dict(sop, summary=_text(sop["sop_id"]).get("summary") or "", links=[link for link in links if link["sop_id"] == sop["sop_id"]],
                 sections=[section for section in sections if section["sop_id"] == sop["sop_id"]]) for sop in sops]
