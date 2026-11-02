"""Date values the desk writes itself. A confident day is MM/DD/YYYY. SQL consumes it with to_date(that string, 'MM/DD/YYYY').
Nothing in this module calls a model or reads a day out of a sentence."""

from datetime import timedelta

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _line(label, day, allowed):
    allowed.append(day.isoformat())
    return label + " " + day.isoformat()


def calendar():
    """Database date, the lines shown to the resolver, and the dates it may copy."""
    from booking_system.content_store import _with

    def run(cur):
        cur.execute("SELECT CURRENT_DATE")
        return cur.fetchone()[0]

    today = _with(run)
    monday = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    previous_end = month_start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    allowed = []
    lines = [
        "Hotel date calendar. Copy dates only from this list.",
        _line("Today " + _WEEKDAYS[today.weekday()], today, allowed),
        _line("Yesterday", today - timedelta(days=1), allowed),
        _line("Tomorrow", today + timedelta(days=1), allowed),
        _line("Right now", today, allowed),
        "This week",
    ]
    for offset in range(7):
        lines.append(_line(_WEEKDAYS[offset], monday + timedelta(days=offset), allowed))
    lines.append("Next week")
    for offset in range(7):
        lines.append(_line(_WEEKDAYS[offset], monday + timedelta(days=7 + offset), allowed))
    lines.append("Last week")
    for offset in range(7):
        lines.append(_line(_WEEKDAYS[offset], monday - timedelta(days=7 - offset), allowed))
    lines.append(_line("This month starts", month_start, allowed))
    lines.append(_line("This month ends", month_end, allowed))
    lines.append(_line("Last month starts", previous_start, allowed))
    lines.append(_line("Last month ends", previous_end, allowed))
    lines.append(_line("Next seven days start", today, allowed))
    lines.append(_line("Next seven days end", today + timedelta(days=6), allowed))
    lines.append(_line("This weekend Saturday", monday + timedelta(days=5), allowed))
    lines.append(_line("This weekend Sunday", monday + timedelta(days=6), allowed))
    unique = []
    for day in allowed:
        if day not in unique:
            unique.append(day)
    return "\n".join(lines), unique


def calendar_text():
    text, _allowed = calendar()
    return text


_SOURCES = ("latest_closed_business_date", "open_business_date", "calendar", "named_period", "history", "unresolved")
SQL_DATE = "to_date(the_date, 'MM/DD/YYYY')"
SQL_PERIOD = "to_char(to_date(the_date, 'MM/DD/YYYY'), 'YYYY-MM-DD')"
DATE_RULES = (
    "Copy dates from the date facts only. Write every date as MM/DD/YYYY. Do not guess a day, an offset, or an end. "
    "resolved_date is that MM/DD/YYYY when the day is settled, and empty when it is not. Empty means unresolved: "
    "period is empty, source is unresolved, and clarification is one spoken question that does not decide the day. "
    "Low confidence is unresolved. Do not fill a date in. "
    "today, yesterday, tomorrow, and tonight are the hotel-local calendar dates. Copy them. Do not ask the person to confirm them. "
    "A written calendar date that the sentence does not call a business date is that day, source calendar. Do not ask. "
    "A weekday such as Tuesday is unresolved unless the sentence says which one. Ask, name the reading and its calendar date, and do not pick this week or next week. "
    "The latest closed business date and the open business date copy business_dates, not the calendar day, including when the sentence also writes a calendar date. Do not ask. "
    "stated_date is a calendar date written in the sentence, as MM/DD/YYYY, or empty. "
    "correction names both dates when stated_date differs from resolved_date, and is empty when they match or stated_date is empty. "
    "A named range in named_periods or recipe_periods uses source named_period, period that key, and resolved_date the start. Do not ask. "
    "A single day is not a range key: period and resolved_date are that day. "
    "A written date that is not a clear calendar day and is not in business_dates is unresolved. The question names that date and asks. "
    "source is history when the sentence points at an earlier turn, or answers a clarification, and names no new day. "
    "Use the date that turn settled. A confirmation is that date. A different day in the answer is that day. "
    "A follow-up whose history does not settle the day is unresolved. "
    "Two dates the facts do not assign to the figures are unresolved. Ask which date goes with which figure. "
    "A span is resolved only when the facts name every day, or the sentence names one period key. "
    "Otherwise return one unresolved resolution for the whole span. Do not return a date for the part you can name. "
    "Ask which end date, or ask to confirm the span. Do not state an end you computed. "
    "A change to how the hotel operates that names no other day, including from now or from today, is hotel_local_today, source calendar. "
    "One resolution per distinct date. phrase is the words it comes from. "
    "Leave resolutions empty when the sentence names no day and points at no earlier day. "
    "Do not invent a date that is not in the facts."
)


