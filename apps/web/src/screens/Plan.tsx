import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  apiMessage,
  createAnalysis,
  decisionsOf,
  getCatalog,
  getStartingPlan,
  reportPath,
  validatePlan,
  type PlanValidation,
} from "../api/client";
import type { plan } from "../contracts";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";
import { Button, buttonClass } from "../ui/controls";

type AnalysisPlan = plan.AnalysisPlan;
type Step = AnalysisPlan["steps"][number];
type Origin = NonNullable<Step["data"]>["origin"];
type Level = AnalysisPlan["answer"]["level"];

export type VariableInfo = { id: string; name: { [language: string]: string | undefined } };
export type CatalogLike = {
  variables: VariableInfo[];
  operations?: { id: string; phrase?: { [language: string]: string | undefined } }[];
  sources?: { id: string; name: string }[];
};

const LEVEL: Record<Level, { label: MessageKey; note: MessageKey; tag: string }> = {
  complete: {
    label: "plan.level.complete.label",
    note: "plan.level.complete.note",
    tag: "border-signal bg-signal-soft text-signal",
  },
  with_limits: {
    label: "plan.level.with_limits.label",
    note: "plan.level.with_limits.note",
    tag: "border-amber bg-amber-soft text-amber-ink",
  },
  not_possible: {
    label: "plan.level.not_possible.label",
    note: "plan.level.not_possible.note",
    tag: "border-alert bg-alert-soft text-alert",
  },
};

const ORIGIN: Record<Origin, MessageKey> = {
  derived: "plan.origin.derived",
  cache: "plan.origin.cache",
  package: "plan.origin.package",
  remote: "plan.origin.remote",
};

const TYPE: Record<Step["type"], MessageKey> = {
  ingest: "plan.type.ingest",
  compute: "plan.type.compute",
  write: "plan.type.write",
  review: "plan.type.review",
};

function year(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? iso : String(date.getUTCFullYear());
}

function strip(error: string, prefix: string): string {
  return error.startsWith(prefix) ? error.slice(prefix.length) : error;
}

export type PlanReviewProps = {
  initialPlan: AnalysisPlan;
  catalog?: CatalogLike | null;
  validate: (candidate: AnalysisPlan) => Promise<PlanValidation>;
  onRun?: (candidate: AnalysisPlan) => Promise<void>;
  /** The plan of an analysis that is already running: shown to explain it, never run again. */
  analysisId?: string | null;
};

