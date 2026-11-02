"""
booking_system/llm_utils.py

Shared model clients: haiku (LangChain chat model), haiku_text (one Anthropic reply), model_text.
"""
import os
import json

HAIKU_MODEL = "claude-haiku-5-5"


def haiku():
    """Claude Haiku 5.5 at high effort. langchain_anthropic loads transformers and torch, so it is imported on first use."""
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=HAIKU_MODEL,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        max_tokens=4096,
        default_request_timeout=60,
        model_kwargs={"output_config": {"effort": "high"}},
    )


def _data_url(value):
    text = str(value or "")
    marker = ";base64,"
    if not text.startswith("data:") or marker not in text:
        return "", ""
    head, data = text.split(marker, 1)
    return head[5:], data


def _haiku_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks = []
    for item in content or []:
        if isinstance(item, str):
            blocks.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            blocks.append({"type": "text", "text": str(item or "")})
            continue
        kind = str(item.get("type") or "")
        if kind in ("text", "input_text"):
            blocks.append({"type": "text", "text": str(item.get("text") or "")})
            continue
        if kind in ("image", "input_image"):
            mime, data = _data_url(item.get("image_url") or item.get("url") or "")
            if mime and data:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
            continue
        if kind in ("document", "input_file"):
            mime, data = _data_url(item.get("file_data") or "")
            if data:
                blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": mime or "application/pdf", "data": data},
                })
            continue
        blocks.append({"type": "text", "text": str(item.get("text") or "")})
    return blocks or [{"type": "text", "text": ""}]


def haiku_text(instructions, content, schema=None, max_tokens=1200, timeout=60):
    """One Haiku reply. JSON schema replies are parsed by the caller from this text."""
    import anthropic

    output_config = {"effort": "high"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    response = anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        timeout=timeout,
    ).messages.create(
        model=HAIKU_MODEL,
        max_tokens=max(int(max_tokens or 1200), 4096),
        system=str(instructions or ""),
        messages=[{"role": "user", "content": _haiku_blocks(content)}],
        output_config=output_config,
    )
    parts = []
    for block in response.content:
        if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
            parts.append(block.text)
    return "\n".join(parts).strip()


def model_text(message):
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or item or ""))
        return "\n".join(part for part in parts if part).strip()
    return str(content or "").strip()


def canonical_key(value):
    """Canonicalize natural key fields: uppercase and strip whitespace (for hotel_name, room_type, etc.)"""
    if isinstance(value, str):
        return value.strip().upper()
    return value