def resolution_schema():
    """The date fields a desk language step writes. An unresolved item asks; it does not carry a day."""
    return {
        "type": "object",
        "properties": {
            "phrase": {"type": "string"},
            "source": {"type": "string", "enum": list(_SOURCES)},
            "period": {"type": "string"},
            "resolved_date": {"type": "string"},
            "stated_date": {"type": "string"},
            "correction": {"type": "string"},
            "unresolved": {"type": "boolean"},
            "clarification": {"type": "string"},
        },
        "required": ["phrase", "source", "period", "resolved_date", "stated_date", "correction", "unresolved", "clarification"],
        "additionalProperties": False,
    }


def _mdy(day):
    return day.strftime("%m/%d/%Y")


def _period_facts(today):
    """Named recipe ranges and their start, as MM/DD/YYYY, for a desk step to copy."""
    monday = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    previous_end = month_start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    return {
        "last_7_days": _mdy(today - timedelta(days=7)),
        "next_7_days": _mdy(today + timedelta(days=1)),
        "last_30_days": _mdy(today - timedelta(days=30)),
        "next_30_days": _mdy(today + timedelta(days=1)),
        "this_week": _mdy(monday),
        "last_week": _mdy(monday - timedelta(days=7)),
        "next_week": _mdy(monday + timedelta(days=7)),
        "month_to_date": _mdy(month_start),
        "this_month": _mdy(month_start),
        "last_month": _mdy(previous_start),
        "next_month": _mdy(next_month),
        "year_to_date": _mdy(today.replace(month=1, day=1)),
    }


def _weekday_facts(today):
    monday = today - timedelta(days=today.weekday())
    found = {}
    for offset in range(14):
        day = monday + timedelta(days=offset)
        label = _WEEKDAYS[offset % 7]
        if offset >= 7:
            label = label + " next"
        found[label] = _mdy(day)
    return found


def _facts(hotel_id, sentence, history):
    from booking_system.semantic.hotel import business_dates, hotel_today
    from booking_system.semantic.seed_general import _PERIOD_NAMES

    today = hotel_today(hotel_id) if hotel_id else None
    business = business_dates(hotel_id) if hotel_id else {}
    return {
        "hotel_local_today": _mdy(today) if today else "",
        "calendar": {
            "today": _mdy(today) if today else "",
            "yesterday": _mdy(today - timedelta(days=1)) if today else "",
            "tomorrow": _mdy(today + timedelta(days=1)) if today else "",
        },
        "weekdays": _weekday_facts(today) if today else {},
        "business_dates": {key: _mdy_text(value) for key, value in business.items()},
        "named_periods": _period_facts(today) if today else {},
        "recipe_periods": _PERIOD_NAMES,
        "history": str(history or ""),
        "sentence": str(sentence or ""),
    }


def _mdy_text(value):
    found = as_date(value)
    return _mdy(found) if found else str(value or "")


def as_date(value):
    """MM/DD/YYYY the desk wrote, or a stored YYYY-MM-DD, as a date. This converts the form. It does not choose a day."""
    from datetime import datetime
    from datetime import date as date_cls

    if isinstance(value, date_cls):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, raw in (("%m/%d/%Y", text), ("%Y-%m-%d", text[:10])):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def as_iso(value):
    found = as_date(value)
    return found.isoformat() if found else ""


def recipe_period(value):
    """YYYY-MM-DD for a recipe period column. Same conversion as to_char(to_date(value, 'MM/DD/YYYY'), 'YYYY-MM-DD'). A named key is unchanged."""
    found = as_date(value)
    return found.isoformat() if found else str(value or "")


def _kept(item):
    """Pass the model's fields through. An unresolved item has no date to use. An empty date is unresolved."""
    date = str(item.get("resolved_date") or "")
    period = str(item.get("period") or "")
    unresolved = bool(item.get("unresolved")) or not date
    clarification = str(item.get("clarification") or "") if unresolved else ""
    if unresolved:
        date = ""
        period = ""
    return {
        "phrase": str(item.get("phrase") or ""),
        "source": str(item.get("source") or ""),
        "period": period,
        "resolved_date": date,
        "stated_date": str(item.get("stated_date") or ""),
        "correction": "" if unresolved else str(item.get("correction") or ""),
        "unresolved": unresolved,
        "clarification": clarification,
    }


