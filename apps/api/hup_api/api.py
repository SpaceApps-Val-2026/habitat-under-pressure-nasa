"""Routes under /api (docs/paquetes/interfaces.md, section 7)."""
import json
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Header, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from hup_api import catalog, executor, legislation, limits, requests_log
from hup_api.assistant import service, templates
from hup_api.errors import ApiError, not_found
from hup_api.library import folder, index, plan_cache
from hup_api.llm.adapter import LLMUnavailable
from hup_data import paths, places
from hup_data.fingerprint import plan_fingerprint

router = APIRouter(prefix="/api")

_COMMON = json.loads((paths.CONTRACTS_DIR / "common.schema.json").read_text(encoding="utf-8"))
_ANALYSIS = json.loads((paths.CONTRACTS_DIR / "analysis.schema.json").read_text(encoding="utf-8"))
DESIGNS = tuple(_COMMON["$defs"]["design"]["enum"])
STATUSES = tuple(_ANALYSIS["properties"]["status"]["enum"])


def _existing(analysis_id: str) -> str:
    if not folder.exists(analysis_id):
        raise not_found()
    return analysis_id


@router.get("/catalog")
def get_catalog() -> dict:
    return catalog.load()


def _plan_of(payload: dict) -> dict:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        raise ApiError(422, "invalid_request", "The body must be {\"plan\": <plan>}.",
                       "El cuerpo debe ser {\"plan\": <plan>}.")
    return plan


class PlaceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    language: str = Field("en", pattern=r"^[a-z]{2}$")
    near: list[float] | None = Field(None, min_length=2, max_length=2)


@router.post("/places/resolve")
def resolve_place(payload: PlaceRequest) -> dict:
    """Exactly what hup_data.places.resolve returns: found with its zone, ambiguous with candidates, or not_found.

    `near` is the visitor's own position, sent only if they agreed in the browser: it orders the candidates of an
    ambiguous place and is used for nothing else. It is not stored, logged or sent to any other service.
    """
    return places.nearest_first(places.resolve(payload.text, payload.language), _near(payload.near))


