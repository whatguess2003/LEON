"""The desk as the UI reaches it: sign in through /api/auth/signin (leon_jwt cookie), clear the chat, send turns to /api/chat/start."""

import json
import time

import httpx

from fixtures.sandbox import HOTELS, PASSWORD


class Desk:
    def __init__(self, base_url, timeout_s=600):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=httpx.Timeout(timeout_s, connect=10))
        self.me = {}

    def up(self):
        try:
            return self.http.get("/api/auth/me", timeout=10).status_code < 500
        except httpx.HTTPError:
            return False

    def sign_in(self, hotel_name="SANDBOX HOTEL"):
        spec = next(item for item in HOTELS if item["hotel_name"] == hotel_name)
        body = {"auth_type": "username", "auth_value": spec["manager"][1]["username"], "password": PASSWORD}
        signed = self.http.post("/api/auth/signin", json=body).json()
        if signed.get("success") and signed.get("hotels") is not None:
            chosen = next(item for item in signed["hotels"] if str(item.get("hotel_name") or "").upper() == hotel_name)
            signed = self.http.post("/api/auth/signin", json=dict(body, hotel_id=chosen["hotel_id"])).json()
        if not signed.get("success") or "leon_jwt" not in self.http.cookies:
            raise RuntimeError("sign-in failed: " + str(signed.get("message") or "no session cookie"))
        self.me = {key: signed.get(key) for key in ("user_id", "hotel_id", "hotel_name", "role", "manager_name")}
        return self.me

    def clear(self):
        return self.http.post("/api/chat/clear").json()

    def definitions(self):
        found = self.http.get("/api/definitions")
        return found.json() if found.status_code == 200 else {"error": found.status_code}

    def send(self, message):
        """One turn. Returns the reply with time to first byte and total wall time in ms."""
        started = time.perf_counter()
        first = None
        chunks = []
        with self.http.stream("POST", "/api/chat/start", json={"message": message}) as response:
            for chunk in response.iter_bytes():
                if first is None:
                    first = time.perf_counter()
                chunks.append(chunk)
            status = response.status_code
        ended = time.perf_counter()
        raw = b"".join(chunks).decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"final_message": raw[:2000]}
        return {
            "status": status,
            "reply": str(body.get("final_message") or ""),
            "refresh": bool(body.get("refresh")),
            "screen": (body.get("screen") or {}).get("title") if isinstance(body.get("screen"), dict) else bool(body.get("screen")),
            "ttfb_ms": int(((first or ended) - started) * 1000),
            "wall_ms": int((ended - started) * 1000),
        }
