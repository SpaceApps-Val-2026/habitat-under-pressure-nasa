"""Write the files in `examples/` from one coherent analysis.

Usage: python docs/contratos/generate_examples.py   (then: python docs/contratos/validate.py)
The figures come from the real-data probe of 31 October; the rest is an example of shape.
Not product code: it lets every front work against the same files.
"""
import hashlib
import json
from pathlib import Path

E = Path(__file__).parent / "examples"
E.mkdir(exist_ok=True)
A = "sagunt-impact-example"
BBOX = [-0.35, 39.60, -0.15, 39.75]
PC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2"
SCENE = "LC08_L2SP_198033_20250830_02_T1"
PHJ = "chj-phj-2022-2027"


def sha(obj):
    """Fingerprint of a canonical object: sorted keys, no spaces, UTF-8."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


catalog = {
    "version": "0.2.0",
    "sources": [
        {"id": "landsat-c2-l2", "name": "Landsat 8/9 Collection 2 Level-2", "provider": "USGS and NASA",
         "product": "landsat-c2-l2", "version": "02", "resolution": "30 m (surface temperature: ~100 m)", "since": "2013",
         "access": {"mode": "api", "account": "none", "coverage": "on_demand", "url": PC},
         # license and citation by language (en required); the other sources show the plain-string shape
         "license": {"en": "Public domain (USGS). No use restrictions; credit is optional. Cite doi:10.5066/P9OGBGM6.",
                     "es": "Dominio público (USGS). Sin restricciones de uso; el crédito es opcional. Citar doi:10.5066/P9OGBGM6."},
         "citation": {"en": "Landsat Collection 2 Level-2 image courtesy of the U.S. Geological Survey. doi:10.5066/P9OGBGM6",
                      "es": "Landsat Collection 2 Level-2 image courtesy of the U.S. Geological Survey. doi:10.5066/P9OGBGM6"},
         "redistributable": True},
        {"id": "nasa-power", "name": "NASA POWER", "provider": "NASA LaRC", "product": "MERRA-2",
         "resolution": "~50 km", "since": "1981",
         "access": {"mode": "api", "account": "none", "coverage": "on_demand",
                    "url": "https://power.larc.nasa.gov/api/temporal/monthly/point"},
         "license": "Open, acknowledgement required", "citation": "NASA Langley Research Center POWER Project",
         "redistributable": True},
        {"id": "smap-l3", "name": "SMAP L3 soil moisture 9 km", "provider": "NASA NSIDC DAAC",
         "product": "SPL3SMP_E", "version": "006", "resolution": "9 km", "since": "2015",
         "access": {"mode": "package", "account": "earthdata", "coverage": "cases", "url": "https://nsidc.org/data/spl3smp_e"},
         "license": "Open, citation required", "citation": "SMAP Enhanced L3 Radiometer Global Daily 9 km (NSIDC DAAC)",
         "redistributable": True},
        {"id": PHJ, "name": "Plan Hidrológico de la Demarcación Hidrográfica del Júcar 2022-2027",
         "provider": "Confederación Hidrográfica del Júcar", "since": "2023",
         "access": {"mode": "manual", "account": "none", "coverage": "cases", "url": "https://www.chj.es/"},
         "license": "To be confirmed", "citation": "Confederación Hidrográfica del Júcar, Plan Hidrológico 2022-2027 (Real Decreto 35/2023)",
         "redistributable": False},
    ],
    "variables": [
        {"id": "ndvi", "name": {"en": "Vegetation greenness (NDVI)", "es": "Verde (NDVI)"}, "unit": "1",
         "layer": "green", "sources": ["landsat-c2-l2"],
         "answers": ["verde", "vegetación", "arbolado", "greenness", "vegetation", "tree cover"],
         "not": {"en": "Not a count of trees and not crop yield.", "es": "No es un recuento de árboles ni el rendimiento de un cultivo."},
         "support": {"resolution_m": 30, "since": 2013, "revisit_days": 8},
         "frame": "state", "designs": ["state", "trend", "impact", "scan"]},
        {"id": "lst", "name": {"en": "Land surface temperature", "es": "Temperatura de superficie"}, "unit": "°C",
         "layer": "heat", "sources": ["landsat-c2-l2"],
         "limit": {"en": "Surface temperature around 10:30, not air temperature.",
                   "es": "Temperatura de superficie hacia las 10:30, no del aire."},
         "answers": ["calor", "isla de calor", "heat", "heat island", "hot neighbourhoods"],
         "not": {"en": "Not air temperature and not the heat a person feels; night-time heat is not covered.",
                 "es": "No es la temperatura del aire ni el calor que siente una persona; no cubre el calor nocturno."},
         "support": {"resolution_m": 100, "since": 2013, "revisit_days": 8},
         "frame": "state", "designs": ["state", "trend", "impact", "scan"], "eurovoc": ["c_3f4ff841"]},
        {"id": "soil_moisture", "name": {"en": "Soil moisture", "es": "Humedad del suelo"}, "unit": "m³/m³",
         "layer": "water", "sources": ["smap-l3"],
         "answers": ["sequía", "suelo seco", "drought", "dry soil"],
         "not": {"en": "Not groundwater and not reservoir storage; one cell is larger than a municipality.",
                 "es": "No es agua subterránea ni agua embalsada; una celda es mayor que un municipio."},
         "support": {"resolution_m": 9000, "since": 2015, "revisit_days": 3},
         "frame": "state", "designs": ["state", "trend"], "eurovoc": ["3988"]},
        {"id": "water_demand", "name": {"en": "Water demand", "es": "Demanda de agua"}, "unit": "hm³/year",
         "layer": "water", "sources": [PHJ],
         "answers": ["consumo de agua", "demanda de agua", "water use", "water demand"],
         "not": {"en": "A planning figure from a document, not a measurement.",
                 "es": "Una cifra de planificación tomada de un documento, no una medición."},
         "frame": "pressure", "designs": ["state", "scenario"], "eurovoc": ["141", "3144"]},
        # A model fed with observations is not a measurement: the card says so and every reading copies it.
        {"id": "air_temperature", "name": {"en": "Air temperature at 2 m", "es": "Temperatura del aire a 2 m"},
         "unit": "°C", "layer": "climate", "sources": ["nasa-power"], "data_class": "reanalysis",
         "limit": {"en": "Reanalysis (MERRA-2) on a cell of about 50 km; it is not a thermometer reading.",
                   "es": "Reanálisis (MERRA-2) en una celda de unos 50 km; no es la lectura de un termómetro."},
         "answers": ["temperatura", "calentamiento", "temperature", "warming"],
         "not": {"en": "Not a station measurement; local extremes are smoothed out.",
                 "es": "No es una medida de estación; los extremos locales se suavizan."},
         "support": {"resolution_m": 50000, "since": 1981, "revisit_days": 1},
         "frame": "state", "designs": ["state", "trend", "scan"], "eurovoc": ["5482"]},
    ],
    "operations": [
        {"id": "read_series", "version": "1.0", "type": "ingest", "visual_type": "satellite",
         "phrase": {"en": "Reading {n} scenes from {source}", "es": "Leyendo {n} escenas de {source}"},
         "params": {"type": "object", "required": ["variable"],
                    "properties": {"variable": {"type": "string"}, "max_cloud_pct": {"type": "number"}}},
         "output": {"type": "series", "data_class": "inherit"}, "evidence_level": "E0"},
        {"id": "baci_contrast", "version": "1.0", "type": "compute", "visual_type": "compute",
         "phrase": {"en": "Comparing the site with its surroundings, before and after",
                    "es": "Comparando la zona con su entorno, antes y después"},
         "params": {"type": "object", "required": ["variable", "before", "after", "reference_km"],
                    "properties": {"variable": {"type": "string"}, "before": {"type": "array"},
                                   "after": {"type": "array"}, "reference_km": {"type": "number"}}},
         "output": {"type": "value", "data_class": "inherit"},
         "limits": [{"en": "Coincides in time and place; does not prove cause.",
                     "es": "Coincide en tiempo y lugar; no prueba la causa."}],
         "evidence_level": "E2", "diagnostics": ["n_dates", "interval_crosses_zero"]},
        {"id": "write_findings", "version": "1.0", "type": "write", "visual_type": "document",
         "phrase": {"en": "Writing the findings", "es": "Redactando los hallazgos"},
         "params": {"type": "object"}, "output": {"type": "document", "data_class": "inherit"}},
        {"id": "review_findings", "version": "1.0", "type": "review", "visual_type": "document",
         "phrase": {"en": "Checking every figure against its result", "es": "Comprobando cada cifra contra su resultado"},
         "params": {"type": "object"}, "output": {"type": "table", "data_class": "inherit"}},
    ],
    "chains": [
        {"id": "greenness_heat", "name": {"en": "Less vegetation, hotter surface", "es": "Menos verde, superficie más caliente"},
         "links": [{"from": "ndvi", "to": "lst", "sign": "-", "lag_years": [0, 0], "ref": None}]},
    ],
    "norms": [
        {"id": "es-rd-35-2023-jucar", "title": "Plan Hidrológico de la Demarcación Hidrográfica del Júcar 2022-2027",
         "scope": {"type": "basin", "id": "jucar"}, "applies_to": ["water_demand"],
         "clause": {"document": PHJ, "quote": None},
         "threshold": {"kind": "limit", "value": None, "unit": "hm³/year"}, "reviewed_by": None},
    ],
    "archetypes": [
        {"id": "household-water", "name": {"en": "Household water use", "es": "Consumo doméstico de agua"},
         "resource": "water", "unit": "L/day", "per": "inhabitant", "low": None, "central": None, "high": None, "ref": None},
    ],
    "blocks": [
        {"id": "KeyFigure", "needs": {"result_types": ["value"], "fields": ["interval"]},
         "styles": ["journalistic", "institutional", "scientific"]},
        {"id": "TrendChart", "needs": {"result_types": ["series"]}, "styles": ["journalistic", "institutional", "scientific"]},
        {"id": "EvidenceBadge", "needs": {"fields": ["evidence_level"]}, "styles": ["journalistic", "institutional", "scientific"]},
        {"id": "NormCard", "needs": {"result_types": ["value"]}, "styles": ["institutional", "scientific"]},
        {"id": "MethodBox", "needs": {}, "styles": ["institutional", "scientific"]},
        {"id": "LimitsBox", "needs": {}, "styles": ["journalistic", "institutional", "scientific"]},
        {"id": "SourcesTable", "needs": {}, "styles": ["journalistic", "institutional", "scientific"]},
    ],
}

baci = {"before": [2013, 2021], "after": [2023, 2025], "reference_km": 3}
plan = {
    "contract_version": "1.0",
    "id": "plan-" + A,
    "request": {"text": "¿Qué ha cambiado alrededor de la gigafactoría de Sagunt?", "language": "es",
                "level": "media", "origin": "assistant"},
    "answer": {"level": "with_limits", "limits": [
        {"en": "Soil moisture is not available for this place: its source needs a downloaded package.",
         "es": "La humedad del suelo no está disponible para este lugar: su fuente necesita un paquete descargado."}]},
    "design": "impact",
    "zone": {"id": "parc-sagunt-ii", "name": "Parc Sagunt II", "bbox": BBOX, "source": "OpenStreetMap, provisional"},
    "period": {"start": "2013-06-01", "end": "2025-09-30", "months": [6, 7, 8, 9]},
    "variables": ["ndvi", "lst", "soil_moisture"],
    "comparison": {"type": "spatial_reference", "reference_km": 3},
    "catalog_version": "0.2.0",
    "steps": [
        {"id": "s1", "type": "ingest", "operation": "read_series", "version": "1.0",
         "params": {"variable": "ndvi", "max_cloud_pct": 20}, "depends_on": [], "sources": ["landsat-c2-l2"],
         "data": {"origin": "remote", "estimated_seconds": 90, "estimated_mb": 40}},
        {"id": "s2", "type": "ingest", "operation": "read_series", "version": "1.0",
         "params": {"variable": "lst", "max_cloud_pct": 20}, "depends_on": [], "sources": ["landsat-c2-l2"],
         "data": {"origin": "cache"}},
        {"id": "s3", "type": "ingest", "operation": "read_series", "version": "1.0",
         "params": {"variable": "soil_moisture"}, "depends_on": [], "sources": ["smap-l3"],
         "data": {"origin": "package"}},
        {"id": "s4", "type": "compute", "operation": "baci_contrast", "version": "1.0",
         "params": dict(variable="ndvi", **baci), "depends_on": ["s1"]},
        {"id": "s5", "type": "compute", "operation": "baci_contrast", "version": "1.0",
         "params": dict(variable="lst", **baci), "depends_on": ["s2"]},
        {"id": "s6", "type": "write", "operation": "write_findings", "version": "1.0",
         "params": {}, "depends_on": ["s4", "s5"]},
        {"id": "s7", "type": "review", "operation": "review_findings", "version": "1.0",
         "params": {}, "depends_on": ["s6"]},
    ],
}

landsat = {"source": "landsat-c2-l2", "product": "landsat-c2-l2", "version": "02", "n_items": 151,
           "items": [SCENE], "url": PC, "license": "Public domain"}
after = {"start": "2023-06-01", "end": "2025-09-30", "months": [6, 7, 8, 9]}
no_cause = {"en": "Coincides in time and place with the works; does not prove cause.",
            "es": "Coincide en tiempo y lugar con la obra; no prueba la causa."}
interval = "bootstrap over clear dates"


def step_fingerprint(step_id):
    """Step cache key: operation, version, params, zone, and what the step reads.

    What it reads: the version of each of its sources, and the fingerprint of each step it depends on.
    """
    step = next(s for s in plan["steps"] if s["id"] == step_id)
    versions = {s["id"]: s["version"] for s in catalog["sources"] if "version" in s}
    inputs = {source: versions[source] for source in step.get("sources", []) if source in versions}
    inputs.update({dep: step_fingerprint(dep) for dep in step["depends_on"]})
    return sha({"operation": step["operation"], "version": step["version"], "params": step["params"],
                "zone": plan["zone"]["id"], "inputs": inputs, "period": plan["period"]})


results = [
    {"id": "r-s4-change", "analysis": A, "step": "s4", "type": "value", "variable": "ndvi", "zone": "parc-sagunt-ii",
     "period": after, "data_class": "measured", "unit": "1", "value": -0.32,
     "interval": {"level": 0.95, "low": -0.33, "high": -0.30, "method": interval},
     "coverage": {"n_dates": 151}, "sources": [landsat],
     "method": {"operation": "baci_contrast", "version": "1.0", "params": baci}, "limits": [no_cause],
     "evidence_level": "E2", "support": {"resolution_m": 30},
     "diagnostics": {"n_dates": 151, "interval_crosses_zero": False},
     "fingerprint": step_fingerprint("s4")},
    {"id": "r-s5-change", "analysis": A, "step": "s5", "type": "value", "variable": "lst", "zone": "parc-sagunt-ii",
     "period": after, "data_class": "measured", "unit": "°C", "value": 1.1,
     "interval": {"level": 0.95, "low": 0.8, "high": 1.4, "method": interval},
     "coverage": {"n_dates": 151}, "sources": [landsat],
     "method": {"operation": "baci_contrast", "version": "1.0", "params": baci},
     "limits": [no_cause, {"en": "Surface temperature, not air temperature.",
                           "es": "Temperatura de superficie, no del aire."}],
     "evidence_level": "E2", "support": {"resolution_m": 100},
     "diagnostics": {"n_dates": 151, "interval_crosses_zero": False},
     "fingerprint": step_fingerprint("s5")},
    # The missing data: it carries a reason and a source, and no value.
    {"id": "r-s3-series", "analysis": A, "step": "s3", "type": "series", "variable": "soil_moisture",
     "zone": "parc-sagunt-ii", "data_class": "measured",
     "absence": {"reason": "needs_package",
                 "detail": {"en": "SMAP has not been downloaded for this area.",
                            "es": "SMAP no está descargado para esta zona."}},
     "sources": [{"source": "smap-l3", "product": "SPL3SMP_E", "url": "https://nsidc.org/data/spl3smp_e"}],
     "method": {"operation": "read_series", "version": "1.0"}},
]

approved = {"status": "approved", "by": "reviewer agent"}
# Canonical plan: what was asked for, without the wording, the ids or the estimates (see the README).
canonical_plan = {k: plan[k] for k in ("design", "zone", "period", "variables", "comparison", "catalog_version")}
canonical_plan["zone"] = plan["zone"]["id"]
canonical_plan["steps"] = [{k: s[k] for k in ("operation", "version", "params", "depends_on")} for s in plan["steps"]]
plan_fingerprint = sha(canonical_plan)
data_versions = {"landsat-c2-l2": "02"}
analysis = {
    "contract_version": "1.0",
    "id": A,
    "title": {"en": "What changed around the Sagunt gigafactory",
              "es": "Qué ha cambiado alrededor de la gigafactoría de Sagunt"},
    "author": "example",
    "created": "2026-11-14T10:00:00Z",
    "status": "finished",
    "plan": plan,
    "plan_fingerprint": plan_fingerprint,
    "featured": True,
    "fingerprint": {"sha256": sha({"plan": plan_fingerprint, "data": data_versions}),
                    "catalog_version": "0.2.0", "data": data_versions},
    "results": [r["id"] for r in results],
    "findings": [
        {"id": "f1", "order": 1, "headline": True,
         "text": {"en": "Since 2023 the site runs {r-s5-change.value} °C hotter than its surroundings, compared with 2013-2021.",
                  "es": "Desde 2023 la parcela está {r-s5-change.value} °C más caliente que su entorno, respecto a 2013-2021."},
         "results": ["r-s5-change"], "data_class": "measured", "evidence_level": "E2", "review": approved},
        {"id": "f2", "order": 2,
         "text": {"en": "Its summer greenness fell by {r-s4-change.value} NDVI relative to the surroundings.",
                  "es": "Su verde de verano cayó {r-s4-change.value} de NDVI respecto al entorno."},
         "results": ["r-s4-change"], "data_class": "measured", "evidence_level": "E2", "review": approved},
    ],
    "alerts": [{"rule": "change_outside_band", "severity": "attention", "result": "r-s5-change",
                "text": {"en": "The change is outside the 2013-2021 band.",
                         "es": "El cambio queda fuera de la banda de 2013-2021."}}],
    "story": {
        "journalistic": [
            {"block": "KeyFigure", "results": ["r-s5-change"], "finding": "f1"},
            {"block": "EvidenceBadge", "results": ["r-s5-change"]},
            {"block": "KeyFigure", "results": ["r-s4-change"], "finding": "f2"},
            {"block": "LimitsBox", "results": ["r-s3-series"],
             "text": {"en": "What we could not measure here.", "es": "Lo que no hemos podido medir aquí."}},
            {"block": "SourcesTable"},
        ],
        "institutional": [
            {"block": "KeyFigure", "results": ["r-s5-change"], "finding": "f1"},
            {"block": "KeyFigure", "results": ["r-s4-change"], "finding": "f2"},
            {"block": "EvidenceBadge", "results": ["r-s5-change", "r-s4-change"]},
            {"block": "MethodBox"}, {"block": "LimitsBox", "results": ["r-s3-series"]}, {"block": "SourcesTable"},
        ],
        "scientific": [
            {"block": "MethodBox"},
            {"block": "KeyFigure", "results": ["r-s5-change"], "finding": "f1"},
            {"block": "KeyFigure", "results": ["r-s4-change"], "finding": "f2"},
            {"block": "EvidenceBadge", "results": ["r-s5-change", "r-s4-change"]},
            {"block": "LimitsBox", "results": ["r-s3-series"]}, {"block": "SourcesTable"},
        ],
    },
    "reports": [{"format": "html", "style": "journalistic", "language": "en", "path": f"analyses/{A}/report.journalistic.en.html"},
                {"format": "pdf", "style": "institutional", "language": "es", "path": f"analyses/{A}/report.institutional.es.pdf"}],
}

events = []


def event(kind, data, step=None, second=0):
    e = {"analysis": A, "seq": len(events) + 1, "type": kind, "ts": f"2026-11-14T10:00:{second:02d}Z", "data": data}
    if step:
        e["step"] = step
    events.append(e)


def start(step, operation, second, **extra):
    op = next(o for o in catalog["operations"] if o["id"] == operation)
    event("step_started", dict(operation=operation, visual_type=op["visual_type"], phrase=op["phrase"], **extra), step, second)


# s1: the data was not there and is downloaded now (on-demand ingest, visible to the person).
start("s1", "read_series", 0, bbox=BBOX)
event("progress", {"fraction": 0.5, "item": "scene 76 of 151", "phase": "download"}, "s1", 20)
event("data_read", {"source": "landsat-c2-l2", "origin": "remote", "n_items": 151, "mb": 38.5,
                    "period": {"start": "2013-06-01", "end": "2025-09-30"}}, "s1", 41)
event("step_finished", {"seconds": 41.0, "results": []}, "s1", 41)
# s2: already cached.
start("s2", "read_series", 41)
event("data_read", {"source": "landsat-c2-l2", "origin": "cache", "n_items": 151}, "s2", 42)
event("step_finished", {"seconds": 0.6, "results": []}, "s2", 42)
# s3: the source needs a package we do not have: a warning and a result with an absence.
start("s3", "read_series", 42)
event("warning", {"code": "needs_package",
                  "text": {"en": "SMAP has not been downloaded for this area; soil moisture is left out.",
                           "es": "SMAP no está descargado para esta zona; la humedad del suelo queda fuera."}}, "s3", 42)
event("step_finished", {"seconds": 0.1, "results": ["r-s3-series"]}, "s3", 42)
for step, result, second in (("s4", "r-s4-change", 44), ("s5", "r-s5-change", 46)):
    start(step, "baci_contrast", second - 2)
    event("partial_result", {"result": result}, step, second)
    event("step_finished", {"seconds": 2.0, "results": [result]}, step, second)
event("alert", {"rule": "change_outside_band", "severity": "attention", "result": "r-s5-change"}, "s5", 46)
start("s6", "write_findings", 46)
event("step_finished", {"seconds": 6.0, "results": []}, "s6", 52)
start("s7", "review_findings", 52)
event("step_finished", {"seconds": 3.0, "results": []}, "s7", 55)
event("analysis_finished", {"status": "finished"}, None, 55)

GSW = "https://storage.googleapis.com/global-surface-water/downloads2021/occurrence/occurrence_10W_40Nv1_4_2021.tif"
manifest = [
    {"source": "landsat-c2-l2", "product": "landsat-c2-l2", "version": "02", "item": SCENE,
     "scope": "parc-sagunt-ii", "bbox": BBOX, "period": {"start": "2025-08-30", "end": "2025-08-30"},
     "path": f"cache/landsat-c2-l2/parc-sagunt-ii/{SCENE}_ndvi.tif", "mb": 0.26,
     "sha256": hashlib.sha256(b"example-1").hexdigest(), "format": "cog",
     "ingest": {"date": "2026-11-14T10:00:20Z", "origin": "on_demand", "url": f"{PC}/items/{SCENE}"},
     "quality": {"cloud_pct": 3.1, "valid_pct": 98.0}, "license": "Public domain",
     "tier": "ready", "variables": ["ndvi"]},
    {"source": "jrc-gsw", "version": "1.4", "item": "occurrence_10W_40Nv1_4_2021", "scope": "global",
     "path": "packages/jrc-gsw/occurrence_10W_40Nv1_4_2021.tif", "mb": 36.2,
     "sha256": hashlib.sha256(b"example-2").hexdigest(), "format": "cog",
     "ingest": {"date": "2026-11-12T18:00:00Z", "origin": "predownload", "url": GSW, "by": "example"},
     "license": "No restrictions; cite Pekel et al. (2016)", "tier": "raw"},
]


def write(name, obj):
    if name.endswith(".jsonl"):
        text = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in obj)
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    (E / name).write_text(text, encoding="utf-8", newline="\n")


write("catalog.json", catalog)
write("plan.json", plan)
write("results.json", results)
write("analysis.json", analysis)
write("events.jsonl", events)
write("manifest.jsonl", manifest)
print(f"Examples written to {E}")