class PointRequest(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    radius_km: float = Field(2.0, gt=0, le=100)
    name: Annotated[str, Field(max_length=200)] | None = None


@router.post("/places/point")
def point_place(payload: PointRequest) -> dict:
    """The zone of a point a person chose on a map: a circle that says what it is. Send it back as `place`."""
    return {"status": "found", "zone": places.point_zone(payload.lon, payload.lat, payload.radius_km, payload.name)}


def _near(value: list[float] | None) -> tuple[float, float] | None:
    if value is None:
        return None
    lon, lat = value
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ApiError(422, "invalid_request", "Invalid request: near is [longitude, latitude].",
                       "Petición no válida: near es [longitud, latitud].")
    return (lon, lat)


class PlanRequest(BaseModel):
    """A request for a plan. With `design` it is the form: no LLM, the plan comes from a template."""
    text: str = Field("", max_length=2000)
    language: str = Field("en", pattern=r"^[a-z]{2}$")
    level: Literal["general", "media", "university"] | None = None
    design: Literal[templates.ALL_DESIGNS] | None = None
    place: Annotated[str, Field(max_length=200)] | dict | None = None
    variables: list[Annotated[str, Field(max_length=100)]] | None = Field(None, max_length=50)
    period: dict | None = None
    before: list[int | str] | None = Field(None, min_length=2, max_length=2)
    after: list[int | str] | None = Field(None, min_length=2, max_length=2)
    # whatif only: catalog archetype id(s) picked from the catalog list. `quantity` is optional and never asked: a
    # size the question states, in the archetype's `per` unit; without it, the catalog's typical size or per unit.
    archetypes: list[Annotated[str, Field(max_length=100)]] | None = Field(None, max_length=20)
    archetype: Annotated[str, Field(max_length=100)] | None = None
    quantity: float | None = None


@router.post("/plans")
def post_plan(payload: PlanRequest) -> dict:
    """A plan, or a question. The form (with `design`) never needs an LLM; free text goes to the assistant.

    Without an LLM, or when its answers fail the checks twice, free text gives 503 assistant_unavailable, which the
    web turns into the form.
    """
    started = time.monotonic()
    current = catalog.load()
    if payload.design is None:
        try:
            # A place sent back with the text (a chosen candidate or a point zone) is honoured, never asked again.
            return service.from_text(payload.text, payload.language, payload.level, current, place=payload.place)
        except LLMUnavailable:
            # AGENTS.md 6 and 7: with no model the question still gets an answer, decided from the catalogue.
            from .assistant import offline

            if isinstance(payload.place, dict):  # already checked as a zone by service.from_text
                zone = payload.place
                return offline.from_text(payload.text, payload.language, current,
                                         resolve=lambda *_, **__: {"status": "found", "zone": zone})
            return offline.from_text(payload.text, payload.language, current)
    request = {"text": payload.text, "language": payload.language, "origin": "form",
               **({"level": payload.level} if payload.level else {})}
    return service.plan_answer(design=payload.design, place=payload.place, variables=payload.variables,
                               period=payload.period, before=payload.before, after=payload.after, request=request,
                               catalog=current, started=started,
                               archetypes=payload.archetypes or ([payload.archetype] if payload.archetype else None),
                               quantity=payload.quantity)


@router.post("/plans/validate")
def validate_plan(payload: Annotated[dict, Body()]) -> dict:
    """A plan from the form or edited by hand: is it runnable, its normalised form, fingerprint and existing analysis.

    An invalid plan is still a 200 answer, with valid false and the reasons. The answer level comes back as sent:
    correcting it against coverage belongs to the assistant (B4).
    """
    started = time.monotonic()
    current = catalog.load()
    plan = plan_cache.normalize(_plan_of(payload), current)
    errors = executor.validate_plan(plan, current)
    plan_fp = None if errors else plan_fingerprint(plan)
    existing = plan_cache.existing(plan_fp) if plan_fp else None
    requests_log.record(kind="validate", **requests_log.plan_fields(plan), plan_fingerprint=plan_fp,
                        cached=existing is not None if plan_fp else None, analysis_id=existing,
                        phases={"validate": time.monotonic() - started}, llm_calls=[])
    return {"valid": not errors, "errors": errors, "plan": plan, "plan_fingerprint": plan_fp,
            "existing_analysis": existing}


@router.post("/plans/estimate")
def estimate_plan(payload: Annotated[dict, Body()]) -> dict:
    """How long the plan should take: per step and its critical path, from the measured read times (timing.py).

    No network and no data read. 422 for a body without a plan or a plan that does not validate, as POST /analyses.
    """
    from hup_api.executor import timing

    current = catalog.load()
    plan = plan_cache.normalize(_plan_of(payload), current)
    errors = executor.validate_plan(plan, current)
    if errors:
        raise ApiError(422, "invalid_plan", "; ".join(errors), "El plan no es válido: " + "; ".join(errors))
    return timing.estimate(plan, current)


@router.post("/analyses", status_code=202, response_model=None)
def post_analysis(payload: Annotated[dict, Body()]) -> dict | JSONResponse:
    """Validate the normalised plan and start it; the run goes on in the server after this answer.

    429 busy when HUP_MAX_RUNNING_ANALYSES analyses are running already."""
    started = time.monotonic()
    current = catalog.load()
    plan = plan_cache.normalize(_plan_of(payload), current)
    errors = executor.validate_plan(plan, current)
    plan_fp = None if errors else plan_fingerprint(plan)
    try:
        analysis_id = None if errors else executor.run(plan)
    except executor.runner.Busy:
        return limits.busy()
    requests_log.record(kind="run", **requests_log.plan_fields(plan), plan_fingerprint=plan_fp, cached=False,
                        analysis_id=analysis_id, phases={"start": time.monotonic() - started}, llm_calls=[])
    if errors:
        raise ApiError(422, "invalid_plan", "; ".join(errors), "El plan no es válido: " + "; ".join(errors))
    return {"analysis_id": analysis_id}


@router.get("/analyses")
def list_analyses(
    q: str | None = Query(None, max_length=500),
    design: str | None = None,
    variable: str | None = Query(None, max_length=200),
    featured: bool | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    history: bool = False,
) -> dict:
    """The library: featured first, then newest. Failed analyses only with status=failed.

    Re-runs of one question show once, the newest finished one, with `versions` (how many finished analyses the
    group has) when there are several; history=1 lists every analysis as before (library/index.py, group_key)."""
    if design is not None and design not in DESIGNS:
        raise ApiError(422, "invalid_request", f"Invalid request: design must be one of {', '.join(DESIGNS)}.",
                       f"Petición no válida: design debe ser uno de {', '.join(DESIGNS)}.")
    if status is not None and status not in STATUSES:
        raise ApiError(422, "invalid_request", f"Invalid request: status must be one of {', '.join(STATUSES)}.",
                       f"Petición no válida: status debe ser uno de {', '.join(STATUSES)}.")
    return {"items": index.query(q=q, design=design, variable=variable, featured=featured, status=status,
                                 limit=limit, history=history)}


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str, editorial_style: Literal["institutional", "scientific"] | None = None) -> dict:
    analysis = folder.read_analysis(_existing(analysis_id))
    if analysis is None:
        raise not_found()
    if editorial_style and analysis["status"] == "finished":
        from hup_api.writer import opening_for_style

        story = analysis.get("story") or {}
        editorial = story.get("editorial") or {}
        if editorial_style not in editorial:
            results = folder.read_results(analysis_id) or []
            if piece := opening_for_style(analysis, results, catalog.load(), editorial_style):
                analysis = {**analysis, "story": {**story, "editorial": {**editorial, editorial_style: piece}}}
    return analysis