def _blank():
    return {"phrase": "", "source": "", "period": "", "resolved_date": "", "stated_date": "", "correction": "", "unresolved": False, "clarification": ""}


def date_facts(hotel_id):
    """Calendar facts a desk step may copy. The sentence is not an argument."""
    facts = _facts(hotel_id, "", "")
    facts.pop("sentence", None)
    facts.pop("history", None)
    return facts


def accept_resolutions(items):
    """Keep the date fields a desk step wrote. An empty date is unresolved."""
    found = []
    for item in items or []:
        if isinstance(item, dict):
            found.append(_kept(item))
    return {"resolutions": found}


def has_unresolved(resolved):
    return any(item.get("unresolved") for item in (resolved or {}).get("resolutions") or [] if isinstance(item, dict))


def pending_clarification(resolved):
    lines = []
    for item in (resolved or {}).get("resolutions") or []:
        if not isinstance(item, dict) or not item.get("unresolved"):
            continue
        text = str(item.get("clarification") or "").strip()
        if text and text not in lines:
            lines.append(text)
    return " ".join(lines)


def first_resolved(resolved):
    for item in (resolved or {}).get("resolutions") or []:
        if isinstance(item, dict) and not item.get("unresolved") and str(item.get("resolved_date") or ""):
            return str(item.get("resolved_date") or "")
    return ""


def usable_periods(resolved):
    """Period values the desk wrote. An empty period is not filled from resolved_date."""
    periods = []
    for item in (resolved or {}).get("resolutions") or []:
        if not isinstance(item, dict) or item.get("unresolved") or not str(item.get("resolved_date") or ""):
            continue
        period = str(item.get("period") or "")
        if period and period not in periods:
            periods.append(period)
    return periods


def bind(spec, resolved):
    """One resolved period replaces the spec's day. An unresolved resolution is not applied."""
    found = resolved if isinstance(resolved, dict) else {"resolutions": []}
    spec["dates"] = found
    periods = usable_periods(found)
    filters = list(spec.get("row_filters") or [])
    if len(periods) == 1 and filters:
        spec["row_filters"] = [dict(item, period=periods[0], day_offset=0) for item in filters]
    return spec


def payload(resolved):
    """The desk's date object, passed through. A list of days is not turned into resolutions."""
    if isinstance(resolved, dict):
        return resolved
    return {"resolutions": []}


def resolved_text(resolved):
    """resolved_date values the desk settled, as MM/DD/YYYY, separated by commas. Unresolved and empty are omitted."""
    found = []
    for item in (resolved or {}).get("resolutions") or []:
        if not isinstance(item, dict) or item.get("unresolved"):
            continue
        day = str(item.get("resolved_date") or "")
        if day and day not in found:
            found.append(day)
    return ", ".join(found)


def pass_stay_dates(brief):
    """A stay field is replaced with the MM/DD/YYYY values this step settled, or cleared when none are settled."""
    text = resolved_text((brief or {}).get("dates"))
    fields = ("stay_dates", "new_stay_dates", "stay_date")
    for group in ("informational_actions", "executional_actions"):
        for row in (brief or {}).get(group) or []:
            if not isinstance(row, dict):
                continue
            for name in fields:
                if name in row:
                    row[name] = text
    kept = []
    for item in (brief or {}).get("read_parameters") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") in fields:
            kept.append({"name": item.get("name"), "value": text})
            continue
        kept.append(item)
    if isinstance(brief, dict):
        brief["read_parameters"] = kept
    return brief


def date_sql_error(sql, resolved_dates):
    """The query may use the desk's MM/DD/YYYY inside to_date, and no other date source."""
    dates = []
    found = resolved_dates if isinstance(resolved_dates, dict) else {}
    for item in found.get("resolutions") or []:
        if isinstance(item, dict) and not item.get("unresolved") and str(item.get("resolved_date") or ""):
            dates.append(str(item.get("resolved_date") or ""))
    if not dates:
        return ""
    text = str(sql or "")
    folded = text.upper()
    missing = [day for day in dates if day not in text]
    form = "TO_DATE" not in folded
    blocked = [name for name in ("CURRENT_DATE", "DATE_TRUNC", "EXTRACT", "INTERVAL") if name in folded]
    if not missing and not form and not blocked:
        return ""
    return "Use to_date(the_date, 'MM/DD/YYYY') for only these dates: " + ", ".join(dates)
