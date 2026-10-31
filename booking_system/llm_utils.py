"""
booking_system/llm_utils.py

All LLM-powered utility functions for LEON PMS tools.
- unified_parser: parameter extraction from user input and dialog history
- unified_execution_detector: detects user intent to execute (confirm/proceed)
No other LLM calls should exist outside this module.
"""
import os
import json
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

HAIKU_MODEL = "claude-haiku-5-5"


def haiku():
    """Claude Haiku 5.5 at high effort."""
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

def unified_parser(user_input, dialog_history, param_list, instruction=None, llm=None):
    """
    Unified parser for extracting parameters from user input and dialog history.
    Args:
        user_input (str): The current user input.
        dialog_history (str): The dialog history/context.
        param_list (list of str): List of parameter names to extract.
        instruction (str, optional): Custom instruction for the parser. If None, uses default.
        llm: Optional LLM instance. If None, creates a new one.
    Returns:
        dict: Extracted parameters (empty string for missing/ambiguous).
    """
    if instruction is None:
        param_str = ', '.join(param_list)
        instruction = f"You are a parser of hotel business, extract {param_str} from dialog_history."
    prompt = f"""
{instruction}

Text: "{user_input}"

Dialog history:
{dialog_history}

Return ONLY valid JSON with these keys:
{json.dumps({k: "" for k in param_list}, indent=2)}
If a parameter is missing or ambiguous, return an empty string for that field.
"""
    if llm is None:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
    try:
        response = llm.invoke([{"role": "user", "content": prompt}])
        response_content = response.content.strip()
        # Clean JSON response
        if response_content.startswith('```json'):
            response_content = response_content.replace('```json', '').replace('```', '').strip()
        elif response_content.startswith('```'):
            response_content = response_content.replace('```', '').strip()
        parsed_data = json.loads(response_content)
        # Ensure all keys present
        for k in param_list:
            if k not in parsed_data:
                parsed_data[k] = ""
        return parsed_data
    except Exception as e:
        # On error, return all empty
        return {k: "" for k in param_list}

def unified_execution_detector(user_confirmation, llm=None):
    """
    Unified execution detector for LEON PMS tools.
    Args:
        user_confirmation (str): The user's confirmation or follow-up input.
        llm: Optional LLM instance. If None, creates a new one.
    Returns:
        str: 'execute' if user intent is to proceed (confirm, proceed, book now, etc.), 'none' otherwise.
    """
    prompt = (
        "Return execute if the user says confirm, proceed, book now, etc. Otherwise, return none.\n"
        f"User input: {user_confirmation}"
    )
    if llm is None:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            openai_api_key=os.getenv('OPENAI_API_KEY')
        )
    try:
        response = llm.invoke([{"role": "user", "content": prompt}])
        result = response.content.strip().upper()
        if "execute" in result:
            return "execute"
        return "none"
    except Exception:
        return "none"

# Example usage for demonstration
if __name__ == "__main__":
    user_input = "Book a Deluxe Room for John Tan, +60123456789, for tomorrow."
    dialog_history = ""
    param_list = ["guest_phone", "room_type", "check_in_date"]
    result = unified_parser(user_input, dialog_history, param_list)
    print("Extraction result:", result)

    confirmation = "Yes, please proceed with the booking."
    exec_result = unified_execution_detector(confirmation)
    print("Execution detector result:", exec_result) 