"""Run a plan: every step as a task, independent steps at once, computation in threads, results in the store.

For each step, in this order: fingerprint; step_started; reuse the derived result if the store has it (data_read
with origin derived), otherwise call the operation, complete and validate its results and save them;
partial_result and step_finished. Then analysis_finished and analysis.json.

One process, no queues: each analysis runs in its own thread with its own event loop, so it goes on whether or
not anybody is listening to its stream and whatever happens to the request that started it.
"""
import asyncio
import copy
import importlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

from hup_api import catalog as catalog_module
from hup_api import contracts
from hup_api.executor import reads, timing
from hup_api.executor.validation import order, validate_plan
from hup_api.library import folder, index, plan_cache
from hup_api.writer import compose_fallback, place_label
from hup_data import cancellation, paths, settings, store
from hup_data.fingerprint import plan_fingerprint, sha, step_fingerprint, zone_key

log = logging.getLogger("hup_api.executor")
# From most to least direct. A result is as indirect as the least direct thing it comes from.
DATA_CLASSES = ["measured", "reanalysis", "extrapolated", "approximated", "illustrative"]
EVIDENCE = ["E0", "E1", "E2", "E3", "E4", "E5"]
# Write and review steps are not registry operations: they call these modules, imported when needed (B6 and B7).
WRITER, REVIEWER = "hup_api.writer", "hup_api.reviewer"
WRITING_SECONDS = 120.0  # a writer or reviewer that takes longer yields to the deterministic report
# What the reviewer may change in a result: it lowers the level and adds limits, never a figure.
REVIEWABLE = ("evidence_level", "limits")


