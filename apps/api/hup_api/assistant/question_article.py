"""The report for a question that cannot yet produce an analysis."""
import json
import re
from html import escape
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from hup_api.errors import not_found
from hup_data import paths, store

router = APIRouter(prefix="/q", include_in_schema=False)


def save(request: dict, *, headline: dict, lead: dict, cannot_say: dict,
         zone: str | None = None, sources=(), candidates=(), clarify: bool = False) -> str:
    article_id = uuid4().hex
    path = paths.data_dir() / "question_articles" / f"{article_id}.json"
    data = {
        "question": request["text"], "language": request["language"], "headline": headline,
        "lead": lead, "cannot_say": cannot_say, "zone": zone, "sources": list(sources),
        "candidates": list(candidates), "clarify": clarify,
    }
    store.write_atomic(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return f"/q/{article_id}"


def out_of_scope(request: dict, *, zone: str | None = None, conflict: bool = False) -> dict:
    if conflict:
        headline = {"en": "The war's local effects remain unverified",
                    "es": "Los efectos locales de la guerra quedan sin verificar"}
        lead = {
            "en": "Delfos has no verified source here linking a local environmental change to the conflict.",
            "es": "Delfos carece aquí de una fuente verificada que vincule un cambio ambiental local al conflicto.",
        }
        cannot_say = {
            "en": "A comparable local indicator and a documented event period could show a change. "
                  "Attributing it to the war requires further evidence.",
            "es": "Un indicador local comparable y un periodo documentado permitirían detectar un cambio. "
                  "Atribuirlo a la guerra requiere más evidencia.",
        }
    else:
        headline = {"en": "Delfos lacks evidence for this question",
                    "es": "Falta evidencia para responder a esta pregunta"}
        lead = {"en": "The available sources and methods do not support a finding for the question as asked.",
                "es": "Las fuentes y los métodos disponibles no sostienen un hallazgo para la pregunta planteada."}
        cannot_say = {
            "en": "An answer would require a source and a method that measure the requested effect or quantity.",
            "es": "Hace falta una fuente y un método que midan el efecto o la magnitud que se pregunta.",
        }
    return {"kind": "article", "article_url": save(request, headline=headline, lead=lead,
                                                    cannot_say=cannot_say, zone=zone)}


def clarification(request: dict, answer: dict) -> dict:
    return {**answer, "article_url": save(
        request, headline=answer["question"],
        lead={"en": "The place determines which observations apply to your question.",
              "es": "El lugar determina qué observaciones sirven para responder a tu pregunta."},
        cannot_say={"en": "Pick a place below or enter a more specific name to continue.",
                    "es": "Elige un lugar de la lista o escribe un nombre más concreto para continuar."},
        candidates=answer.get("candidates", []), clarify=True,
    )}


def unavailable_forecast(request: dict, place: str | None) -> dict:
    url = save(
        request, zone=place,
        headline={"en": "These sources cannot date the next event",
                  "es": "Estas fuentes no permiten fechar el próximo episodio"},
        lead={"en": "Delfos has no forecast feed for the type of event you asked about.",
              "es": "Delfos carece de un servicio de pronóstico para el episodio que preguntas."},
        cannot_say={
            "en": "Historical observations and climate scenarios cover different time scales. "
                  "They cannot establish whether that episode will occur on a given day.",
            "es": "Las observaciones históricas y los escenarios climáticos cubren escalas distintas. "
                  "No pueden establecer si ese episodio ocurrirá un día concreto.",
        },
    )
    return {"kind": "article", "article_url": url}


def unavailable_resource_estimate(request: dict, place: str | None) -> dict:
    url = save(
        request, zone=place,
        headline={"en": "The requested resource use remains unverified",
                  "es": "El consumo solicitado queda sin verificar"},
        lead={
            "en": "No catalogued archetype matches this question, so Delfos cannot estimate that use.",
            "es": "Ningún arquetipo del catálogo encaja con la pregunta, así que Delfos no puede estimar ese consumo.",
        },
        cannot_say={
            "en": "The estimate needs a published coefficient for the resource and activity concerned.",
            "es": "La estimación requiere un coeficiente publicado para el recurso y la actividad solicitados.",
        },
    )
    return {"kind": "article", "article_url": url}


def _read(article_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", article_id):
        raise not_found("article")
    path = paths.data_dir() / "question_articles" / f"{article_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise not_found("article") from None


def _places(data: dict, lang: str) -> str:
    candidates = "".join(
        f'<button name="candidate" value="{i}">{escape(zone["name"])}</button>'
        for i, zone in enumerate(data["candidates"])
    )
    label = "Otro lugar" if lang == "es" else "Another place"
    action = "Continuar" if lang == "es" else "Continue"
    return f"""<form method="post">{candidates}</form>
    <form method="post"><label for="place">{label}</label>
    <input id="place" name="place" maxlength="200" required><button>{action}</button></form>"""


api_router = APIRouter(prefix="/api/questions", include_in_schema=False)


@api_router.get("/{article_id}")
def article(article_id: str) -> dict:
    """The stored article as data, for the web app: it draws it with its own header, footer and styles."""
    return {"id": article_id, **_read(article_id)}


@router.get("/{article_id}", response_class=HTMLResponse)
def page(article_id: str, request: Request):
    data = _read(article_id)
    # A browser gets the web app, which draws the article in the house style from /api/questions/{id}.
    # Anything else (and a server without the built web) gets this plain page, which works on its own.
    if "text/html" in request.headers.get("accept", ""):
        from hup_api import main  # late: main imports this module

        index = main.WEB_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
    lang = data["language"] if data["language"] in {"en", "es"} else "en"
    title = escape(data["headline"][lang])
    lead = escape(data["lead"][lang])
    limit = escape(data["cannot_say"][lang])
    query = escape(data["question"])
    place = f'<p class="place">{escape(data["zone"])}</p>' if data["zone"] else ""
    limit_title, sources_title, home = (
        ("Qué queda por saber", "Fuentes", "Hacer otra pregunta") if lang == "es"
        else ("What remains unknown", "Sources", "Ask another question")
    )
    sources = "".join(
        f'<li><a href="{escape(source["url"], quote=True)}">{escape(source["name"])}</a></li>'
        for source in data["sources"] if source["url"].startswith("https://")
    )
    source_section = f"<section><h2>{sources_title}</h2><ul>{sources}</ul></section>" if sources else ""
    location_form = _places(data, lang) if data.get("clarify") else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title} · Delfos</title>
<style>
  :root {{ color-scheme: dark; font-family: system-ui,sans-serif; background:#0e1e24; color:#dbe9e8; }}
  body {{ margin:0; }} main {{ box-sizing:border-box; max-width:49rem; margin:auto; padding:3rem 1.5rem; }}
  a {{ color:#74caca; }} h1 {{ font-size:clamp(2.35rem,7vw,4.4rem); line-height:1.03; margin:.6em 0 .35em; }}
  h2 {{ font-size:1.15rem; }} p, li {{ line-height:1.55; }} .question,.place {{ color:#acc4c7; }}
  .lead {{ font-size:1.32rem; }} section {{ border-top:1px solid #365057; margin-top:2em; }}
  form {{ display:flex; gap:.65rem; flex-wrap:wrap; align-items:center; margin:1.4rem 0; }}
  button,input {{ box-sizing:border-box; font:inherit; padding:.65rem .85rem; border:1px solid #59757b;
                 border-radius:.4rem; color:inherit; background:#172d34; }} button {{ cursor:pointer; }}
</style></head>
<body><main><header><a href="/">Delfos</a></header><article>
<p class="question">{query}</p><h1>{title}</h1><p class="lead">{lead}</p>{place}
<section><h2>{limit_title}</h2><p>{limit}</p>{location_form}</section>{source_section}
</article><p><a href="/">{home}</a></p></main></body></html>""",
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


def _continue(article_id: str, form: dict) -> RedirectResponse:
    from hup_api.api import PlanRequest, post_analysis, post_plan

    data = _read(article_id)
    if not data.get("clarify"):
        raise not_found("article")
    candidate = (form.get("candidate") or [""])[0]
    place = (form.get("place") or [""])[0].strip()[:200]
    if candidate.isascii() and candidate.isdecimal() and len(candidate) < 4:
        index = int(candidate)
        if index < len(data["candidates"]):
            place = data["candidates"][index]
    if not place:
        return RedirectResponse(f"/q/{article_id}", status_code=303)
    reply = post_plan(PlanRequest(text=data["question"], place=place, language=data["language"]))
    if url := reply.get("article_url"):
        return RedirectResponse(url, status_code=303)
    if analysis := reply.get("existing_analysis"):
        return RedirectResponse(f"/a/{analysis}", status_code=303)
    if reply.get("kind") == "plan":
        started = post_analysis({"plan": reply["plan"]})
        if isinstance(started, dict):
            return RedirectResponse(f"/run/{started['analysis_id']}", status_code=303)
        return started
    return RedirectResponse(f"/q/{article_id}", status_code=303)


@router.post("/{article_id}")
async def choose(article_id: str, request: Request):
    try:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"), max_num_fields=4)
    except ValueError:
        form = {}
    return await run_in_threadpool(_continue, article_id, form)
