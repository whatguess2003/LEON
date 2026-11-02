"""The simulated hotel manager and the judge's figure reader. Both use gpt-6.1-sol at low reasoning effort, the same client
style as the Catalog Agent. The simulator never sees ground truth; the reader only reports what a reply states."""

import json
import os

MODEL = "gpt-6.1-sol"
EFFORT = "low"
USAGE = {"simulator": {"calls": 0, "input_tokens": 0, "output_tokens": 0}, "judge": {"calls": 0, "input_tokens": 0, "output_tokens": 0}}


def _llm():
    import httpx
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=MODEL,
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=httpx.Timeout(180),
        use_responses_api=True,
        reasoning_effort=EFFORT,
    )


def _structured(role, schema, instructions, content):
    runnable = _llm().with_structured_output(schema, method="json_schema", strict=True, include_raw=True)
    out = runnable.invoke([{"role": "system", "content": instructions}, {"role": "user", "content": content}])
    usage = getattr(out.get("raw"), "usage_metadata", None) or {}
    USAGE[role]["calls"] += 1
    USAGE[role]["input_tokens"] += int(usage.get("input_tokens") or 0)
    USAGE[role]["output_tokens"] += int(usage.get("output_tokens") or 0)
    if out.get("parsing_error") or not out.get("parsed"):
        raise RuntimeError(role + " output could not be read: " + str(out.get("parsing_error")))
    return out["parsed"]


SIMULATOR_SCHEMA = {
    "title": "manager_turn",
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["continue", "done", "give_up"]},
        "step": {"type": "integer"},
        "message": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["status", "step", "message", "reason"],
    "additionalProperties": False,
}

SIMULATOR_INSTRUCTIONS = """You play the general manager of SANDBOX HOTEL talking to Leon, the hotel's desk assistant, in a chat box.
You pursue the goal steps in order, one step per message, in natural, varied wording a real manager would type (short, no
bullet lists, no step numbers, never mention a goal, a test, or a simulation). Use the persona hint for tone.

Each turn decide:
- continue: write the next message. step is the goal step (1-based) the message pursues.
- done: every goal step has been answered by Leon with a concrete figure or the action was confirmed done. message is "".
- give_up: Leon failed the same step twice (no figure, an error, an unrelated reply, or it refused), or the conversation cannot go on. message is "".

Rules:
- When Leon asks you a clarifying or definition question, answer it in one short sentence, consistently with your definitions
  below, in your own words. Then continue the goal on the following turn if Leon has not answered yet.
- When Leon asks you to confirm an action, confirm it. When Leon asks for a missing booking detail, give it from the goal.
- When Leon tacks an unrelated "One more thing" question onto an answer, ignore it: do not answer it, and judge the step by the
  answer before it (status done when that was the last step).
- When Leon offers to remember a report for you, say no thanks.
- You cannot see the hotel's data. Never correct Leon's figures unless a goal step tells you to complain or push back.
- If Leon's reply does not contain the figure the step asked for, ask again differently once.
- You have at most {max_turns} messages. Turn {turn} of {max_turns} is next."""


def manager_turn(scenario, transcript, turn, max_turns, guest):
    goal = [step.replace("{guest}", guest) for step in scenario["goal"]]
    from fixtures.stress.scenarios import PERSONA_DEFINITIONS
    content = json.dumps({
        "persona_hint": scenario["persona"],
        "goal_steps": goal,
        "your_definitions": PERSONA_DEFINITIONS,
        "conversation_so_far": [{"you": item["manager"], "leon": item["desk"]} for item in transcript],
    }, ensure_ascii=False)
    return _structured("simulator", SIMULATOR_SCHEMA,
                       SIMULATOR_INSTRUCTIONS.replace("{max_turns}", str(max_turns)).replace("{turn}", str(turn)), content)


REWORD_SCHEMA = {
    "title": "reworded",
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}

REWORD_INSTRUCTIONS = """You are the same hotel general manager. Rewrite your message as you would naturally type it on another
day: the same request with the same figures, period and references to the earlier conversation, in different words. One short
message, no lists, never mention rewriting."""


def reword(scenario, transcript, message):
    content = json.dumps({"persona_hint": scenario["persona"], "conversation_so_far": [{"you": item["manager"], "leon": item["desk"]} for item in transcript],
                          "message_to_reword": message}, ensure_ascii=False)
    return _structured("simulator", REWORD_SCHEMA, REWORD_INSTRUCTIONS, content)["message"]


READER_INSTRUCTIONS = """You read one reply from a hotel desk assistant and report which of the listed figures it explicitly states.
For each figure: stated is true only when the reply gives a value for exactly that figure. Do not compute or infer values the
reply does not give, except converting a directly stated fraction to a percentage (3 of 5 rooms -> 60). A reply that only
asks a question states nothing. number: the value as a plain number (no currency, no % sign) for count, money, pct, minutes and
hours figures (hours as decimal hours, 7h30m -> 7.5), else null. text: for text figures the name or room number as written, for date figures the date as YYYY-MM-DD
(today is {today}), else "". "none" or "no one" for a count means 0. Use null and "" when not stated."""


def read_figures(figures, manager_message, reply, today):
    names = [item["name"] for item in figures]
    schema = {
        "title": "figures_read",
        "type": "object",
        "properties": {"figures": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": names},
                "stated": {"type": "boolean"},
                "number": {"type": ["number", "null"]},
                "text": {"type": "string"},
            },
            "required": ["name", "stated", "number", "text"],
            "additionalProperties": False,
        }}},
        "required": ["figures"],
        "additionalProperties": False,
    }
    content = json.dumps({"figures": figures, "manager_asked": manager_message, "assistant_reply": reply}, ensure_ascii=False)
    parsed = _structured("judge", schema, READER_INSTRUCTIONS.replace("{today}", str(today)), content)
    return {item["name"]: item for item in parsed["figures"] if item["stated"]}