@router.get("/analyses/{analysis_id}/versions")
def get_versions(analysis_id: str) -> list[dict]:
    """The other analyses of the same question, zone and design, newest first: [{id, created, status}]."""
    return index.versions(_existing(analysis_id))


@router.post("/analyses/{analysis_id}/cancel", status_code=202)
def cancel_analysis(analysis_id: str) -> JSONResponse:
    outcome = executor.runner.cancel(_existing(analysis_id))
    if outcome is None:
        raise not_found()
    accepted, status = outcome
    return JSONResponse({"analysis_id": analysis_id, "status": status}, status_code=202 if accepted else 200)


@router.get("/analyses/{analysis_id}/results")
def get_results(analysis_id: str) -> list[dict]:
    return folder.read_results(_existing(analysis_id)) or []


@router.get("/analyses/{analysis_id}/events")
async def get_events(
    analysis_id: str,
    replay: bool = False,
    speed: float = Query(1.0, gt=0, le=10000),
    last_event_id: str | None = Header(None),
) -> EventSourceResponse:
    """SSE: id is seq, event is the type, data is the whole event. Reconnecting with Last-Event-ID resumes after it."""
    _existing(analysis_id)
    after = int(last_event_id) if last_event_id and last_event_id.strip().isdigit() else 0
    events = folder.replay_events(analysis_id, after, speed) if replay else folder.follow_events(analysis_id, after)

    async def messages():
        async for event in events:
            yield {"id": str(event["seq"]), "event": event["type"], "data": json.dumps(event, ensure_ascii=False)}

    return EventSourceResponse(messages(), ping=15, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/analyses/{analysis_id}/files/{path:path}")
def get_file(analysis_id: str, path: str) -> FileResponse:
    """Layers and clips of an analysis, with range requests. Nothing outside its folder."""
    target = folder.file(_existing(analysis_id), path)
    if target is None:
        raise not_found("file")
    return FileResponse(target)


@router.get("/legislation")
def get_legislation(
    variable: str = Query(..., min_length=1, max_length=100),
    language: str = Query("en", pattern=r"^[a-z]{2}$"),
) -> dict:
    """EU legislation in force on the subject of a variable. A list for reference, never a compliance check."""
    built = catalog.load()
    found = next((v for v in built["variables"] if v["id"] == variable), None)
    if found is None:
        raise not_found("variable")
    return legislation.related(found, language, built.get("legislation"))
