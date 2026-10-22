"""
booking_system/date_utils.py

Canonical date parsing and normalization utilities for LEON PMS.
This module provides the single source of truth for all date parsing logic across the system.
All date-dependent functions should use normalize_date() to ensure consistent behavior.

Expanded logic to handle:
- 'next Saturday', 'in 3 days', 'end of month', etc.
- If a range of dates or ambiguous input is detected, returns None.
"""
import re
from datetime import datetime, timedelta


def normalize_date(date_str, date_context):
    """
    Canonical regex-based date parser for all booking operations.
    Handles:
    - 'today', 'tomorrow', 'next week', 'next month'
    - 'next Saturday', 'in 3 days', 'end of month'
    - ISO dates (YYYY-MM-DD)
    - Dates like '7th July', 'July 7', '7/7', and fills in the current year if missing
    - Returns date in YYYY-MM-DD format, or None if ambiguous or a range is detected
    """
    date_str = date_str.strip().lower()
    today = datetime.now()
    # Detect range of dates (e.g., '7-9 July', 'July 7-9', '7/7-9/7', 'next weekend')
    if re.search(r"\bto\b|\-|–|—|\bthrough\b|\buntil\b|\bbetween\b", date_str):
        return None
    if re.fullmatch(r"next weekend", date_str):
        return None
    # Handle keywords
    if re.fullmatch(r"today", date_str):
        return date_context['today']
    elif re.fullmatch(r"tomorrow", date_str):
        return date_context['tomorrow']
    elif re.fullmatch(r"next week", date_str):
        return date_context['next_week']
    elif re.fullmatch(r"next month", date_str):
        return date_context['next_month']
    # Handle 'in X days'
    m = re.fullmatch(r"in (\d{1,2}) days?", date_str)
    if m:
        days = int(m.group(1))
        target = today + timedelta(days=days)
        return target.strftime("%Y-%m-%d")
    # Handle 'end of month'
    if re.fullmatch(r"end of month", date_str):
        next_month = today.replace(day=28) + timedelta(days=4)  # this will never fail
        end_of_month = next_month - timedelta(days=next_month.day)
        return end_of_month.strftime("%Y-%m-%d")
    # Handle 'next <weekday>'
    m = re.fullmatch(r"next (monday|tuesday|wednesday|thursday|friday|saturday|sunday)", date_str)
    if m:
        weekday_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        target_weekday = weekday_map[m.group(1)]
        days_ahead = (target_weekday - today.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = today + timedelta(days=days_ahead)
        return target_date.strftime("%Y-%m-%d")
    # ISO date
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str
    # 7th July, July 7, 7/7, 07/07, 7-7, 07-07
    month_map = {m.lower(): i+1 for i, m in enumerate(['january','february','march','april','may','june','july','august','september','october','november','december'])}
    # 7th July or 7 July
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)", date_str)
    if m:
        day = int(m.group(1))
        month = month_map.get(m.group(2))
        if month:
            return f"{today.year}-{month:02d}-{day:02d}"
    # July 7
    m = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", date_str)
    if m:
        month = month_map.get(m.group(1))
        day = int(m.group(2))
        if month:
            return f"{today.year}-{month:02d}-{day:02d}"
    # 7/7 or 07/07 or 7-7 or 07-07
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", date_str)
    if m:
        month = int(m.group(2))
        day = int(m.group(1))
        return f"{today.year}-{month:02d}-{day:02d}"
    # Fallback: return None
    return None 