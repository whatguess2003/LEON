"""Load approved WhatsApp templates from Meta and fill their fields before the screen shows them."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dotenv import load_dotenv

load_dotenv()

_waba = ""
_cached_at = 0.0
_cached_rows = None


def _graph(path):
    token = str(os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
    if not token:
        return {"error": "WhatsApp access token is not configured"}
    request = urllib.request.Request(
        "https://graph.facebook.com/v23.0/" + path,
        headers={"Authorization": "Bearer " + token, "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        message = str(((parsed.get("error") or {}).get("message")) or "WhatsApp templates could not be loaded")
        return {"error": message}
    except Exception as exc:
        return {"error": str(exc)}


def _waba_id():
    global _waba
    if _waba:
        return _waba, None
    phone = str(os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    if not phone:
        return "", {"error": "WhatsApp phone number is not configured"}
    body = _graph(phone + "?fields=health_status")
    if body.get("error"):
        return "", body
    for entity in ((body.get("health_status") or {}).get("entities") or []):
        if str(entity.get("entity_type") or "") == "WABA" and entity.get("id"):
            _waba = str(entity.get("id"))
            return _waba, None
    return "", {"error": "WhatsApp business account was not found"}


def _fetch_templates():
    global _cached_at, _cached_rows
    now = time.time()
    if _cached_rows is not None and now - _cached_at < 60:
        return _cached_rows
    waba, failed = _waba_id()
    if failed:
        return failed
    rows = []
    fields = "name,language,status,parameter_format,components"
    path = waba + "/message_templates?limit=100&fields=" + fields
    while path:
        body = _graph(path)
        if body.get("error"):
            return body
        rows.extend(body.get("data") or [])
        after = str((((body.get("paging") or {}).get("cursors") or {}).get("after")) or "")
        if after and (body.get("paging") or {}).get("next"):
            path = waba + "/message_templates?limit=100&fields=" + fields + "&after=" + urllib.parse.quote(after)
        else:
            path = ""
    _cached_rows = rows
    _cached_at = now
    return rows


def _markers(text):
    found = []
    rest = str(text or "")
    while "{{" in rest and "}}" in rest:
        start = rest.index("{{") + 2
        end = rest.index("}}", start)
        found.append(rest[start:end].strip())
        rest = rest[end + 2:]
    return found


def _body(item):
    for component in item.get("components") or []:
        if str(component.get("type") or "").upper() == "BODY":
            return component
    return {}


def _examples(component):
    example = component.get("example") or {}
    named = {}
    for row in example.get("body_text_named_params") or []:
        named[str(row.get("param_name") or "")] = str(row.get("example") or "")
    grid = example.get("body_text") or []
    positional = grid[0] if grid and isinstance(grid[0], list) else []
    return named, [str(value or "") for value in positional]


def _fill(text, context, named_examples, positional):
    sentence = str(text or "")
    values = []
    names = []
    for index, marker in enumerate(_markers(sentence)):
        if index == 0:
            value = str(context.get("guest_name") or "").strip()
        else:
            found = context.get(marker)
            if found is None and marker in named_examples:
                found = named_examples[marker]
            if found is None and index < len(positional):
                found = positional[index]
            value = str(found or "").strip()
        if not value:
            return "", [], []
        values.append(value)
        names.append(marker)
        sentence = sentence.replace("{{" + marker + "}}", value)
    return sentence, values, names


def _button(item, context):
    for component in item.get("components") or []:
        if str(component.get("type") or "").upper() != "BUTTONS":
            continue
        for button in component.get("buttons") or []:
            url = str(button.get("url") or "")
            if "{{" in url and "}}" in url:
                return str(context.get("guest_link") or "").strip()
    return ""


def _has_url_button(item):
    for component in item.get("components") or []:
        if str(component.get("type") or "").upper() != "BUTTONS":
            continue
        for button in component.get("buttons") or []:
            if str(button.get("type") or "").upper() == "URL":
                return True
    return False


def _key(item):
    return str(item.get("name") or "") + "/" + str(item.get("language") or "")


def _approved():
    loaded = _fetch_templates()
    if isinstance(loaded, dict):
        return loaded
    rows = []
    for item in loaded:
        if str(item.get("status") or "") == "APPROVED" and item.get("name") and item.get("language"):
            rows.append(item)
    return rows


def _render(item, context):
    component = _body(item)
    named_examples, positional = _examples(component)
    sentence, values, names = _fill(component.get("text"), context, named_examples, positional)
    if not sentence:
        return {"error": "WhatsApp template fields are empty"}
    return {
        "key": _key(item),
        "template_name": str(item.get("name") or ""),
        "language": str(item.get("language") or ""),
        "sentence": sentence,
        "values": values,
        "names": names,
        "named": str(item.get("parameter_format") or "") == "NAMED",
        "button": _button(item, context),
    }


def active_templates(context):
    """Approved templates with the guest name and the other fields already filled in."""
    loaded = _approved()
    if isinstance(loaded, dict):
        return loaded
    rows = []
    for item in loaded:
        rendered = _render(item, context)
        if rendered.get("error"):
            continue
        rows.append({"key": rendered["key"], "template_name": rendered["template_name"], "sentence": rendered["sentence"]})
    return rows


def render_template(key, context):
    """One approved template ready to send."""
    loaded = _approved()
    if isinstance(loaded, dict):
        return loaded
    for item in loaded:
        if _key(item) == str(key or ""):
            return _render(item, context)
    return {"error": "WhatsApp template is not approved"}


def link_template_key():
    """The approved template that carries the verify-account button."""
    loaded = _approved()
    if isinstance(loaded, dict):
        return ""
    for item in loaded:
        if _has_url_button(item):
            return _key(item)
    return ""
