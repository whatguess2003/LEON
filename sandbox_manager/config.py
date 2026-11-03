"""Process settings. The desk URL and the status port come from the environment."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

HOTEL_ID = "htl_fb48230a-7260-4b66-9c66-db90e96a6a81"
USER_ID = "usr_fa75aca6-1048-4c20-8ab0-afa91fdc255a"
HOTEL_NAME = "SANDBOX HOTEL"
TIMEZONE = "Asia/Kuala_Lumpur"
MODEL = "gpt-6.1-sol"
EFFORT = "high"
DAILY_CAP = 100
ASK_INTERVAL_SECONDS = 14 * 60
FLAG_SENTENCES = (
    "i doubt the answer is correct",
    "i think this is incorrect",
)
VERDICTS = ("match", "mismatch", "unsaid")


def main_url():
    return (os.getenv("SANDBOX_MAIN_URL") or "http://127.0.0.1:8002").rstrip("/")


def status_port():
    raw = os.getenv("SANDBOX_STATUS_PORT") or "8012"
    try:
        port = int(raw)
    except ValueError:
        port = 8012
    if port < 1 or port > 65535:
        return 8012
    return port


def status_host():
    return os.getenv("SANDBOX_STATUS_HOST") or "127.0.0.1"


def local_now():
    return datetime.now(ZoneInfo(TIMEZONE))


def local_day():
    return local_now().date().isoformat()


def hotel_clock():
    now = local_now()
    return {
        "timezone": TIMEZONE,
        "hotel_local_today": now.strftime("%m/%d/%Y"),
        "weekday": now.strftime("%A"),
    }


def public_text(value):
    """Drop a note that carries a connection string, a token, or a password."""
    text = str(value or "")
    lowered = text.casefold()
    if "api_key" in lowered:
        return "model client is not configured"
    for token in ("postgres://", "postgresql://", "password", "bearer ", "leon_jwt"):
        if token in lowered:
            return "redacted"
    return text[:500]