export function PlanReview({ initialPlan, catalog, validate, onRun, analysisId = null }: PlanReviewProps) {
  const { t, language } = useI18n();
  const [candidate, setCandidate] = useState<AnalysisPlan>(initialPlan);
  const [validation, setValidation] = useState<PlanValidation | null>(null);
  const [checking, setChecking] = useState(true);
  const [failed, setFailed] = useState(false);
  const [failureText, setFailureText] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const latest = useRef(0);

  useEffect(() => {
    const token = ++latest.current;
    setChecking(true);
    validate(candidate)
      .then((result) => {
        if (token !== latest.current) return;
        setValidation(result);
        setFailed(false);
      })
      .catch(() => {
        if (token !== latest.current) return;
        setValidation(null);
        setFailed(true);
      })
      .finally(() => {
        if (token === latest.current) setChecking(false);
      });
  }, [candidate, validate]);

  const shown = validation?.plan ?? candidate;
  const level = shown.answer.level;
  const canRun = Boolean(onRun) && !analysisId && !checking && !running && !failed && Boolean(validation?.valid) && level !== "not_possible";
  const errors = validation?.errors ?? [];
  const planErrors = errors.filter((error) => !error.startsWith("step ")).map((error) => strip(error, "plan: "));
  const stepErrors = (id: string) => errors.filter((error) => error.startsWith(`step ${id}:`)).map((error) => strip(error, `step ${id}: `));

  const status = analysisId
    ? t("plan.running")
    : failed
    ? failureText || t("plan.checkFailed")
    : checking
      ? t("plan.checking")
      : level === "not_possible"
        ? t("plan.cannotRun")
        : validation && !validation.valid
          ? t("plan.toFix")
          : t("plan.ready");

  const text = (value?: Record<string, string>) => (value ? (value[language] ?? value.en) : "");
  const bbox = new Intl.NumberFormat(language, { maximumFractionDigits: 3 });
  const seconds = new Intl.NumberFormat(language, { style: "unit", unit: "second", unitDisplay: "short", maximumFractionDigits: 0 });
  const megabytes = new Intl.NumberFormat(language, { style: "unit", unit: "megabyte", unitDisplay: "short", maximumFractionDigits: 0 });
  const months = new Intl.DateTimeFormat(language, { month: "long" });
  const monthName = (month: number) => months.format(new Date(Date.UTC(2020, month - 1, 15)));

  const optionIds = initialPlan.variables;
  const options: VariableInfo[] = optionIds.map((id) => ({
    id,
    name: catalog?.variables.find((variable) => variable.id === id)?.name ?? { en: id },
  }));

  function toggleVariable(id: string) {
    setCandidate((current) => {
      const next = current.variables.includes(id)
        ? current.variables.filter((variable) => variable !== id)
        : [...current.variables, id];
      return { ...current, variables: next as unknown as AnalysisPlan["variables"] };
    });
  }

  // What a step does, in the catalogue's words; the operation id only when the catalogue is not there.
  function doing(step: Step): string {
    const phrase = catalog?.operations?.find((operation) => operation.id === step.operation)?.phrase;
    const words = phrase ? (phrase[language] ?? phrase.en ?? "") : "";
    if (!words) return step.operation;
    const sourceId = (step as { sources?: string[] }).sources?.[0];
    const source = catalog?.sources?.find((item) => item.id === sourceId)?.name ?? sourceId ?? "";
    return words
      .replace("{source}", source)
      .replace(/\s*\{[^}]+\}/g, "")
      .replace(/\s+([,.;:])/g, "$1")
      .trim();
  }

  const decisions = decisionsOf(shown);

  function wait(step: Step): string {
    if (!step.data) return t("plan.none");
    const parts: string[] = [];
    if (step.data.estimated_seconds !== undefined) parts.push(seconds.format(step.data.estimated_seconds));
    if (step.data.estimated_mb !== undefined) parts.push(megabytes.format(step.data.estimated_mb));
    return parts.length > 0 ? parts.join(" · ") : t("plan.none");
  }

  async function run() {
    if (!canRun || !onRun) return;
    setRunning(true);
    try {
      await onRun(validation?.plan ?? candidate);
    } catch (error) {
      setFailureText(apiMessage(error, language, ""));
      setFailed(true);
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className={`${PAGE} fade-in py-22`}>
      <section className="rounded-xl border border-line bg-paper p-16">
        <div className="flex flex-col gap-16">
          <div className="flex flex-wrap items-baseline justify-between gap-10">
            <h1 className="title-1 text-balance">{t("plan.title")}</h1>
            <span className="label inline-flex items-center rounded-xs border border-line-strong px-6 py-2 text-muted">
              {t(analysisId ? "plan.running" : "plan.notRun")}
            </span>
          </div>

          {candidate.request.text && (
            <p className="small text-muted">
              {t("plan.question")}: <b className="body text-ink">{candidate.request.text}</b>
            </p>
          )}

          {decisions.length > 0 && (
            <section aria-labelledby="plan-decided" className="flex flex-col gap-8 rounded-md border border-line bg-soft p-16">
              <h2 id="plan-decided" className="title-2">
                {t("plan.decided")}
              </h2>
              <ul className="grid list-disc gap-4 pl-16">
                {decisions.map((decision, index) => (
                  <li key={`${decision.field}-${index}`} className="body">
                    {text(decision.text)}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="plan-answer" className="flex flex-col gap-10 rounded-md border border-line p-16">
            <h2 id="plan-answer" className="title-2">
              {t("plan.answerTitle")}
            </h2>
            <span className={`label inline-flex w-fit items-center rounded-xs border px-6 py-2 ${LEVEL[level].tag}`}>
              {t(LEVEL[level].label)}
            </span>
            <p className="small text-muted">{t(LEVEL[level].note)}</p>
            {shown.answer.limits && shown.answer.limits.length > 0 && (
              <div className="flex flex-col gap-4">
                <h3 className="label text-muted">{t("plan.limits")}</h3>
                <ul className="grid list-disc gap-4 pl-16">
                  {shown.answer.limits.map((limit, index) => (
                    <li key={index} className="small">
                      {text(limit)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {level === "not_possible" && shown.answer.alternative && (
              <div className="flex flex-wrap items-center gap-10">
                <p className="body">
                  {t("plan.alternative")}: {text(shown.answer.alternative)}
                </p>
                <Link
                  to="/"
                  state={{ question: text(shown.answer.alternative) }}
                  className="body font-semibold text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  {t("plan.askAlternative")}
                </Link>
              </div>
            )}
          </section>

          {validation?.existing_analysis && (
            <div className="flex flex-wrap items-center gap-10 rounded-md border border-line bg-soft p-16">
              <p className="body">{t("plan.existing")}</p>
              <Link
                to={`/a/${validation.existing_analysis}`}
                className="body font-semibold text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                {t("plan.openExisting")}
              </Link>
            </div>
          )}

          <div className="grid gap-16 md:grid-cols-2">
            <section className="flex flex-col gap-6">
              <h2 className="title-2">{t("plan.place")}</h2>
              <p className="body">{shown.zone.name}</p>
              {shown.zone.source && (
                <p className="small text-muted">
                  {t("plan.source")}: {shown.zone.source}
                </p>
              )}
              <p className="data text-muted">
                {t("plan.bbox")}: ({bbox.format(shown.zone.bbox[0])}, {bbox.format(shown.zone.bbox[1])}) – (
                {bbox.format(shown.zone.bbox[2])}, {bbox.format(shown.zone.bbox[3])})
              </p>
            </section>

            <section className="flex flex-col gap-6">
              <h2 className="title-2">{t("plan.period")}</h2>
              <p className="body">
                {year(shown.period.start)}–{year(shown.period.end)}
              </p>
              {shown.period.months && shown.period.months.length > 0 && (
                <p className="small text-muted">
                  {t("plan.months")}: {shown.period.months.map(monthName).join(", ")}
                </p>
              )}
            </section>
          </div>

          <fieldset className="flex flex-col gap-8">
            <legend className="label pb-4 text-muted">{t("plan.variables")}</legend>
            {options.map((variable) => (
              <label key={variable.id} className="body flex items-center gap-8">
                <input
                  type="checkbox"
                  className="accent-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                  checked={candidate.variables.includes(variable.id)}
                  onChange={() => toggleVariable(variable.id)}
                />
                {variable.name[language] ?? variable.name.en ?? variable.id}
              </label>
            ))}
          </fieldset>

          <section className="flex flex-col gap-8">
            <h2 className="title-2">{t("plan.steps")}</h2>
            <ol className="flex flex-col gap-8">
              {shown.steps.map((step) => (
                <li key={step.id} className="flex flex-col gap-4 rounded-md border border-line p-10">
                  <div className="flex flex-wrap items-baseline gap-8">
                    <span className="body">{doing(step)}</span>
                    <span className="small text-muted">{t(TYPE[step.type])}</span>
                    {step.data && (
                      <span className="small ml-auto rounded-xs border border-line-strong px-6 py-2 text-muted">
                        {t(ORIGIN[step.data.origin])}
                      </span>
                    )}
                  </div>
                  <p className="small text-muted">
                    {t("plan.wait")}: {wait(step)}
                  </p>
                  {step.data?.origin === "package" && <p className="small text-amber-ink">{t("plan.origin.packageNote")}</p>}
                  {stepErrors(step.id).map((error, index) => (
                    <p key={index} className="small text-alert">
                      ! {error}
                    </p>
                  ))}
                </li>
              ))}
            </ol>
          </section>

          {planErrors.length > 0 && (
            <ul className="flex flex-col gap-4" aria-live="polite">
              {planErrors.map((error, index) => (
                <li key={index} className="small text-alert">
                  ! {error}
                </li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-10 border-t border-line pt-16">
            <p role="status" className="small text-muted">
              {status}
            </p>
            {analysisId ? (
              <div className="ml-auto flex flex-wrap items-center gap-10">
                <Link to={`/run/${encodeURIComponent(analysisId)}`} className={buttonClass("secondary")}>
                  {t("plan.backToRun")}
                </Link>
                <Link to={reportPath(analysisId)} className={buttonClass("primary")}>
                  {t("plan.openReport")}
                </Link>
              </div>
            ) : (
              <div className="ml-auto flex flex-wrap items-center gap-10">
                <Link to="/" className={buttonClass("secondary")}>
                  {t("plan.askAgain")}
                </Link>
                <Button type="button" disabled={!canRun} onClick={run}>
                  {t("plan.run")}
                </Button>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

type RouteState = { plan?: AnalysisPlan; analysisId?: string } | null;

export function Plan() {
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();
  const [initial, setInitial] = useState<AnalysisPlan | null>(() => (location.state as RouteState)?.plan ?? null);
  const [catalog, setCatalog] = useState<CatalogLike | null>(null);
  const [missing, setMissing] = useState(false);
  const analysisId = (location.state as RouteState)?.analysisId ?? null;

  useEffect(() => {
    if (initial) return;
    let live = true;
    getStartingPlan()
      .then((starting) => {
        if (!live) return;
        if (starting) setInitial(starting);
        else setMissing(true);
      })
      .catch(() => {
        if (live) setMissing(true);
      });
    return () => {
      live = false;
    };
  }, [initial]);

  useEffect(() => {
    if (!initial) return;
    let live = true;
    getCatalog()
      .then((current) => {
        if (live) setCatalog(current);
      })
      .catch(() => {
        // The variables fall back to their identifiers.
      });
    return () => {
      live = false;
    };
  }, [initial]);

  async function run(candidate: AnalysisPlan) {
    const { analysis_id } = await createAnalysis(candidate);
    navigate(`/run/${analysis_id}`);
  }

  if (missing) {
    return (
      <main className={`${PAGE} py-22`}>
        <section className="flex flex-col gap-16 rounded-xl border border-line bg-paper p-16">
          <h1 className="title-1">{t("plan.title")}</h1>
          <p className="body text-muted">{t("plan.askFirst")}</p>
          <Link to="/" className={`${buttonClass("secondary")} w-fit`}>
            {t("plan.askAgain")}
          </Link>
        </section>
      </main>
    );
  }

  if (!initial) {
    return (
      <main className={`${PAGE} py-22`}>
        <p className="body text-muted">{t("plan.loading")}</p>
      </main>
    );
  }

  return (
    <PlanReview initialPlan={initial} catalog={catalog} validate={validatePlan} onRun={run} analysisId={analysisId} />
  );
}
