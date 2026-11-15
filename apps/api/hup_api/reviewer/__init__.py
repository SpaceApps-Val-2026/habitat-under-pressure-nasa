"""Review statistical evidence, then check the sentences it supports."""

from copy import deepcopy

from hup_api.writer import finding_text, rare_event
from hup_ops._series import DATA_CLASSES, least_direct

from .statistical import RULES
from .text import validate_text

MESSAGES = {
    "causal_language": ("The sentence uses causal language.", "La frase usa lenguaje causal."),
    "legal_verdict": ("The sentence makes a legal determination.", "La frase emite un veredicto jurídico."),
    "literal_figure": ("A figure must come from a result.", "La cifra debe salir de un resultado."),
    "invalid_placeholder": ("The placeholder cannot be filled.", "El hueco no se puede rellenar."),
    "unsupported_verb": ("The verb exceeds the available evidence.", "El verbo supera la evidencia disponible."),
    "missing_evidence": (
        "The sentence lacks usable cited evidence.",
        "La frase carece de evidencia citada utilizable.",
    ),
    "data_class": ("The sentence overstates the data class.", "La frase presenta datos como más directos."),
    "evidence_reduced": ("The sentence was adjusted to available evidence.", "Se ajustó la frase a la evidencia."),
}


def _warning(code, en, es, *, result=None, finding=None):
    return {"code": code, "text": {"en": en, "es": es}, "result": result, "finding": finding}


def review(results: list[dict], findings: list[dict], catalog: dict) -> dict:
    results, findings = deepcopy(results), deepcopy(findings)
    warnings = []
    for result in results:
        for check, en, es in RULES:
            ceiling = check(result)
            if ceiling is not None:
                result["evidence_level"] = min(ceiling, result.get("evidence_level", "E0"))
                warnings.append(_warning(check.__name__, en, es, result=result["id"]))

    by_id = {result["id"]: result for result in results}
    labels = {v["id"]: v["name"] for v in catalog.get("variables", [])}
    kept = []
    for finding in findings:
        cited = [by_id[rid] for rid in finding["results"] if rid in by_id]
        code = None
        if (
            not cited
            or len(cited) != len(finding["results"])
            or any(("absence" in r and not rare_event(r)) or not r.get("sources") for r in cited)
        ):
            code = "missing_evidence"
        else:
            least = least_direct(r["data_class"] for r in cited)
            if DATA_CLASSES.index(finding["data_class"]) < DATA_CLASSES.index(least):
                code = "data_class"

        if code is None:
            hard_error = validate_text(finding, cited)
            if hard_error != "unsupported_verb":
                code = hard_error

        if code is None:
            original = finding.get("evidence_level", "E0")
            level = min(original, *(r.get("evidence_level", "E0") for r in cited))
            restricted = [r for r in cited if r.get("direction_assertable") is False]
            if level < original or restricted:
                warnings.append(_warning("evidence_reduced", *MESSAGES["evidence_reduced"], finding=finding["id"]))
                if len(cited) > 1:
                    code = "missing_evidence"
                else:
                    source = cited[0]
                    level = "E0" if restricted else level
                    template = finding_text(source, labels.get(source["variable"], {}), level)
                    if template is None:
                        code = "missing_evidence"
                    else:
                        finding.update(text=template, evidence_level=level)
            code = code or validate_text(finding, cited)

        if code is not None:
            warnings.append(_warning(code, *MESSAGES[code], finding=finding["id"]))
            continue
        else:
            finding["review"] = {"status": "approved", "by": "rules"}
        kept.append(finding)
    return {"results": results, "findings": kept, "warnings": warnings}
