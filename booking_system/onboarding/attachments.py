"""A file dropped in the onboarding chat. The extension picks the handler: a table is an import, a document is an SOP, an image is kept as media. The chat does not ask which."""

import base64

from booking_system import db
from booking_system.content_store import client
from booking_system.hotel_registry import new_id
from booking_system.onboarding import files, importer, sops

media_files = client["leon_onboarding"]["media"]
MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
MAX_MEDIA_BYTES = 8 * 1024 * 1024


def _kinds():
    found = {}
    for ext in files.TABLE_READERS:
        found[ext] = "table"
    for ext in files.DOCUMENT_READERS:
        if ext not in found:
            found[ext] = "document"
    for ext in MEDIA:
        found[ext] = "media"
    return found


KINDS = _kinds()


def _table(hotel_id, user_id, onboarding_id, modules, file_name, content_base64):
    result = importer.add_file(hotel_id, user_id, onboarding_id, modules, file_name, content_base64, "")
    if result.get("ok"):
        result["summary"] = result["file_name"] + " read as " + result["entity"] + ", " + str(result["rows"]) + " rows. Mapping is proposed; nothing is imported until you confirm."
    return result


def _document(hotel_id, user_id, onboarding_id, modules, file_name, content_base64):
    try:
        original = files.decode(content_base64)
        given = files.read_document(file_name, original)
    except files.Unreadable as reason:
        return {"ok": False, "error": str(reason)}
    title = str(file_name).rsplit(".", 1)[0]
    result = sops.submit(hotel_id, user_id, onboarding_id, title, "upload", file_name, given, original)
    if result.get("ok"):
        result["summary"] = result["title"] + " read (" + str(len(result["sections"])) + " sections). " + result["summary"] + " Nothing is saved until you confirm."
    return result


def _media(hotel_id, user_id, onboarding_id, modules, file_name, content_base64):
    try:
        original = files.decode(content_base64)
    except files.Unreadable as reason:
        return {"ok": False, "error": str(reason)}
    if len(original) > MAX_MEDIA_BYTES:
        return {"ok": False, "error": "the image is larger than 8 MB"}
    media_id = new_id("med")
    mime = MEDIA[files.extension(file_name)]

    def run(cur):
        media_files.write_in(cur, media_id, {"_id": media_id, "media_id": media_id, "hotel_id": hotel_id, "onboarding_id": onboarding_id,
                                             "file_name": str(file_name)[:255], "mime": mime, "content_base64": base64.b64encode(original).decode("ascii")})
        return {"ok": True, "media_id": media_id, "file_name": file_name,
                "summary": file_name + " is kept with this onboarding. It is an image, so no text was read from it."}
    return db.transaction(run)


HANDLERS = {"table": _table, "document": _document, "media": _media}


def receive(hotel_id, user_id, onboarding_id, modules, file_name, content_base64):
    """Read one chat attachment and hand it to the handler for its extension."""
    if not hotel_id:
        return {"ok": False, "error": "the hotel is not set up yet; tell Leon the hotel name, then attach the file again"}
    kind = KINDS.get(files.extension(file_name))
    handler = HANDLERS.get(kind or "")
    if not handler:
        return {"ok": False, "error": "attach a CSV, XLSX, PDF, DOCX, text file, or an image (JPG, PNG, WEBP, GIF)"}
    result = handler(hotel_id, user_id, onboarding_id, modules, file_name, content_base64)
    result["kind"] = kind
    return result
