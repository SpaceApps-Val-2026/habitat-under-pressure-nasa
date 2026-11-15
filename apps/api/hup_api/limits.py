"""Caps for a public server, as pure ASGI middlewares.

BodyLimit: a body above MAX_BODY gets 413 too_large. A declared Content-Length above the cap is refused before
anything is read. A body without a length (chunked) is counted as it arrives; once it passes the cap the app is told
the body is broken and whatever it would answer is replaced by the 413.

RateLimit: with HUP_RATE_LIMIT_PER_MINUTE set, each client gets that many POST /api/plans and as many
POST /api/analyses in any 60 seconds; more get 429 rate_limited. Kept in memory: one process, one instance.
The client is the connection's address, or with HUP_TRUST_FORWARDED_FOR=1 (behind Cloud Run's front end) the first
address of X-Forwarded-For. That first address is whatever the client sent, so this slows a careless or ordinary
visitor, not a determined one: the daily LLM budget is the cap that holds.

busy(): the 429 for HUP_MAX_RUNNING_ANALYSES (executor.runner.Busy).
"""
import json
import threading
import time
from collections import deque

from fastapi.responses import JSONResponse

from hup_api.errors import body
from hup_data import settings

MAX_BODY = 2 * 1024 * 1024  # a plan with a detailed zone outline stays well under this
LIMITED = (("POST", "/api/plans"), ("POST", "/api/analyses"))
WINDOW = 60.0
MAX_CLIENTS = 10_000  # beyond this many remembered clients the oldest windows are forgotten


def _bilingual(code: str, en: str, es: str) -> dict:
    return {"error": {"code": code, "text": {"en": en, "es": es}}}


RATE_LIMITED = _bilingual(
    "rate_limited", "Too many requests from this address. Wait a minute and try again.",
    "Demasiadas peticiones desde esta dirección. Espera un minuto y vuelve a intentarlo.")
BUSY = _bilingual(
    "busy", "The server is running as many analyses as it can. Try again in a few minutes.",
    "El servidor está ejecutando todos los análisis que puede. Vuelve a intentarlo en unos minutos.")


def busy() -> JSONResponse:
    return JSONResponse(BUSY, status_code=429, headers={"retry-after": "60"})


class _TooLarge(Exception):
    pass


async def _refuse(send) -> None:
    payload = json.dumps(body("too_large", "The request body is too large.")).encode("utf-8")
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


class BodyLimit:
    def __init__(self, app, limit: int = MAX_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.strip().isdigit() or int(length) > self.limit):
            await _refuse(send)
            return
        state = {"received": 0, "too_large": False, "refused": False}

        async def counted():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > self.limit:
                    state["too_large"] = True
                    raise _TooLarge()
            return message

        async def guarded(message):
            if state["too_large"]:
                if not state["refused"]:
                    state["refused"] = True
                    await _refuse(send)
                return
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except _TooLarge:
            if not state["refused"]:
                state["refused"] = True
                await _refuse(send)


class RateLimit:
    def __init__(self, app, clock=time.monotonic):
        self.app, self.clock = app, clock
        self.lock = threading.Lock()
        self.seen: dict[tuple[str, str], deque] = {}

    def client(self, scope, trust_forwarded: bool) -> str:
        if trust_forwarded:
            forwarded = dict(scope.get("headers") or []).get(b"x-forwarded-for", b"").decode("latin-1")
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        client = scope.get("client")
        return str(client[0]) if client else "unknown"

    def allowed(self, key: tuple[str, str], limit: int) -> bool:
        now = self.clock()
        with self.lock:
            stamps = self.seen.get(key)
            if stamps is None:
                if len(self.seen) >= MAX_CLIENTS:  # forget the windows that are over, or else the oldest
                    stale = [k for k, v in self.seen.items() if not v or v[-1] <= now - WINDOW]
                    for k in stale or list(self.seen)[: MAX_CLIENTS // 10]:
                        del self.seen[k]
                stamps = self.seen[key] = deque()
            while stamps and stamps[0] <= now - WINDOW:
                stamps.popleft()
            if len(stamps) >= limit:
                return False
            stamps.append(now)
            return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope.get("method"), scope.get("path", "").rstrip("/")) not in LIMITED:
            await self.app(scope, receive, send)
            return
        current = settings.load()
        limit = current.rate_limit_per_minute
        if limit is not None and not self.allowed((self.client(scope, current.trust_forwarded_for),
                                                   scope["path"].rstrip("/")), limit):
            await JSONResponse(RATE_LIMITED, status_code=429, headers={"retry-after": "60"})(scope, receive, send)
            return
        await self.app(scope, receive, send)