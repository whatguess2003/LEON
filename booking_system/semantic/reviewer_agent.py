"""One-shot Reviewer Agent. It judges a proposal and does not execute SQL or publish."""

import json
import os

_REVIEW_MODEL = "gpt-6.1-sol"
_REVIEW_EFFORT = "max"


def accept_verdict(raw):
    """Keep a structured verdict. An unreadable decision fails closed as REJECT."""
    raw = raw or {}
    decision = str(raw.get("decision") or "")
    if decision not in ("APPROVE", "REVISE", "REJECT"):
        decision = "REJECT"
    findings = []
    for item in raw.get("findings") or []:
        if not isinstance(item, dict):
            continue
        findings.append({
            "severity": str(item.get("severity") or ""),
            "category": str(item.get("category") or ""),
            "issue": str(item.get("issue") or ""),
            "required_fix": str(item.get("required_fix") or ""),
        })
    unresolved = [str(item) for item in (raw.get("unresolved_questions") or []) if str(item)]
    if decision == "APPROVE" and (unresolved or any(item.get("severity") == "critical" for item in findings)):
        decision = "REVISE"
    if decision != "APPROVE":
        for item in findings:
            if not item["issue"]:
                item["issue"] = "The proposal was not approved."
            if not item["required_fix"]:
                item["required_fix"] = item["issue"]
        if not findings:
            findings.append({
                "severity": "critical",
                "category": "review",
                "issue": "The proposal was not approved.",
                "required_fix": "Make the procedure answer the original question.",
            })
    confidence = str(raw.get("confidence") or "")
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    return {
        "decision": decision,
        "confidence": confidence,
        "findings": findings,
        "unresolved_questions": unresolved,
    }


def review_once(package, trace=None):
    """Ask the reviewer for one verdict. The model has no tools. Tokens are metered against the trace's hotel."""
    from openai import OpenAI
    from booking_system.semantic.gate import record_usage

    path = os.path.join(os.path.dirname(__file__), "reviewer_agent_prompt.md")
    with open(path, encoding="utf-8") as handle:
        prompt = handle.read().strip()
    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["APPROVE", "REVISE", "REJECT"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                        "category": {"type": "string"},
                        "issue": {"type": "string"},
                        "required_fix": {"type": "string"},
                    },
                    "required": ["severity", "category", "issue", "required_fix"],
                    "additionalProperties": False,
                },
            },
            "unresolved_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["decision", "confidence", "findings", "unresolved_questions"],
        "additionalProperties": False,
    }
    import httpx
    try:
        response = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=httpx.Timeout(None)).responses.create(
            model=_REVIEW_MODEL,
            instructions=prompt,
            input=[{
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(package or {}, default=str)}],
            }],
            text={"format": {"type": "json_schema", "name": "recipe_review", "strict": True, "schema": schema}},
            reasoning={"effort": _REVIEW_EFFORT},
            store=False,
            max_output_tokens=64000,
        )
        usage = getattr(response, "usage", None)
        if trace and usage:
            record_usage(trace, "reviewer", getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
        raw = json.loads(str(getattr(response, "output_text", "") or ""))
    except Exception:
        raw = {
            "decision": "REJECT",
            "confidence": "low",
            "findings": [{
                "severity": "critical",
                "category": "review_unavailable",
                "issue": "The review response could not be read.",
                "required_fix": "Review again when the reviewer is available.",
            }],
            "unresolved_questions": [],
        }
    return accept_verdict(raw)
