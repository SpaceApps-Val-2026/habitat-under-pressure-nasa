"""FastAPI application: the API under /api, and the built web app for every other path."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from hup_api import __version__, api, errors, layers, limits, place_image
from hup_api.assistant import question_article
from hup_api.executor import recovery
from hup_api.library import index, plan_cache
from hup_api.limits import MAX_BODY
from hup_data import paths

WEB_DIST = Path(os.environ.get("HUP_WEB_DIST") or paths.REPO_ROOT / "apps" / "web" / "dist")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Close analyses a previous process left running, then rebuild the in-memory plan cache and library index
    from the analysis folders; neither is kept on disk."""
    # An analysis written a moment ago may belong to another live process on the same data folder: it is left
    # alone now and looked at again once it has been quiet for that long.
    quiet = float(os.environ.get("HUP_RECOVERY_QUIET_SECONDS", "300"))

    def recover():
        try:
            return recovery.recover_interrupted(quiet)
        except Exception:
            logging.getLogger("hup_api").exception("startup: interrupted analyses could not be recovered")
            return []

    async def recover_later():
        await asyncio.sleep(quiet + 5)
        if await asyncio.to_thread(recover):
            for part in (plan_cache, index):
                part.rebuild()

    recover()
    later = asyncio.create_task(recover_later()) if quiet > 0 else None
    for part in (plan_cache, index):
        try:
            part.rebuild()
        except Exception:
            logging.getLogger("hup_api").exception("startup: %s could not be rebuilt", part.__name__)
    yield
    if later is not None:
        later.cancel()


# The server shows what the engine logs: each model call with its tokens and cost, each failed step.
logging.basicConfig(level=os.environ.get("HUP_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

IMMUTABLE = "public, max-age=31536000, immutable"
# Never compressed: the event stream must flow event by event, and binary layers are already compressed (and are
# read by ranges, which a compressed body would break).
NOT_COMPRESSED = (*DEFAULT_EXCLUDED_CONTENT_TYPES, "text/event-stream", "image/tiff", "application/octet-stream")


class CacheHeaders:
    """Cache-Control for every answer that does not set its own (UX-15): the built files under /assets carry a
    content hash in their name, so they never change; everything else (index.html, the API) is revalidated."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        value = IMMUTABLE if scope["path"].startswith("/assets/") else "no-cache"

        async def sending(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers and message.get("status", 200) < 400:
                    headers["cache-control"] = value
            await send(message)

        await self.app(scope, receive, sending)


class Compression:
    """Gzip for text answers (JSON, HTML, JS, CSS). A request for a byte range goes through untouched, so 206
    answers keep their Content-Range over the bytes of the file."""

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024, exclude_content_types=NOT_COMPRESSED)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and any(name == b"range" for name, _ in scope.get("headers", ())):
            return await self.app(scope, receive, send)
        return await self.gzip(scope, receive, send)


app = FastAPI(title="Habitat Under Pressure", version=__version__, lifespan=lifespan)
errors.install(app)
app.add_middleware(limits.BodyLimit, limit=MAX_BODY)
app.add_middleware(limits.RateLimit)  # off unless HUP_RATE_LIMIT_PER_MINUTE is set
app.add_middleware(CacheHeaders)
app.add_middleware(Compression)
app.include_router(api.router)  # before the web catch-all, which must stay the last route
app.include_router(layers.router)  # A11: map layers of an analysis
app.include_router(place_image.router)  # satellite picture of the place of a report
app.include_router(question_article.router)  # editorial answer without a runnable analysis
app.include_router(question_article.api_router)  # the same article as data, for the web app


def catalog_version() -> str | None:
    file = paths.CATALOG_DIR / "version.txt"
    return file.read_text(encoding="utf-8").strip() if file.is_file() else None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "catalog_version": catalog_version(),
            "commit": os.environ.get("HUP_COMMIT") or None}  # the commit the image was built from


@app.get("/{path:path}", include_in_schema=False)
def web(path: str) -> FileResponse:
    """Serve a built file when it exists; otherwise the app shell, so client-side routes survive a reload."""
    if path == "api" or path.startswith("api/"):
        raise errors.not_found("route")
    root = WEB_DIST.resolve()
    file = (root / path).resolve()
    if path and file.is_file() and file.is_relative_to(root):
        return FileResponse(file)
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="The web app is not built: npm --prefix apps/web run build")
    return FileResponse(index)
