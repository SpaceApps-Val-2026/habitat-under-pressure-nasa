"""Errors as the API returns them: {"error": {"code": "...", "text": {"en": "...", "es": "..."}}}. Never a trace or a
disk path. Every error carries both languages: the Spanish text is given where the error is raised, or else it is
the general text of its code (the web reads `error.text` in the visitor's language)."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("hup_api")

# The Spanish text of a code, for errors raised with an English text only.
SPANISH = {
    "not_found": "No existe lo que se ha pedido.",
    "invalid_request": "La petición no es válida.",
    "invalid_plan": "El plan no es válido.",
    "internal": "Algo ha fallado en el servidor.",
    "too_large": "El cuerpo de la petición es demasiado grande.",
    "busy": "El servidor está ejecutando todos los análisis que puede. Vuelve a intentarlo en unos minutos.",
    "rate_limited": "Demasiadas peticiones desde esta dirección. Espera un minuto y vuelve a intentarlo.",
    "assistant_unavailable": "El asistente no está disponible: elige el diseño, el lugar y las variables en el "
                             "formulario.",
}
GENERAL_ES = "La petición no se ha podido atender."


class ApiError(Exception):
    def __init__(self, status: int, code: str, text: str, es: str | None = None):
        super().__init__(text)
        self.status, self.code, self.text, self.es = status, code, text, es


def body(code: str, text: str, es: str | None = None) -> dict:
    return {"error": {"code": code, "text": {"en": text, "es": es or SPANISH.get(code, GENERAL_ES)}}}


def not_found(what: str = "analysis") -> ApiError:
    names = {"analysis": "ese análisis", "file": "ese archivo", "variable": "esa variable", "plan": "ese plan",
             "route": "esa ruta"}
    return ApiError(404, "not_found", f"No such {what}.", f"No existe {names[what]}." if what in names else None)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(body(error.code, error.text, error.es), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = ", ".join(".".join(map(str, e.get("loc", ()))) for e in error.errors())
        return JSONResponse(body("invalid_request", f"Invalid request: {fields}.",
                                 f"Petición no válida: {fields}."), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.exception("unexpected error on %s", request.url.path)
        return JSONResponse(body("internal", "Something went wrong on the server."), status_code=500)