class InvalidPlan(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def default_registry() -> dict:
    """hup_ops.registry.REGISTRY when it exists; an empty registry otherwise (every step then fails cleanly)."""
    try:
        from hup_ops.registry import REGISTRY
    except ImportError:
        return {}
    return REGISTRY


def _context_class():
    try:
        from hup_ops.registry import Context
    except ImportError:
        return SimpleNamespace
    return Context


def _readers():
    try:
        from hup_data import readers
    except ImportError:
        return None
    return readers


def _function(module: str, name: str):
    """A function of a module imported now. Raises ImportError or AttributeError when either is missing."""
    function = getattr(importlib.import_module(module), name)
    if not callable(function):
        raise AttributeError(f"{module}.{name} is not callable")
    return function


def plain(value):
    """A deep copy with numpy scalars as the Python value they hold (np.True_ → True, np.float64 → float).

    Only the representation changes, never the value: the result is then validated as it is. Anything else that
    is not JSON (an array, a frame) is left as it is, so validation still rejects it.
    """
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    if type(value).__module__ == "numpy" and hasattr(value, "item") and getattr(value, "ndim", None) == 0:
        return value.item()
    return copy.deepcopy(value)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def text(en: str) -> dict:
    return {"en": en}


@dataclass
class Outcome:
    """What a step leaves for the steps that depend on it."""
    step: str
    ok: bool
    fingerprint: str
    results: list[dict] = field(default_factory=list)
    variable: str | None = None
    sources: list[dict] = field(default_factory=list)
    cacheable: bool = True


class Run:
    def __init__(self, analysis_id: str, plan: dict, catalog: dict, registry: dict):
        self.id, self.plan, self.catalog, self.registry = analysis_id, plan, catalog, registry
        self.operations = {o["id"]: o for o in catalog["operations"]}
        self.sources = {s["id"]: s for s in catalog["sources"]}
        self.variables = {v["id"]: v for v in catalog["variables"]}
        self.plan_fp = plan_fingerprint(plan)
        self.seed = int(self.plan_fp[:8], 16)  # every step's randomness derives from the plan fingerprint
        self.zone = zone_key(plan["zone"])
        self.lock = threading.Lock()
        self.cancelled = cancellation.Signal()
        self.execution: tuple[asyncio.AbstractEventLoop, asyncio.Task] | None = None
        self.status = "running"
        self.seq = 0
        self.results: list[dict] = []
        self.failed = False
        self.created = now()
        self.findings: list[dict] | None = None  # None until a write step has written them
        self.story: dict | None = None
        self.editorial_notice: dict[str, str] | None = None

    # files and events ---------------------------------------------------------------------------------------

    def emit(self, kind: str, data: dict, step: str | None = None) -> None:
        with self.lock:
            if self.status == "running" and not self.cancelled.is_set():
                self._emit(kind, data, step)

    def _emit(self, kind: str, data: dict, step: str | None = None) -> None:
        self.seq += 1
        event = {"analysis": self.id, "seq": self.seq, "type": kind, "ts": now(), "data": data}
        if step:
            event["step"] = step
        folder.append_event(self.id, event)

    def keep(self, results: list[dict]) -> None:
        with self.lock:
            cancellation.checkpoint()
            self.results += results
            folder.write_results(self.id, self.results)

    def progress(self, step: str, fraction: float, item: str = "") -> None:
        data = {"fraction": min(1.0, max(0.0, float(fraction)))}
        if item:
            data["item"] = str(item)
        self.emit("progress", data, step)

    def title(self) -> dict:
        """Display the plan's place and variable names with the reading's temporal aggregation."""
        place = place_label(self.plan["zone"])
        if self.plan["design"] == "whatif":
            archetypes = [
                name for step in self.plan["steps"] if step["operation"] == "resource_estimate"
                and (name := (step["params"].get("archetype") or {}).get("name"))
            ]
            titles = {
                lang: f"{place}: {', '.join(dict.fromkeys(name.get(lang, name['en']) for name in archetypes))}"
                if archetypes else f"{place}: {label}"
                for lang, label in (("en", "Resource estimate"), ("es", "Estimación de recursos"))
            }
            request = self.plan["request"]
            if (question := request["text"].strip()) and request["language"] in titles:
                titles[request["language"]] = question
            return titles
        annual = {step["params"]["variable"] for step in self.plan["steps"]
                  if step["operation"] == "read_series" and step["params"].get("aggregate") == "year"}
        months = self.plan["period"].get("months")
        names = []
        if self.plan.get("design") == "whatif":  # what was asked about: its archetypes, not the context variables
            names = [step["params"]["archetype"]["name"] for step in self.plan["steps"]
                     if step["operation"] == "resource_estimate"
                     and isinstance((step["params"].get("archetype") or {}).get("name"), dict)]
        for variable in [] if names else self.plan["variables"]:
            name = self.variables[variable]["name"]
            if variable == "air_temperature" and variable in annual:
                if months and len(set(months)) < 12:
                    name = {**name, "en": "Mean air temperature by year", "es": "Temperatura media del aire por año"}
                else:
                    name = {**name, "en": "Annual mean air temperature", "es": "Temperatura media anual del aire"}
            names.append(name)
        languages = [lang for lang in names[0] if all(lang in n for n in names)]
        return {lang: f"{place}: {', '.join(n[lang] for n in names)}" for lang in languages}

    def analysis(self, status: str) -> dict:
        data = {}
        for result in self.results:
            if "absence" in result:
                continue
            for ref in result["sources"]:
                version = ref.get("version") or self.sources.get(ref["source"], {}).get("version")
                if version:
                    data[ref["source"]] = version
        return {
            "contract_version": "1.0", "id": self.id, "title": self.title(), "created": self.created,
            "status": status, "plan": self.plan, "plan_fingerprint": self.plan_fp,
            "fingerprint": {"sha256": sha({"plan": self.plan_fp, "data": data}),
                            "catalog_version": self.catalog["version"], "data": data, "seed": self.seed},
            "results": [r["id"] for r in self.results],
            "findings": [] if status == "cancelled" else copy.deepcopy(self.findings or []),
            "alerts": [], "reports": [], **({"story": copy.deepcopy(self.story)}
                                           if status != "cancelled" and self.story is not None else {}),
        }

    def prepare(self) -> None:
        folder.directory(self.id).mkdir(parents=True, exist_ok=False)
        folder.open_events(self.id)  # with HUP_EVENTS_LOCAL_DIR, events go to local disk while the run is alive
        # On Windows, concurrent writes can fail while creating different shards under a missing cache directory.
        paths.derived().mkdir(exist_ok=True)
        folder.write_plan(self.id, self.plan)
        folder.write_results(self.id, [])
        folder.write_analysis(self.id, self.analysis("running"))

    # steps ----------------------------------------------------------------------------------------------------

    def inputs(self, step: dict, deps: list[Outcome]) -> dict[str, str]:
        """Source → version for what the step reads; step it depends on → that step's fingerprint."""
        inputs = {s: self.sources[s]["version"] for s in step.get("sources", []) if self.sources[s].get("version")}
        inputs.update({d.step: d.fingerprint for d in deps})
        return inputs

    def source_refs(self, step: dict, deps: list[Outcome]) -> list[dict]:
        refs = {}
        for source in step.get("sources", []):
            catalog_source = self.sources[source]
            if catalog_source["access"].get("url"):
                refs[source] = {"source": source, "url": catalog_source["access"]["url"]}
        for d in deps:
            for ref in d.sources:
                refs.setdefault(ref["source"], ref)
        return list(refs.values())

    def data_class(self, own: str | None, variable: str, deps: list["Outcome"]) -> str:
        """The class a result ends up with: the least direct of what the operation says, the class of its
        variable and the classes of the results it was computed from. A model never comes out as measured."""
        classes = [own, self.variables.get(variable, {}).get("data_class", "measured")]
        classes += [r["data_class"] for d in deps for r in d.results]
        known = [c for c in classes if c in DATA_CLASSES]
        return max(known, key=DATA_CLASSES.index)

    def complete(self, step: dict, fp: str, bodies, deps: list["Outcome"] = ()) -> list[dict]:
        """Results with id, analysis, step and fingerprint. Raises ValueError if any of them does not validate."""
        if not isinstance(bodies, dict) or not bodies or not all(isinstance(b, dict) for b in bodies.values()):
            raise ValueError("an operation returns {key: result body}")
        operation = self.operations[step["operation"]]
        ceiling = operation.get("evidence_level")
        declared = operation.get("diagnostics", [])
        results = []
        for key, body in bodies.items():
            result = {**plain(body), "id": f"r-{step['id']}-{key}", "analysis": self.id, "step": step["id"],
                      "fingerprint": fp}
            name = self.variables.get(result.get("variable"), {}).get("name")
            if name and "absence" in result:
                result["absence"] = reads.localized_absence(result["absence"], result["variable"], name)
            own = result.get("data_class")
            # An unknown class is left as it is, so that validation rejects it instead of quietly repairing it.
            if isinstance(result.get("variable"), str) and (own in DATA_CLASSES or own in (None, "inherit")):
                result["data_class"] = self.data_class(own, result["variable"], list(deps))
            problems = contracts.errors("result", result)
            if "evidence_level" in result and (ceiling is None or result["evidence_level"] > ceiling):
                problems.append(f"evidence level {result['evidence_level']} above its operation ({ceiling})")
            problems += [f"undeclared diagnostic {d}" for d in result.get("diagnostics", {}) if d not in declared]
            if problems:
                raise ValueError(f"result {key}: " + "; ".join(problems))
            results.append(result)
        return results

    def not_computed(self, step: dict, fp: str, variable: str, refs: list[dict],
                     deps: list["Outcome"] = ()) -> list[dict]:
        """The absence a step leaves when a step it depends on could not be computed."""
        output = self.operations[step["operation"]]["output"]
        if not refs:
            return []
        body = {"type": output["type"], "variable": variable, "data_class": output["data_class"],  # may be "inherit"
                "absence": {"reason": "not_computed",
                            "detail": text("Not computed: a step it depends on could not be computed.")},
                "sources": refs,
                "method": {"operation": step["operation"], "version": step["version"], "params": step["params"]}}
        try:
            return self.complete(step, fp, {"not_computed": body}, deps)
        except ValueError:
            log.exception("analysis %s step %s: the absence result does not validate", self.id, step["id"])
            return []

    def report_reads(self, step: dict, data) -> list[dict]:
        """One data_read per read the operation made, or a warning when the reader answered with an absence."""
        recorded = []
        for record in getattr(data, "reads", []) if isinstance(data, reads.Recording) else []:
            event = reads.events(record, self.variables.get(record["variable"], {}).get("name"))
            if event is None:
                log.warning("analysis %s step %s: a reader answer without origin or sources", self.id, step["id"])
                continue
            self.emit(*event, step["id"])
            if event[0] == "data_read":
                recorded.append(event[1])
        return recorded

    def fail(self, step: dict, code: str, message: str) -> None:
        self.failed = True
        self.emit("error", {"code": code, "text": text(message)}, step["id"])

    def step_failure(self, step, outcome, started, code, message):
        if step["operation"] != "zone_layer":
            self.fail(step, code, message)
            self.finish_step(step, started, [])
            return outcome
        detail = {"en": "This optional map layer could not be prepared.",
                  "es": "No se pudo preparar esta capa opcional del mapa."}
        self.emit("warning", {"code": "layer_unavailable", "text": detail}, step["id"])
        refs = outcome.sources or self.source_refs(
            {"sources": self.variables.get(outcome.variable, {}).get("sources", [])}, [],
        )
        if refs and outcome.variable:
            body = {"type": "layer", "variable": outcome.variable, "data_class": "measured", "sources": refs,
                    "period": step["params"].get("period", self.plan["period"]),
                    "absence": {"reason": "not_computed", "detail": detail},
                    "method": {"operation": step["operation"], "version": step["version"], "params": step["params"]}}
            outcome.results = self.complete(step, outcome.fingerprint, {"missing": body})
            self.keep(outcome.results)
        self.finish_step(step, started, outcome.results)
        outcome.ok = True
        return outcome

    def finish_step(self, step: dict, started: float, results: list[dict]) -> None:
        for result in results:
            self.emit("partial_result", {"result": result["id"]}, step["id"])
        self.emit("step_finished", {"seconds": round(time.monotonic() - started, 3),
                                    "results": [r["id"] for r in results]}, step["id"])

    async def step(self, step: dict, deps: list[Outcome]) -> Outcome:
        cancellation.checkpoint()
        operation = self.operations[step["operation"]]
        fp = step_fingerprint(step["operation"], step["version"], step["params"], self.zone, self.inputs(step, deps),
                              self.plan["period"])
        variable = step["params"].get("variable") or next((d.variable for d in deps if d.variable), None) \
            or self.plan["variables"][0]
        refs = self.source_refs(step, deps)
        outcome = Outcome(step["id"], False, fp, variable=variable, sources=refs)
        started = time.monotonic()
        started_data = {"operation": step["operation"], "visual_type": operation["visual_type"],
                        "phrase": operation["phrase"]}
        if step["type"] == "ingest":
            started_data["bbox"] = self.plan["zone"]["bbox"]
        self.emit("step_started", started_data, step["id"])

        if step["type"] in ("write", "review"):  # writing runs on whatever results exist, even after a failure
            await (self.write(step) if step["type"] == "write" else self.review(step))
            self.finish_step(step, started, [])
            outcome.ok = True
            return outcome

        if not all(d.ok for d in deps):
            self.emit("warning", {"code": "not_computed",
                                  "text": text("Left out: a step it depends on could not be computed.")}, step["id"])
            results = self.not_computed(step, fp, variable, refs, deps)
            self.keep(results)
            self.finish_step(step, started, results)
            outcome.results = results
            return outcome

        # Exposure depends on which local map packages cover this place and period.
        # A newly prepared package can change the chosen map without changing the plan.
        cacheable = (step["operation"] not in {"read_exposure", "zone_layer", "evaluate_chain"}
                     and not step["params"].get("grid_resolution_m")
                     and "weather_hypotheses" not in step["params"]  # the table can be valid with a missing read
                     and all(d.cacheable for d in deps))
        outcome.cacheable = cacheable
        data = reads.Recording(None, self.id, step["id"], step.get("sources", []), area=step["params"].get("area"))
        cached = await asyncio.to_thread(cancellation.call, data.derived, variable, fp) if cacheable else None
        if cached is not None:
            bodies = cached["results"]
        else:
            op = self.registry.get(step["operation"])
            if op is None or op.version != step["version"]:
                missing = f"Operation {step['operation']} {step['version']} is not available."
                return self.step_failure(step, outcome, started, "operation_missing", missing)
            readers = _readers()
            if step["type"] == "ingest" and readers is not None:
                data.readers = readers
            else:
                data = readers
            ctx = _context_class()(zone=copy.deepcopy(self.plan["zone"]), period=copy.deepcopy(self.plan["period"]),
                                   seed=self.seed, data=data,
                                   progress=lambda fraction, item="": self.progress(step["id"], fraction, item))
            inputs = {d.step: copy.deepcopy(d.results) for d in deps}
            try:
                bodies = await asyncio.to_thread(cancellation.call, op.fn, copy.deepcopy(step["params"]), inputs, ctx)
            except Exception:
                log.exception("analysis %s step %s: operation %s failed", self.id, step["id"], step["operation"])
                self.report_reads(step, data)
                return self.step_failure(step, outcome, started, "step_failed", "This step could not be computed.")
            recorded = self.report_reads(step, data)

        try:
            results = self.complete(step, fp, bodies, deps)
        except ValueError as error:
            log.error("analysis %s step %s: %s", self.id, step["id"], error)
            return self.step_failure(step, outcome, started, "invalid_result",
                                     "This step returned a result that does not follow the contract.")

        if cached is not None:
            recorded = cached.get("data_reads")
            if recorded is None and step["type"] == "ingest":
                recorded = reads.legacy_events(results)
            elif not recorded:
                names = sorted({ref["source"] for result in results for ref in result["sources"]})
                recorded = [{"source": ", ".join(names), "n_items": 0}]
            for event in recorded or ():
                self.emit("data_read", {**event, "origin": "derived"}, step["id"])
        elif cacheable and not any("absence" in r for r in results):
            await asyncio.to_thread(store.put_derived, fp, {"results": plain(bodies), "data_reads": recorded})
        self.keep(results)
        self.finish_step(step, started, results)
        outcome.ok, outcome.results = True, results
        outcome.variable = results[0]["variable"]
        outcome.sources = self.source_refs(step, deps) + [ref for r in results for ref in r["sources"]]
        return outcome

    # writing and review ------------------------------------------------------------------------------------------

    async def call(self, module: str, name: str, *args):
        """One writer or reviewer function, in a thread, with a time limit. Arguments are copies."""
        function = _function(module, name)
        return await asyncio.wait_for(asyncio.to_thread(cancellation.call, function, *copy.deepcopy(args)),
                                      WRITING_SECONDS)

    def warn(self, step: dict, code: str, en: str, es: str) -> None:
        self.emit("warning", {"code": code, "text": {"en": en, "es": es}}, step["id"])

    def give_up(self, step: dict, code: str, en: str, es: str) -> None:
        """Discard the draft and leave publication to the deterministic report."""
        log.exception("analysis %s step %s: %s", self.id, step["id"], code)
        self.findings, self.story = [], None
        self.editorial_notice = {"en": en, "es": es}
        self.warn(step, code, en, es)

    def accept(self, step: dict, findings, story) -> bool:
        """Keep findings and story only if the analysis that holds them still follows the contract."""
        self.findings, self.story = findings, story
        if isinstance(findings, list) and not contracts.errors("analysis", self.analysis("running")):
            return True
        log.error("analysis %s step %s: the findings or the story do not follow the contract", self.id, step["id"])
        self.findings, self.story = [], None
        self.editorial_notice = {
            "en": "The written findings did not pass validation and were left out.",
            "es": "Los hallazgos escritos no pasaron la validación y se han dejado fuera.",
        }
        self.warn(step, "invalid_findings", **self.editorial_notice)
        return False

    async def compose(self, step: dict) -> None:
        try:
            story = await self.call(WRITER, "compose", self.plan, self.results, self.findings, self.catalog)
            if not isinstance(story, dict):
                raise TypeError("compose returns a dict")
            self.accept(step, self.findings, story)
        except Exception:
            self.give_up(step, "writing_failed", "The report could not be composed for this analysis.",
                         "No se ha podido componer el informe de este análisis.")

    def recover_report(self) -> None:
        if self.story is not None:
            return
        step = next((s for s in reversed(self.plan["steps"]) if s["type"] in {"write", "review"}), None)
        if step is None:
            return
        cancellation.checkpoint()
        try:
            story = compose_fallback(self.plan, self.results, self.catalog, notice=self.editorial_notice)
            self.accept(step, [], story)
        except Exception:
            log.exception("analysis %s: deterministic report could not be composed", self.id)
            self.warn(step, "report_fallback_failed", "The report could not be published.",
                      "No se ha podido publicar el informe.")

    async def write(self, step: dict) -> None:
        try:
            findings = await self.call(WRITER, "write_findings", self.plan, self.results, self.catalog)
            reviewed_later = any(s["type"] == "review" for s in self.plan["steps"])
            # With no findings there is still a report: the results, their sources, their limits and what is missing.
            if self.accept(step, findings, None) and not reviewed_later and (findings or self.results):
                await self.compose(step)
        except Exception:
            self.give_up(step, "writing_failed", "The findings could not be written for this analysis.",
                         "No se han podido redactar los hallazgos de este análisis.")

    def take_reviewed(self, step: dict, results) -> None:
        """The reviewer's copy of each result, if it only lowered the level or added limits; else the original."""
        given = results if isinstance(results, list) else []
        by_id = {r["id"]: r for r in given if isinstance(r, dict) and isinstance(r.get("id"), str)}
        kept = []
        for original in self.results:
            new = by_id.get(original["id"], original)
            if new is not original:
                same = ({k: v for k, v in original.items() if k not in REVIEWABLE}
                        == {k: v for k, v in new.items() if k not in REVIEWABLE})
                level, before = new.get("evidence_level", "E0"), original.get("evidence_level", "E0")
                lower = level in EVIDENCE and EVIDENCE.index(level) <= EVIDENCE.index(before)
                if not (same and lower and not contracts.errors("result", new)):
                    self.warn(step, "review_changed_result",
                              "The review tried to change a result beyond its level; it was kept as computed.",
                              "La revisión intentó cambiar un resultado más allá de su nivel; se conserva tal "
                              "como se calculó.")
                    new = original
            kept.append(new)
        with self.lock:
            cancellation.checkpoint()
            self.results = kept
            folder.write_results(self.id, self.results)

    async def review(self, step: dict) -> None:
        if not self.findings:
            self.warn(step, "review_skipped", "There are no findings to review.", "No hay hallazgos que revisar.")
            if self.findings is not None and self.results and self.editorial_notice is None:
                await self.compose(step)
            return
        try:
            out = await self.call(REVIEWER, "review", self.results, self.findings, self.catalog)
            if not isinstance(out, dict) or not isinstance(out.get("findings"), list):
                raise TypeError("review returns {results, findings, warnings}")
            self.take_reviewed(step, out.get("results"))
            for warning in out.get("warnings") or []:
                text = warning.get("text") if isinstance(warning, dict) else None
                if isinstance(text, dict) and isinstance(text.get("en"), str) and isinstance(warning.get("code"), str):
                    self.emit("warning", {"code": warning["code"],
                                          "text": {k: v for k, v in text.items() if isinstance(v, str)}}, step["id"])
            if self.accept(step, out["findings"], None) and self.findings:
                await self.compose(step)
        except Exception:
            self.give_up(step, "review_failed", "The findings could not be reviewed and were left out.",
                         "No se han podido revisar los hallazgos y se han dejado fuera.")

    async def after(self, step: dict, waits: list[asyncio.Task]) -> Outcome:
        deps = [await task for task in waits]
        try:
            return await self.step(step, deps)
        except Exception:
            log.exception("analysis %s step %s: executor error", self.id, step["id"])
            self.fail(step, "step_failed", "This step could not be computed.")
            return Outcome(step["id"], False, sha(["failed", self.id, step["id"]]))

    async def execute(self) -> None:
        with self.lock:
            if self.cancelled.is_set():
                return
            self.execution = asyncio.get_running_loop(), asyncio.current_task()
        token = cancellation.signal.set(self.cancelled)
        started = time.monotonic()
        predicted = await asyncio.to_thread(timing.forecast, self.plan, self.catalog)
        try:
            tasks: dict[str, asyncio.Task] = {}
            for step in order(self.plan["steps"]):
                tasks[step["id"]] = asyncio.create_task(self.after(step, [tasks[d] for d in step["depends_on"]]))
            await asyncio.gather(*tasks.values())
            self.recover_report()
        except (asyncio.CancelledError, cancellation.Cancelled):
            if not self.cancelled.is_set():
                raise
        except Exception:
            log.exception("analysis %s: executor error", self.id)
            self.failed = True
            self.emit("error", {"code": "internal", "text": text("The analysis stopped on a server error.")})
        finally:
            cancellation.signal.reset(token)
            with self.lock:
                self.execution = None
                analysis = None if self.cancelled.is_set() else self._finish("failed" if self.failed else "finished")
            if analysis is not None:
                self._record(analysis)
                timing.record(self.id, predicted, time.monotonic() - started, analysis["status"])

    def _finish(self, status: str) -> dict:
        """The lock serializes the only terminal event and state write."""
        self.status = status
        kind = "analysis_cancelled" if status == "cancelled" else "analysis_finished"
        self._emit(kind, {"status": status})
        try:  # the data folder gets every event before analysis.json says the analysis is over
            folder.close_events(self.id)
        except Exception:
            log.exception("analysis %s: the events could not be copied to the data folder", self.id)
        analysis = self.analysis(status)
        for problem in contracts.errors("analysis", analysis):
            log.error("analysis %s does not validate: %s", self.id, problem)
        folder.write_analysis(self.id, analysis)
        return analysis

    def _record(self, analysis: dict) -> None:
        try:
            if analysis["status"] == "cancelled":
                index.remove(self.id)
            else:
                plan_cache.record(analysis, self.catalog)
                index.record(analysis)
        except Exception:
            log.exception("analysis %s: the library could not record it", self.id)

    def cancel(self) -> tuple[bool, str]:
        with self.lock:
            if self.status != "running":
                return False, self.status
            self.cancelled.request()  # the capacity check stops counting this job immediately
            if self.execution:
                loop, task = self.execution
                loop.call_soon_threadsafe(task.cancel)
            analysis = self._finish("cancelled")
        self._record(analysis)
        return True, "cancelled"


class Busy(Exception):
    """As many analyses as HUP_MAX_RUNNING_ANALYSES are already running in this process."""


_running: dict[str, Run] = {}
_running_lock = threading.Lock()


def running() -> int:
    """How many analyses this process is running now."""
    with _running_lock:
        return _occupied()


def _occupied() -> int:
    return sum(not job.cancelled.is_set() for job in _running.values())


def active(analysis_id: str) -> bool:
    with _running_lock:
        return analysis_id in _running


def cancel(analysis_id: str) -> tuple[bool, str] | None:
    from hup_api.executor import recovery

    with _running_lock:
        job = _running.get(analysis_id)
    if job is not None:
        return job.cancel()
    outcome = recovery.cancel_stored(analysis_id)
    if outcome and outcome[1] == "cancelled":
        try:
            index.remove(analysis_id)
        except Exception:
            log.exception("analysis %s: the library could not remove it", analysis_id)
    return outcome


def _background(job: Run) -> None:
    try:
        asyncio.run(job.execute())
    finally:
        with _running_lock:
            _running.pop(job.id, None)


def run(plan: dict, catalog: dict | None = None, registry: dict | None = None) -> str:
    """Start the plan in the background and return its analysis id. Raises InvalidPlan before starting anything,
    and Busy when HUP_MAX_RUNNING_ANALYSES analyses are running already."""
    catalog = catalog if catalog is not None else catalog_module.load()
    errors = validate_plan(plan, catalog)
    if errors:
        raise InvalidPlan(errors)
    job = Run(new_id(), plan, catalog, default_registry() if registry is None else registry)
    cap = settings.load().max_running_analyses
    with _running_lock:
        occupied = _occupied()
        if cap is not None and occupied >= cap:
            raise Busy(f"{occupied} analyses are running")
        _running[job.id] = job
    try:
        job.prepare()
        threading.Thread(target=_background, args=(job,), name=f"analysis-{job.id}", daemon=True).start()
    except BaseException:
        with _running_lock:
            _running.pop(job.id, None)
        raise
    return job.id
