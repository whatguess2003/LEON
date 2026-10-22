#!/usr/bin/env python3
"""
SIMPLE NAVIGATION AGENT (NO LANGCHAIN)
Determines which UI route to open based on a short user instruction.
Uses OpenAI function-calling to select a topic; returns route + params.
"""

from __future__ import annotations

import os
import json
from typing import Dict, Any, Optional
from dotenv import load_dotenv

import httpx

load_dotenv()


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Explicit model for navigation agent (no .env required)
OPENAI_MODEL = "gpt-4o-mini"


TOPIC_TO_ROUTE = {
    "rooms": "/rooms.html",
    "room": "/rooms.html",
    "housekeeping": "/housekeeping.html",
    "bookings": "/bookings.html",
    "booking": "/bookings.html",
    "analytics": "/analytics.html",
    "dashboard": "/dashboard.html",
    "inventory": "/inventory.html",
    "operations": "/operations.html",
    "guests": "/guests.html",
    "home": "/index.html",
    "index": "/index.html",
}


# Fast local keyword-based guesser for immediate routing (no external calls)
_TOPIC_KEYWORDS = {
    "rooms": {"room", "rooms", "assignments"},
    "housekeeping": {"housekeeping", "cleaning", "dirty", "inspected", "ready", "maid", "attendant"},
    "bookings": {"booking", "bookings", "reserve", "reservation", "check-in", "checkin", "check out", "checkout"},
    "analytics": {"analytics", "report", "reports", "occupancy", "revenue", "performance"},
    "dashboard": {"dashboard", "home", "overview"},
    "inventory": {"inventory", "pricing", "rates", "rate", "quantity", "stock"},
    "operations": {"operations", "maintenance", "tasks", "ops"},
    "guests": {"guest", "guests", "profiles", "loyalty"},
    "index": {"index", "home", "landing"},
}

def fast_decide_navigation(user_text: str) -> Optional[Dict[str, Any]]:
    if not user_text:
        return None
    text = str(user_text).strip().lower()
    tokens = set(text.replace("/", " ").replace("-", " ").split())
    best_topic = None
    best_score = -1
    for topic, keywords in _TOPIC_KEYWORDS.items():
        score = len(tokens.intersection(keywords))
        if score > best_score and score > 0:
            best_score = score
            best_topic = topic
    if best_topic:
        return {"topic": best_topic, "route": TOPIC_TO_ROUTE.get(best_topic, "/index.html"), "params": {}}
    return None


def _build_tools_spec() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "decide_navigation",
                "description": "Pick a UI topic to navigate to, based on the instruction.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "One of: rooms, bookings, analytics, dashboard, inventory, operations, guests, index",
                        },
                        "params": {
                            "type": "object",
                            "description": "Optional query params to pass to the page, such as hotel or room_type.",
                            "additionalProperties": {
                                "type": ["string", "number", "boolean"]
                            },
                        },
                    },
                    "required": ["topic"],
                },
            },
        }
    ]


def _system_prompt() -> str:
    return (
        "You are a minimal navigation router. "
        "Given a short instruction, choose exactly one topic and call decide_navigation. "
        "Never answer with text; always call the function. Keep params minimal."
    )


def decide_navigation_from_text(user_text: str) -> Optional[Dict[str, Any]]:
    if not OPENAI_API_KEY:
        return None

    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": user_text.strip()},
        ],
        "tools": _build_tools_spec(),
        "tool_choice": {"type": "function", "function": {"name": "decide_navigation"}},
        "temperature": 0.0,
        "max_tokens": 64,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=15.0) as client:
        resp = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        choice = data["choices"][0]["message"]
        tool_calls = choice.get("tool_calls") or []
        if not tool_calls:
            return None
        args_raw = tool_calls[0]["function"]["arguments"]
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        topic = str(args.get("topic", "")).lower().strip()
        params = args.get("params") or {}
        route = TOPIC_TO_ROUTE.get(topic)
        if not route:
            return None
        return {"topic": topic, "route": route, "params": params}
    except Exception:
        return None


__all__ = [
    "decide_navigation_from_text",
    "TOPIC_TO_ROUTE",
]


