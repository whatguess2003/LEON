"""WhatsApp Cloud API media. Upload returns an id for the messages API. Download reads the short-lived URL."""
import json
import os
import urllib.error
import urllib.request

_LIMIT = 100 * 1024 * 1024
_KIND_PREFIXES = (("image/", "image"), ("video/", "video"), ("audio/", "audio"))
_CAPTION_KINDS = {"image", "video", "document"}
_ALLOWED = {
    "image/jpeg", "image/png", "image/webp",
    "audio/aac", "audio/amr", "audio/mpeg", "audio/mp4", "audio/ogg",
    "video/mp4", "video/3gpp",
    "application/pdf",
}
_ALIASES = {"image/jpg": "image/jpeg"}
_SUFFIXES = (
    (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp"),
    (".aac", "audio/aac"), (".amr", "audio/amr"), (".mp3", "audio/mpeg"), (".m4a", "audio/mp4"), (".ogg", "audio/ogg"),
    (".mp4", "video/mp4"), (".3gp", "video/3gpp"),
    (".pdf", "application/pdf"),
)


class _KeepAuth(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        prepared = super().redirect_request(req, fp, code, msg, headers, newurl)
        if prepared is None:
            return None
        auth = req.get_header("Authorization")
        if auth:
            prepared.add_unredirected_header("Authorization", auth)
        return prepared


def _opener():
    return urllib.request.build_opener(_KeepAuth)


def _token():
    return str(os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()


def _phone_number_id():
    return str(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()


def _read_limited(response):
    chunks = []
    total = 0
    while True:
        block = response.read(256 * 1024)
        if not block:
            break
        total += len(block)
        if total > _LIMIT:
            return None
        chunks.append(block)
    return b"".join(chunks)


def _error(exc):
    raw = exc.read().decode()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    return str(((parsed.get("error") or {}).get("message")) or "WhatsApp media failed")


def accepted_mime(filename: str, mime: str) -> str:
    """Image, audio, video, or PDF. The browser type wins, then the file name."""
    content = _ALIASES.get(str(mime or "").split(";")[0].strip().lower(), str(mime or "").split(";")[0].strip().lower())
    if content in _ALLOWED:
        return content
    name = str(filename or "").lower()
    matched = [value for suffix, value in _SUFFIXES if name.endswith(suffix)]
    return matched[0] if matched else ""


def media_kind(mime: str) -> str:
    """Cloud API message type for a file. Anything else is a document."""
    text = str(mime or "").lower()
    for prefix, kind in _KIND_PREFIXES:
        if text.startswith(prefix):
            return kind
    return "document"


def download_media(media_id: str) -> dict:
    """Retrieve the media URL, then download the bytes before that URL expires."""
    token = _token()
    if not token:
        return {"error": "WhatsApp access token is not configured"}
    opener = _opener()
    info_request = urllib.request.Request(
        "https://graph.facebook.com/v23.0/" + str(media_id or ""),
        headers={"Authorization": "Bearer " + token, "User-Agent": "LEON-PMS/1.0.0"},
    )
    try:
        with opener.open(info_request, timeout=25) as response:
            info = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": _error(exc)}
    except Exception as exc:
        return {"error": str(exc)}
    url = str((info or {}).get("url") or "")
    if not url:
        return {"error": "WhatsApp media URL is missing"}
    file_request = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token, "User-Agent": "LEON-PMS/1.0.0"},
    )
    try:
        with opener.open(file_request, timeout=60) as response:
            data = _read_limited(response)
            mime = str(response.headers.get("Content-Type") or info.get("mime_type") or "application/octet-stream")
    except urllib.error.HTTPError as exc:
        return {"error": _error(exc)}
    except Exception as exc:
        return {"error": str(exc)}
    if data is None:
        return {"error": "WhatsApp media is too large"}
    return {"data": data, "mime": mime.split(";")[0].strip()}


def upload_media(filename: str, mime: str, data: bytes) -> dict:
    """Upload one file. The returned id is what the messages API sends."""
    token = _token()
    phone_number_id = _phone_number_id()
    if not token or not phone_number_id:
        return {"error": "WhatsApp access token is not configured"}
    if not data:
        return {"error": "File is required"}
    if len(data) > _LIMIT:
        return {"error": "WhatsApp media is too large"}
    safe_name = "".join(ch for ch in str(filename or "file") if ch not in "\"\r\n\\") or "file"
    content_type = accepted_mime(safe_name, mime)
    if not content_type:
        return {"error": "File must be an image, audio, video, or PDF"}
    boundary = "leonmedia"
    body = (
        ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n").encode()
        + ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + safe_name + "\"\r\nContent-Type: " + content_type + "\r\n\r\n").encode()
        + data
        + ("\r\n--" + boundary + "--\r\n").encode()
    )
    request = urllib.request.Request(
        "https://graph.facebook.com/v23.0/" + phone_number_id + "/media",
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "LEON-PMS/1.0.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            parsed = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": _error(exc)}
    except Exception as exc:
        return {"error": str(exc)}
    media_id = str((parsed or {}).get("id") or "")
    if not media_id:
        return {"error": "WhatsApp media id is missing"}
    return {"id": media_id, "kind": media_kind(content_type), "mime": content_type, "filename": safe_name}


def caption_allowed(kind: str) -> bool:
    return str(kind or "") in _CAPTION_KINDS
