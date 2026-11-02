"""The onboarding model: gpt-6.1-sol at low reasoning effort, the same Responses-API client as the catalog agent. chat() is the
tool-calling chat model; structured() is one strict JSON-schema reply (mapping proposals, SOP splits, the reply step)."""

import json
import os

MODEL = "gpt-6.1-sol"
EFFORT = "low"
TIMEOUT = 180


def chat():
    import httpx
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=MODEL,
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=httpx.Timeout(TIMEOUT),
        use_responses_api=True,
        reasoning_effort=EFFORT,
    )


def structured(instructions, payload, name, schema, files=()):
    """One reply that matches schema, parsed. files: [{"file_name", "data_url"}] sent as input files (a PDF SOP)."""
    import httpx
    from openai import OpenAI
    content = [{"type": "input_text", "text": payload if isinstance(payload, str) else json.dumps(payload, default=str)}]
    content += [{"type": "input_file", "filename": item["file_name"], "file_data": item["data_url"]} for item in files]
    response = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=httpx.Timeout(TIMEOUT)).responses.create(
        model=MODEL,
        instructions=instructions,
        input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
        reasoning={"effort": EFFORT},
        store=False,
    )
    return json.loads(str(response.output_text or ""))
