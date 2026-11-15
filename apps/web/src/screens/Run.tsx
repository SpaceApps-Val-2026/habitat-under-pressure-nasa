import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  cancelAnalysis,
  decisionsOf,
  estimatePlan,
  getAnalysis,
  getAnalysisResults,
  getCatalog,
  openRunEvents,
  panelPath,
  reportPath,
  type PlanEstimate,
  type StepEstimate,
} from "../api/client";
import type { RunEvent } from "../api/events";
import { formatMeasure, periodText, trendPresentation, type StepResult } from "../blocks/shared";
import type { plan } from "../contracts";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";
import { useUnits } from "../units";
import { absenceOf, finalStatus, generalError, lastError, stepsOf, type StepState } from "./run-model";
import {
  kindOfStep,
  LABEL_LINE,
  placeNodes,
  sceneNodes,
  shortName,
  spoke,
  toneOfLayer,
  VIEW,
  type NodeKind,
  type PlacedNode,
  type PlanStepInfo,
} from "./run-scene";

import {
  about,
  estimatesFor,
  pending,
  timeText,
  waitingHint,
  waitingRows,
  weightedProgress,
} from "./run-estimate";
import "./run-stage.css";

type RunStatus = "connecting" | "reconnecting" | "running" | "finished" | "failed" | "cancelled" | "unreachable";

type OperationInfo = {
  id: string;
  visual_type: string;
  phrase: Record<string, string>;
  name?: Record<string, string> | null;
};
type SourceInfo = { id: string; name: string };
type VariableInfo = { id: string; name: Record<string, string | undefined>; layer?: string | null };
type CardState = "waiting" | "active" | "ready" | "warn";
type AnswerCard = { key: string; label: string; state: CardState; done: number; total: number; body: ReactNode };

const STATUS_KEY: Record<RunStatus, MessageKey> = {
  connecting: "run.status.connecting",
  reconnecting: "run.status.reconnecting",
  running: "run.status.running",
  finished: "run.status.finished",
  failed: "run.status.failed",
  cancelled: "run.status.cancelled",
  unreachable: "run.status.unreachable",
};

const STATUS_TONE: Record<RunStatus, string> = {
  connecting: "border-line-strong text-muted",
  reconnecting: "border-amber text-amber-ink",
  running: "border-line-strong text-ink",
  finished: "border-signal text-signal",
  failed: "border-alert text-alert",
  unreachable: "border-alert text-alert",
  cancelled: "border-line-strong bg-soft text-muted",
};

const ORIGIN: Record<string, MessageKey> = {
  derived: "run.origin.derived",
  cache: "run.origin.cache",
  package: "run.origin.package",
  remote: "run.origin.remote",
};

const SEVERITY: Record<string, MessageKey> = {
  info: "run.severity.info",
  attention: "run.severity.attention",
  critical: "run.severity.critical",
};

const PHASE: Record<string, MessageKey> = {
  download: "run.phase.download",
  processing: "run.phase.processing",
};

const KIND_LABEL: Record<NodeKind, MessageKey> = {
  question: "run.stage.kind.question",
  zone: "run.stage.kind.zone",
  data: "run.stage.kind.satellite",
  compute: "run.stage.kind.compute",
  write: "run.stage.kind.write",
  review: "run.stage.kind.review",
};

const CARD_STATE: Record<CardState, MessageKey> = {
  waiting: "run.stage.cardState.waiting",
  active: "run.stage.cardState.active",
  ready: "run.stage.cardState.ready",
  warn: "run.stage.cardState.warn",
};

function dictionary(value: unknown): Record<string, string> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, string>) : null;
}

function useLocalized() {
  const { language } = useI18n();
  return (value: unknown): string => {
    const text = dictionary(value);
    return text ? (text[language] ?? text.en ?? "") : "";
  };
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function clockOf(ts: string, language: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  try {
    return new Intl.DateTimeFormat(language, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  } catch {
    return ts;
  }
}

// A catalogue phrase with whatever placeholder it still has taken out, never shown as «…» or «{n}».
function withoutHoles(phrase: string): string {
  return phrase
    .replace(/\s*\{[^}]+\}/g, "")
    .replace(/\s+([,.;:])/g, "$1")
    .trim();
}

function NodeIcon({ kind }: { kind: NodeKind }) {
  switch (kind) {
    case "question":
      return (
        <g className="run-ico" transform="translate(-12 -12)">
          <path d="M9 9a3 3 0 1 1 4.5 2.6c-.9.5-1.5 1.2-1.5 2.4" />
          <circle cx="12" cy="17.5" r=".6" />
        </g>
      );
    case "zone":
      return (
        <g className="run-ico" transform="translate(-12 -12)">
          <path d="M12 21s6-5.3 6-10a6 6 0 1 0-12 0c0 4.7 6 10 6 10z" />
          <circle cx="12" cy="11" r="2" />
        </g>
      );
    case "data":
      return (
        <g className="run-ico" transform="translate(-12 -12)">
          <rect x="9" y="9" width="6" height="6" rx="1" transform="rotate(45 12 12)" />
          <path d="M5 5l3 3M16 16l3 3M4 9l3-3M17 20l3-3" />
        </g>
      );
    case "compute":
      return (
        <g className="run-ico" transform="translate(-12 -12)">
          <path d="M4 18l5-6 4 3 7-9" />
          <path d="M4 21h16" />
        </g>
      );
    case "review":
      return (
        <g className="run-ico" transform="translate(-12 -12)">
          <circle cx="11" cy="11" r="6" />
          <path d="M15.5 15.5L20 20M8.5 11l2 2 3.5-4" />
        </g>
      );
    default:
      return (
        <g className="run-ico" transform="translate(-12 -12)">
          <path d="M4 20l4-1 11-11-3-3L5 16z" />
          <path d="M14 7l3 3" />
        </g>
      );
  }
}

function Warning({ event }: { event: RunEvent }) {
  const { t } = useI18n();
  const text = useLocalized();
  const data = event.data as Record<string, unknown>;
  return (
    <div className="flex flex-wrap items-baseline gap-6 rounded-md bg-amber-soft p-8 text-amber-ink">
      <span className="label">{t("run.warning")}</span>
      <span className="small">{text(data.text)}</span>
    </div>
  );
}

function Alert({ event }: { event: RunEvent }) {
  const { t } = useI18n();
  const text = useLocalized();
  const data = event.data as Record<string, unknown>;
  const severity = String(data.severity ?? "");
  return (
    <div className="flex flex-wrap items-baseline gap-6 rounded-md bg-soft p-8 text-ink">
      <span className="label">{t("run.alert")}</span>
      <span className="label">{SEVERITY[severity] ? t(SEVERITY[severity]) : t("run.severity.unknown")}</span>
      {text(data.text) && <span className="small">{text(data.text)}</span>}
    </div>
  );
}

function RunError({ event }: { event: RunEvent }) {
  const { t } = useI18n();
  const text = useLocalized();
  const data = event.data as Record<string, unknown>;
  return (
    <div className="flex flex-wrap items-baseline gap-6 rounded-md bg-alert-soft p-8 text-alert">
      <span className="label">{t("run.error")}</span>
      <span className="small">{text(data.text)}</span>
    </div>
  );
}

function SceneNodeMark({ node, format }: { node: PlacedNode; format: (n: number) => string }) {
  return (
    <g className={`run-node is-${node.state} ${node.tone} ring-${node.ring}`} transform={`translate(${node.x} ${node.y})`}>
      <title>
        {node.detail}
        {node.count !== null ? ` · ${format(node.count)}` : ""}
        {node.reason ? ` · ${node.reason}` : ""}
      </title>
      {node.state === "active" && <circle className="run-halo" r={node.r} />}
      <circle className="run-ring" r={node.r} />
      <NodeIcon kind={node.kind} />
      {node.state === "done" && (
        <g className="run-check" transform={`translate(${node.r * 0.72} ${-node.r * 0.72})`}>
          <circle r={7} />
          <path d="M-3 0l2 2 4-4" />
        </g>
      )}
      <text className="run-node-label" textAnchor={node.anchor} x={node.lx} y={node.ly}>
        {node.lines.map((line, index) => (
          <tspan key={index} x={node.lx} dy={index === 0 ? 0 : LABEL_LINE}>
            {line}
          </tspan>
        ))}
      </text>
    </g>
  );
}

export function RunView({
  analysisId,
  events,
  connected,
  unreachable,
  onReplay,
  analysisPlan = null,
  operations = [],
  sources = [],
  variables = [],
  results = [],
  openIn = 3000,
  opening = false,
  onCancel,
  cancelling = false,
  cancelled = false,
  onAskAgain,
  historyWindowMs = 800,
  estimate = null,
  clock,
}: {
  analysisId: string;
  events: RunEvent[];
  connected: boolean;
  unreachable: boolean;
  onReplay?: () => void;
  analysisPlan?: plan.AnalysisPlan | null;
  operations?: OperationInfo[];
  sources?: SourceInfo[];
  variables?: VariableInfo[];
  /** What the steps have saved so far; the key figure card shows its value from here. */
  results?: StepResult[];
  openIn?: number;
  /** The run has finished and the report opens by itself in a moment. */
  opening?: boolean;
  /** Cancel the run on the server. */
  onCancel?: () => void;
  /** The cancel request is on its way. */
  cancelling?: boolean;
  /** The saved analysis says it was cancelled (its events may not say it yet). */
  cancelled?: boolean;
  /** Back to the cover with the question, after a cancelled run. */
  onAskAgain?: () => void;
  /** Events that arrive within this time of the first ones are the run's history: shown, not animated. */
  historyWindowMs?: number;
  /** The expected time of each step (POST /api/plans/estimate); null: the bar counts finished steps. */
  estimate?: PlanEstimate | null;
  /** The time «now», for tests; the screen keeps its own clock otherwise. */
  clock?: number;
}) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const text = useLocalized();
  const steps = stepsOf(events);
  const streamed = finalStatus(events);
  const final = streamed === "running" && cancelled ? "cancelled" : streamed;
  const error = lastError(events);
  const general = generalError(events);
  const status: RunStatus = error
    ? "failed"
    : final !== "running"
      ? final
      : unreachable
        ? "unreachable"
        : connected
          ? "running"
          : events.length > 0
            ? "reconnecting"
            : "connecting";
  const numbers = new Intl.NumberFormat(language);
  const format = (n: number) => numbers.format(n);
  const bytes = new Intl.NumberFormat(language, { style: "unit", unit: "megabyte", unitDisplay: "short", maximumFractionDigits: 20 });
  const seconds = new Intl.NumberFormat(language, { style: "unit", unit: "second", unitDisplay: "short", maximumFractionDigits: 20 });
  const plural = new Intl.PluralRules(language);
  const itemWord = (count: number) => t(plural.select(count) === "one" ? "run.items.one" : "run.items.other");
  const stepWord = (count: number) => t(plural.select(count) === "one" ? "run.stage.steps.one" : "run.stage.steps.other");
  const generalEvents = events.filter((event) => !event.step && (event.type === "warning" || event.type === "alert"));

  const operationMap = useMemo(() => new Map(operations.map((operation) => [operation.id, operation])), [operations]);
  const sourceMap = useMemo(() => new Map(sources.map((source) => [source.id, source.name])), [sources]);
  const variableMap = useMemo(() => new Map(variables.map((variable) => [variable.id, variable])), [variables]);
  const stateById = useMemo(() => new Map(steps.map((step) => [step.id, step])), [steps]);

  // Names come from the catalogue; an id shows only when the catalogue has not arrived.
  const sourceName = (id: string) => sourceMap.get(id) ?? id;
  const variableName = (id: string) => {
    const name = variableMap.get(id)?.name;
    return name ? (name[language] ?? name.en ?? null) : null;
  };

  // The plan says what each step reads (its source and variable); steps only seen in events join after.
  const planSteps: PlanStepInfo[] = (analysisPlan?.steps ?? []).map((step) => {
    const params = (step as { params?: Record<string, unknown> }).params ?? {};
    return {
      id: step.id,
      operation: step.operation,
      type: String(step.type),
      source: (step as { sources?: string[] }).sources?.[0] ?? null,
      variable: typeof params.variable === "string" ? params.variable : null,
    };
  });
  const declared = new Set(planSteps.map((step) => step.id));
  for (const step of steps) {
    if (declared.has(step.id)) continue;
    const kind = step.operation.startsWith("read_")
      ? "ingest"
      : step.operation.startsWith("write_")
        ? "write"
        : step.operation.startsWith("review_")
          ? "review"
          : step.visualType === "document"
            ? "write"
            : "compute";
    planSteps.push({ id: step.id, operation: step.operation, type: kind, source: null, variable: null });
    declared.add(step.id);
  }
  const infoById = new Map(planSteps.map((step) => [step.id, step]));

  // What a step does, in words. A read says what it reads until its count arrives, then the catalogue
  // phrase filled with the count and the source: never «…» or a missing source.
  // A catalogue text only in the interface language: a phrase the catalogue has only in another language
  // gives way to the operation's own name, or to the kind of step. Never English on a Spanish screen.
  const own = (value: Record<string, string> | null | undefined) => value?.[language] ?? "";
  const stepShort = (info: PlanStepInfo): string =>
    own(operationMap.get(info.operation)?.name) || t(KIND_LABEL[kindOfStep(info.type, info.operation)]);
  const stepLabel = (info: PlanStepInfo, state: StepState | undefined): string => {
    const kind = kindOfStep(info.type, info.operation);
    const phrase = own(state?.phrase) || own(operationMap.get(info.operation)?.phrase);
    if (kind === "data") {
      const read = [...(state?.dataReads ?? [])].reverse().find((item) => item.nItems !== null);
      const source = read?.source ?? info.source;
      if (read && read.nItems !== null && source && phrase.includes("{n}")) {
        return phrase.replace("{n}", format(read.nItems)).replace("{source}", sourceName(source));
      }
      // A variable name reads inside the sentence in lower case («Reading air temperature»); an acronym or a
      // source name keeps its capitals.
      const variable = info.variable ? variableName(info.variable) : null;
      const inline = variable && !/^.[A-Z]/.test(variable) ? variable.charAt(0).toLowerCase() + variable.slice(1) : variable;
      const name = inline || (source ? sourceName(source) : null);
      return name ? t("run.stage.reading").replace("{name}", name) : t(KIND_LABEL.data);
    }
    return withoutHoles(phrase) || stepShort(info);
  };

  const actives = steps.filter((step) => step.started && !step.finished);
  const active = actives[actives.length - 1] ?? null;
  const planReady = analysisPlan !== null || events.length > 0;

  const nodes = sceneNodes({
    steps: planSteps,
    states: stateById,
    planReady,
    placeName: analysisPlan?.zone?.name ?? null,
    labels: { question: t("run.stage.understand"), place: t("run.stage.place") },
    sourceName: (id) => shortName(sourceName(id)),
    variableName,
    variableTone: (id) => toneOfLayer(variableMap.get(id)?.layer),
    stepLabel,
    stepShort,
    absenceText: (step) => {
      const absence = absenceOf(step);
      return absence ? text((absence.data as Record<string, unknown>).text) || null : null;
    },
  });
  const placed = placeNodes(nodes, format);
  const nodeOfStep = new Map(placed.flatMap((node) => node.steps.map((id) => [id, node.id] as const)));

  const total = planSteps.length;
  const finishedCount = planSteps.filter((step) => stateById.get(step.id)?.finished).length;
  const done = final === "finished" ? total : finishedCount;
  // With an estimate the bar weighs each step by its expected time (Iban's decision: the time is labelled as an
  // estimate below); without one it counts finished steps, as before. It never goes backwards.
  const [tick, setTick] = useState(() => Date.now());
  const now = clock ?? tick;
  const stepIds = planSteps.map((step) => step.id);
  const weighted = final === "running" ? weightedProgress(stepIds, stateById, estimate, now) : null;
  const highest = useRef(0);
  if (weighted !== null) highest.current = Math.max(highest.current, weighted);
  const percent =
    final === "finished"
      ? 100
      : weighted !== null
        ? Math.round(100 * highest.current)
        : total > 0
          ? Math.round((100 * done) / total)
          : 0;
  const estimates = estimatesFor(stepIds, estimate);
  const runStart = steps.reduce<number | null>(
    (first, step) => (step.startedAt !== null && (first === null || step.startedAt < first) ? step.startedAt : first),
    null,
  );
  const runEnd = steps.reduce<number | null>(
    (last, step) => (step.finishedAt !== null && (last === null || step.finishedAt > last) ? step.finishedAt : last),
    null,
  );

  // The key figure: the first value a calculation has saved, assertable first, in the reader's units.
  const figure = (() => {
    const values = results.filter((item) => item.type === "value" && typeof item.value === "number");
    const pick = values.find((item) => item.direction_assertable !== false) ?? values[0];
    if (!pick || typeof pick.value !== "number") return null;
    const trend = trendPresentation(pick, units, language);
    const measure = trend
      ? { text: trend.value, unit: trend.unit }
      : formatMeasure(pick.value, pick.unit, language, units, pick.method?.operation);
    return {
      name: (pick.variable && variableName(pick.variable)) || "",
      value: measure.text,
      unit: measure.unit ?? "",
    };
  })();

  const cards: AnswerCard[] = (() => {
    const of = (kind: NodeKind) => planSteps.filter((step) => kindOfStep(step.type, step.operation) === kind);
    const progress = (list: PlanStepInfo[]) => {
      const states = list.map((step) => stateById.get(step.id));
      const finished = states.filter((state) => state?.finished).length;
      const running = states.some((state) => state?.started && !state.finished);
      return { finished, running, total: list.length };
    };
    const stateOf = (p: { finished: number; running: boolean; total: number }, warn = false): CardState =>
      warn ? "warn" : p.total > 0 && p.finished === p.total ? "ready" : p.running || p.finished > 0 ? "active" : "waiting";

    const computes = of("compute");
    const computeProgress = progress(computes);
    const computeWarn = computes.some((step) => {
      const state = stateById.get(step.id);
      return Boolean(state && (state.warnings.length > 0 || state.alerts.length > 0));
    });
    const figureCard: AnswerCard = {
      key: "figure",
      label: t("run.stage.figure"),
      state: stateOf(computeProgress, computeWarn && computeProgress.finished === computeProgress.total),
      done: computeProgress.finished,
      total: computeProgress.total,
      body: figure ? (
        <span className="run-card-figure">
          <b>{figure.value}</b> {figure.unit && <span>{figure.unit}</span>}
          {figure.name && <small>{figure.name}</small>}
        </span>
      ) : computeProgress.total > 0 && computeProgress.finished === computeProgress.total ? (
        <span>{t(computeWarn ? "run.stage.figureWarn" : "run.stage.figureReady")}</span>
      ) : null,
    };

    const readSteps = of("data");
    const readProgress = progress(readSteps);
    const bySource = new Map<string, { period: { start: string; end: string } | null; count: number }>();
    for (const step of readSteps) {
      for (const read of stateById.get(step.id)?.dataReads ?? []) {
        if (!read.source) continue;
        const seen = bySource.get(read.source) ?? { period: null, count: 0 };
        bySource.set(read.source, { period: seen.period ?? read.period, count: seen.count + (read.nItems ?? 0) });
      }
    }
    const dataCard: AnswerCard = {
      key: "data",
      label: t("run.stage.data"),
      state: stateOf(readProgress),
      done: readProgress.finished,
      total: readProgress.total,
      body:
        bySource.size > 0 ? (
          <ul>
            {[...bySource].map(([id, read]) => (
              <li key={id}>
                <b>{shortName(sourceName(id), 28)}</b>
                {read.period && <span> · {periodText(read.period, language)}</span>}
                {read.count > 0 && (
                  <span className="data">
                    {" "}
                    · {format(read.count)} {itemWord(read.count)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        ) : null,
    };

    const limitLines = [
      ...new Set(
        steps.flatMap((step) => [
          ...step.warnings.map((event) => text((event.data as Record<string, unknown>).text)),
          ...step.alerts.map((event) => {
            const data = event.data as Record<string, unknown>;
            const severity = String(data.severity ?? "");
            return text(data.text) || `${t("run.alert")} · ${SEVERITY[severity] ? t(SEVERITY[severity]) : t("run.severity.unknown")}`;
          }),
        ]),
      ),
    ].filter(Boolean);
    const reviews = of("review");
    const reviewProgress = progress(reviews);
    const limitsCard: AnswerCard = {
      key: "limits",
      label: t("run.stage.limits"),
      state: limitLines.length > 0 ? "warn" : stateOf(reviewProgress),
      done: reviewProgress.finished,
      total: reviewProgress.total,
      body:
        limitLines.length > 0 ? (
          <ul>
            {limitLines.slice(0, 3).map((line) => (
              <li key={line}>{line}</li>
            ))}
            {limitLines.length > 3 && <li className="run-card-more">{t("run.stage.more").replace("{n}", String(limitLines.length - 3))}</li>}
          </ul>
        ) : reviewProgress.total > 0 && reviewProgress.finished === reviewProgress.total ? (
          <span>{t("run.stage.noWarnings")}</span>
        ) : null,
    };

    const writes = of("write");
    const writeProgress = progress(writes);
    const phraseCard: AnswerCard = {
      key: "phrase",
      label: t("run.stage.phrase"),
      state: stateOf(writeProgress),
      done: writeProgress.finished,
      total: writeProgress.total,
      body: writeProgress.total > 0 && writeProgress.finished === writeProgress.total ? <span>{t("run.stage.phraseReady")}</span> : null,
    };

    return [figureCard, dataCard, limitsCard, phraseCard];
  })();

  const sceneOpen = final !== "running" && events.length > 0;
  const [opened, setOpened] = useState(sceneOpen);
  const [rest] = useState(prefersReducedMotion);
  const [pulses, setPulses] = useState<{ seq: number; node: string }[]>([]);
  const finalRef = useRef(final);
  finalRef.current = final;
  const seen = useRef<Set<number>>(new Set());
  // When the screen opens on a run already under way (a reload, another tab), the stream first resends
  // every past event at once. That history sets each node's state without animating it; only what arrives
  // afterwards travels to Delfos.
  const historyEnds = useRef<number | null>(null);

  useEffect(() => {
    if (opened) return;
    const timer = setTimeout(() => {
      if (finalRef.current === "running") setOpened(true);
    }, Math.max(0, openIn));
    return () => clearTimeout(timer);
  }, [opened, openIn]);

  // A pulse travels from a node to Delfos for each real arrival: a read or a finished step.
  useEffect(() => {
    const arrivals = events.filter(
      (event) => (event.type === "data_read" || event.type === "step_finished") && event.step && !seen.current.has(event.seq),
    );
    for (const event of arrivals) seen.current.add(event.seq);
    if (events.length > 0 && historyEnds.current === null) historyEnds.current = Date.now() + historyWindowMs;
    if (historyEnds.current !== null && Date.now() < historyEnds.current) return;
    if (rest) {
      setPulses([]);
      return;
    }
    if (arrivals.length === 0) return;
    setPulses(arrivals.map((event) => ({ seq: event.seq, node: event.step ?? "" })));
    const timer = setTimeout(() => setPulses([]), 1000);
    return () => clearTimeout(timer);
  }, [events, rest, historyWindowMs]);

  const activeNames = [...new Set(actives.map((step) => {
    const info = infoById.get(step.id);
    return info ? stepLabel(info, step) : step.operation;
  }))];
  const nowText =
    final === "finished"
      ? t("run.stage.done")
      : final === "cancelled"
        ? t("run.stage.cancelled")
      : final === "failed"
        ? t("run.stage.failed")
        : actives.length > 1
          ? `${t("run.stage.many").replace("{n}", String(actives.length))}: ${activeNames.slice(0, 3).join(" · ")}${activeNames.length > 3 ? " …" : ""}`
          : active
            ? activeNames[0] || t("run.stage.waiting")
            : events.length > 0
              ? t("run.stage.between")
              : t("run.stage.waiting");
  const working = final === "running" && status !== "unreachable";

  // The clock behind the weighted bar and the waiting list: twice a second, once a second with reduced motion.
  useEffect(() => {
    if (clock !== undefined || !estimate || final !== "running" || events.length === 0) return;
    const timer = setInterval(() => setTick(Date.now()), prefersReducedMotion() ? 1000 : 500);
    return () => clearInterval(timer);
  }, [clock, estimate, final, events.length]);

  const tr = (key: string) => t(key as MessageKey);
  const elapsedRun = runStart === null ? 0 : Math.max(0, ((final === "running" ? now : (runEnd ?? now)) - runStart) / 1000);
  const timeLine = estimate && estimates && final !== "failed" && final !== "cancelled"
    ? timeText(estimate, elapsedRun, final === "finished", tr)
    : null;
  const kindOfId = (id: string): StepEstimate["kind"] => {
    const info = infoById.get(id);
    const kind = info ? kindOfStep(info.type, info.operation) : "compute";
    return kind === "data" ? "read" : kind === "write" || kind === "review" ? kind : "compute";
  };
  const rows = estimate ? waitingRows(stepIds, stateById, estimates, kindOfId, now) : [];
  const later = estimate ? pending(stepIds, stateById, estimates) : { count: 0, seconds: null };
  const rowName = (row: { id: string; estimate: StepEstimate | null }) => {
    const named = row.estimate?.source_name;
    if (named) return named[language] ?? named.en;
    const info = infoById.get(row.id);
    if (info?.source) return shortName(sourceName(info.source), 28);
    return info ? stepShort(info) : row.id;
  };
  const ROW_KIND: Record<StepEstimate["kind"], NodeKind> = { read: "data", compute: "compute", write: "write", review: "review" };

  const coreKey: MessageKey =
    final === "finished"
      ? "run.stage.core.answers"
      : active && kindOfStep(infoById.get(active.id)?.type ?? "", active.operation) === "write"
        ? "run.stage.core.writes"
        : active
          ? "run.stage.core.asks"
          : "run.stage.core.listens";

  const sceneAlt = `${t("run.stage.scene")}. ${done} ${t("run.stage.of")} ${total} ${stepWord(total)}. ${nowText}`;

  // The log names the kind of each step («Data read», «Calculation»), never its plan id.
  const kindOf = new Map(planSteps.map((step) => [step.id, t(KIND_LABEL[kindOfStep(step.type, step.operation)])] as const));
  const decisions = decisionsOf(analysisPlan);

  const logRows = events
    .filter((event) => !(event.type === "error" && !event.step))
    .map((event) => {
      const data = event.data as Record<string, unknown>;
      let body: ReactNode = null;
      switch (event.type) {
        case "step_started": {
          const info = event.step ? infoById.get(event.step) : undefined;
          body = <span>{info ? stepLabel(info, undefined) : String(data.operation ?? "")}</span>;
          break;
        }
        case "data_read": {
          const origin = String(data.origin ?? "");
          const count = typeof data.n_items === "number" ? data.n_items : null;
          body = (
            <>
              <span>{ORIGIN[origin] ? t(ORIGIN[origin]) : t("run.origin.unknown")}</span>
              {typeof data.source === "string" && <span>{sourceName(data.source)}</span>}
              {count !== null && (
                <span className="data">
                  {format(count)} {itemWord(count)}
                </span>
              )}
              {typeof data.mb === "number" && <span className="data">{bytes.format(data.mb)}</span>}
            </>
          );
          break;
        }
        case "progress": {
          const phase = String(data.phase ?? "");
          body = (
            <>
              {PHASE[phase] && <span>{t(PHASE[phase])}</span>}
              {data.item != null && <span>{String(data.item)}</span>}
            </>
          );
          break;
        }
        case "partial_result":
          body = <span>{t("run.stage.log.partial")}</span>;
          break;
        case "step_finished":
          body = (
            <>
              <span>{t("run.stage.log.finished")}</span>
              {typeof data.seconds === "number" && <span className="data">{seconds.format(data.seconds)}</span>}
            </>
          );
          break;
        case "warning":
          body = (
            <>
              <span>{t("run.warning")}</span>
              {text(data.text) && <span>{text(data.text)}</span>}
            </>
          );
          break;
        case "alert": {
          const severity = String(data.severity ?? "");
          body = (
            <>
              <span>{t("run.alert")}</span>
              <span>{SEVERITY[severity] ? t(SEVERITY[severity]) : t("run.severity.unknown")}</span>
            </>
          );
          break;
        }
        case "error":
          body = (
            <>
              <span>{t("run.error")}</span>
              <span>{text(data.text)}</span>
            </>
          );
          break;
        case "analysis_finished":
          body = (
            <span>
              {String(data.status) === "failed" ? t("run.stage.log.runFailed") : t("run.stage.log.runFinished")}
            </span>
          );
          break;
        default:
          break;
      }
      return (
        <li key={event.seq}>
          <time dateTime={event.ts}>{clockOf(event.ts, language)}</time>
          <span className="run-log-step">{event.step ? (kindOf.get(event.step) ?? "") : ""}</span>
          <span className="run-log-body">{body}</span>
        </li>
      );
    });

  return (
    <main className={`${PAGE} flex flex-col gap-16 py-22`}>
      <section
        aria-labelledby="run-title"
        className="flex flex-col gap-12 rounded-xl border border-line bg-paper p-16 text-ink"
      >
        <div className="flex flex-wrap items-center gap-8">
          <h1 id="run-title" className="label text-muted">
            {t("run.title")}
          </h1>
          <span className={`label inline-flex items-center rounded-xs border px-6 py-2 ${STATUS_TONE[status]}`}>
            {t(STATUS_KEY[status])}
          </span>
          <div className="run-actions ml-auto flex flex-wrap items-center gap-6">
            {final === "running" && onCancel && (
              <button
                type="button"
                onClick={onCancel}
                disabled={cancelling}
                aria-busy={cancelling}
                className="small inline-flex items-center gap-6 rounded-sm border border-line px-8 py-2 text-muted hover:border-line-strong hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:opacity-70"
              >
                {cancelling && <span className="run-spinner" aria-hidden="true" />}
                {t(cancelling ? "run.cancelling" : "run.cancel")}
              </button>
            )}
            {final === "cancelled" && onAskAgain && (
              <button
                type="button"
                onClick={onAskAgain}
                className="small rounded-sm bg-signal px-8 py-2 font-semibold text-on-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                {t("run.askAgain")}
              </button>
            )}
            {final !== "running" && final !== "cancelled" && onReplay && (
              <button
                type="button"
                onClick={onReplay}
                className="small rounded-sm border border-line px-8 py-2 text-muted hover:border-line-strong hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                {t("run.replay")}
              </button>
            )}
            {status === "finished" && (
              <>
                <Link
                  to={reportPath(analysisId)}
                  className="small rounded-sm bg-signal px-8 py-2 font-semibold text-on-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  {t("run.stage.openReport")}
                </Link>
                <Link
                  to={panelPath(analysisId)}
                  className="small rounded-sm border border-line px-8 py-2 text-muted hover:border-line-strong hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                >
                  {t("run.panel")}
                </Link>
              </>
            )}
          </div>
        </div>

        {final === "cancelled" && (
          <p role="status" className="fade-in body rounded-md border border-line-strong bg-soft p-12 text-ink">
            {t("run.stage.cancelled")}
          </p>
        )}
        {opening && (
          <p role="status" className="fade-in body font-semibold text-signal">
            {t("run.stage.opening")}
          </p>
        )}
        {events.length === 0 && status === "connecting" && <p className="body text-muted">{t("run.waiting")}</p>}
        {status === "unreachable" && <p className="body text-ink">{t("run.unreachable")}</p>}
        {general && <RunError event={general} />}
        {status === "failed" && !error && <p className="body text-ink">{t("run.failed")}</p>}

        {generalEvents.length > 0 && (
          <div className="flex flex-col gap-6">
            <h2 className="label text-muted">{t("run.general")}</h2>
            {generalEvents.map((event) =>
              event.type === "warning" ? <Warning key={event.seq} event={event} /> : <Alert key={event.seq} event={event} />,
            )}
          </div>
        )}

        <section className="run-stage" aria-label={t("run.stage.answer")}>
          <div className="run-stage-question">
            <span className="sr-only">{t("run.stage.question")}</span>
            <p className="run-stage-question-text">{analysisPlan?.request.text || t("run.stage.questionUnknown")}</p>
          </div>

          <div className="run-stage-progress">
            <p className="run-stage-now" aria-live="polite">
              {working && <span className="run-spinner" aria-hidden="true" />}
              <span>{nowText}</span>
            </p>
            <div
              className="run-stage-track"
              role="progressbar"
              aria-label={t("run.stage.planProgress")}
              aria-valuemin={0}
              aria-valuemax={Math.max(total, 1)}
              aria-valuenow={done}
            >
              {/* Until the first event the run is starting: the bar moves as on the cover, without a count. */}
              <div
                className={`run-stage-fill ${events.length === 0 && working ? "is-indeterminate" : ""}`}
                style={{ width: `${percent}%` }}
              />
            </div>
            <div className="run-stage-count">
              <b>{done}</b> {t("run.stage.of")} <b>{total}</b> {stepWord(total)}
              {timeLine && <span className="run-stage-time"> · {timeLine}</span>}
            </div>
            {active && (active.fraction !== null || active.item || active.phase) && (
              <div className="run-stage-step">
                {active.fraction !== null && (
                  <progress value={active.fraction} max={1} aria-label={t("run.progress")} />
                )}
                {active.item && <span>{active.item}</span>}
                {active.phase && <span>{PHASE[active.phase] ? t(PHASE[active.phase]) : active.phase}</span>}
              </div>
            )}
          </div>

          {decisions.length > 0 && (
            <details className="run-stage-decided">
              <summary className="small font-semibold">{t("run.stage.decided")}</summary>
              <ul>
                {decisions.map((decision, index) => (
                  <li key={`${decision.field}-${index}`} className="small">
                    {text(decision.text)}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {opened && (
            <div className={`run-stage-scene ${rest ? "run-stage-rest" : ""}`}>
              <div className="run-stage-grid">
                <div className="run-stage-net">
                  <svg viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} role="img" aria-label={sceneAlt}>
                    <defs>
                      <radialGradient id="run-core-glow">
                        <stop offset="0%" className="run-glow-in" />
                        <stop offset="100%" className="run-glow-out" />
                      </radialGradient>
                    </defs>
                    {placed.map((node) => {
                      const line = spoke(node);
                      return (
                        <line
                          key={`edge-${node.id}`}
                          className={`run-edge ${node.tone} is-${node.state}`}
                          x1={line.x1}
                          y1={line.y1}
                          x2={line.x2}
                          y2={line.y2}
                        />
                      );
                    })}
                    {!rest &&
                      pulses.map((pulse) => {
                        const node = placed.find((item) => item.id === nodeOfStep.get(pulse.node));
                        if (!node) return null;
                        const line = spoke(node);
                        return (
                          <circle key={`pulse-${pulse.seq}`} className={`run-pulse ${node.tone}`} r={4.5}>
                            <animateMotion dur="0.8s" fill="freeze" path={`M${line.x1},${line.y1} L${line.x2},${line.y2}`} />
                          </circle>
                        );
                      })}
                    <g className={`run-core ${pulses.length > 0 && !rest ? "is-receiving" : ""} ${final === "finished" ? "is-done" : ""}`} transform={`translate(${VIEW.cx} ${VIEW.cy})`}>
                      <circle className="run-core-glow" r={VIEW.core * 1.9} fill="url(#run-core-glow)" />
                      <circle className="run-core-ring" r={VIEW.core} />
                      <text className="run-core-name" y={-2}>
                        Delfos
                      </text>
                      <text className="run-core-state" y={17}>
                        {t(coreKey)}
                      </text>
                    </g>
                    {placed.map((node) => (
                      <SceneNodeMark key={node.id} node={node} format={format} />
                    ))}
                  </svg>
                  <div className="run-stage-list">
                    <p className="run-stage-list-core">
                      <b>Delfos</b> <span>{t(coreKey)}</span>
                    </p>
                    <ol aria-label={t("run.stage.scene")}>
                      {placed.map((node) => (
                        <li key={node.id} className={`${node.tone} is-${node.state}`}>
                          <span className="run-stage-dot" aria-hidden="true" />
                          <span>{node.detail}</span>
                          {node.count !== null && <span className="data">{format(node.count)}</span>}
                          {node.reason && <span className="run-stage-reason">{node.reason}</span>}
                        </li>
                      ))}
                    </ol>
                  </div>
                </div>

                {estimate && (rows.length > 0 || later.count > 0) && final === "running" && (
                  <div className="run-stage-card run-waiting" aria-live="polite">
                    <h2>{t("run.time.waiting")}</h2>
                    <ul className="run-waiting-list">
                      {rows.map((row) => (
                        <li key={row.id} className={row.done ? "is-done" : ""}>
                          <svg viewBox="-12 -12 24 24" className="run-waiting-icon" aria-hidden="true">
                            <NodeIcon kind={ROW_KIND[row.kind]} />
                          </svg>
                          <div className="run-waiting-body">
                            <span className="run-waiting-name">
                              {rowName(row)}
                              {row.done && <span className="run-waiting-check" aria-hidden="true"> ✓</span>}
                            </span>
                            {row.fraction !== null && (
                              <span className="run-waiting-track" aria-hidden="true">
                                <i style={{ width: `${Math.round(row.fraction * 100)}%` }} />
                              </span>
                            )}
                            {row.estimate && (
                              <span className="run-waiting-hint">
                                {waitingHint(row.estimate, tr, row.done ? 0 : row.elapsed)}
                              </span>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                    {later.count > 0 && (
                      <p className="run-waiting-next">
                        {(later.seconds !== null && later.seconds > 0 ? t("run.time.nextTime") : t("run.time.next"))
                          .replace("{n}", String(later.count))
                          .replace("{steps}", stepWord(later.count))
                          .replace("{about}", later.seconds !== null ? about(later.seconds, tr) : "")}
                      </p>
                    )}
                  </div>
                )}

                <div className="run-stage-card run-answer scroll-thin">
                  <h2>{t("run.stage.answer")}</h2>
                  <div className="run-stage-build">
                    {cards.map((card) => (
                      <div key={card.key} className={`run-card is-${card.state}`}>
                        <div className="run-card-head">
                          <span className="run-card-label">{card.label}</span>
                          <span className="run-card-state">
                            {t(CARD_STATE[card.state])}
                            {card.total > 1 && (
                              <span className="data">
                                {" "}
                                {card.done}/{card.total}
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="run-card-track" aria-hidden="true">
                          <i style={{ width: `${card.total > 0 ? Math.round((100 * card.done) / card.total) : 0}%` }} />
                        </div>
                        {card.body && <div className="run-card-body">{card.body}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="run-stage-card">
                <h2>{t("run.stage.logTitle")}</h2>
                <ol className="run-stage-log scroll-thin" tabIndex={0} aria-label={t("run.stage.logTitle")}>
                  {logRows}
                </ol>
              </div>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

// How long the finished run stays on screen before the report opens: enough to see the bar complete.
export const OPEN_REPORT_AFTER_MS = 1200;

export function Run({ openAfter = OPEN_REPORT_AFTER_MS }: { openAfter?: number }) {
  const { analysisId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const replay = search.get("replay") === "1";
  const speed = Number(search.get("speed") ?? (replay ? "8" : "1")) || 1;
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // The cover hands over the plan it just sent, so the question shows before the saved analysis arrives.
  const handed = (location.state as { plan?: plan.AnalysisPlan } | null)?.plan ?? null;
  const [analysisPlan, setAnalysisPlan] = useState<plan.AnalysisPlan | null>(handed);
  const [operations, setOperations] = useState<OperationInfo[]>([]);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [variables, setVariables] = useState<VariableInfo[]>([]);
  const [results, setResults] = useState<StepResult[]>([]);
  const [estimate, setEstimate] = useState<PlanEstimate | null>(null);
  const estimatedFor = useRef<string | null>(null);

  useEffect(() => {
    setEvents([]);
    setConnected(false);
    setUnreachable(false);
    return openRunEvents(
      analysisId,
      { replay, speed },
      (event) => {
        setConnected(true);
        setUnreachable(false);
        setEvents((current) => (current.some((seen) => seen.seq === event.seq) ? current : [...current, event]));
      },
      (state) => {
        setConnected(state === "open");
        setUnreachable(state === "error");
      },
    );
  }, [analysisId, replay, speed, attempt]);

  // «Cancel» stops the run on the server and goes back to the cover with the question. If the run had already
  // finished, its report opens instead. If the request fails, the cover says honestly that the run may still
  // be computing. Leaving the screen any other way does not cancel anything.
  const [cancelling, setCancelling] = useState(false);
  const [savedCancelled, setSavedCancelled] = useState(false);
  const question = analysisPlan?.request.text ?? "";
  const cancel = async () => {
    setCancelling(true);
    try {
      const answer = await cancelAnalysis(analysisId);
      if (!answer.accepted && answer.status === "finished") {
        navigate(reportPath(analysisId), { replace: true });
        return;
      }
      if (!answer.accepted && answer.status === "failed") {
        setCancelling(false);
        return;
      }
      navigate("/", { state: { question, cancelled: true } });
    } catch {
      navigate("/", { state: { question, leftRun: true } });
    }
  };
  const askAgain = () => navigate("/", { state: { question } });

  useEffect(() => {
    let live = true;
    // The plan says how many steps there are and what each reads; the catalogue names the sources and
    // the variables. Without them the events still drive the run.
    Promise.all([getAnalysis(analysisId).catch(() => null), getCatalog().catch(() => null)]).then(([saved, catalog]) => {
      if (!live) return;
      if (saved?.plan) setAnalysisPlan(saved.plan);
      if (saved?.status === "cancelled") setSavedCancelled(true);
      if (catalog) {
        setOperations(
          catalog.operations.map((operation) => ({
            id: operation.id,
            visual_type: operation.visual_type,
            phrase: operation.phrase,
            name: (operation as { name?: Record<string, string> }).name ?? null,
          })),
        );
        setSources(catalog.sources.map((source) => ({ id: source.id, name: source.name })));
        setVariables(
          (catalog.variables ?? []).map((variable) => ({
            id: variable.id,
            name: variable.name as Record<string, string | undefined>,
            layer: (variable as { layer?: string }).layer ?? null,
          })),
        );
      }
    });
    return () => {
      live = false;
    };
  }, [analysisId]);

  // The expected time of the plan, once, without holding the screen. A missing route or any failure leaves the
  // bar counting finished steps, as before the estimate existed.
  //
  // The answer is kept even if the plan object changes meanwhile: the cover hands over its plan and, a moment
  // later, the saved analysis brings the same plan again. Cancelling the request on that change (as the first
  // version did, through the effect's cleanup) threw the answer away while the guard stopped a second request,
  // so a run reached through the place question never showed its estimate. Only a change of analysis discards it.
  const currentAnalysis = useRef(analysisId);
  currentAnalysis.current = analysisId;
  useEffect(() => {
    setEstimate(null);
  }, [analysisId]);
  useEffect(() => {
    if (!analysisPlan || estimatedFor.current === analysisId) return;
    estimatedFor.current = analysisId;
    const forAnalysis = analysisId;
    const drop = (reason: unknown) => {
      // Only a later plan (the saved one) may try again; nothing changes for the person.
      if (estimatedFor.current === forAnalysis) estimatedFor.current = null;
      console.debug("[run] estimate not used:", reason);
    };
    estimatePlan(analysisPlan)
      .then((value) => {
        if (currentAnalysis.current !== forAnalysis) return;
        const total = Number(value?.total_seconds);
        if (!value || !Array.isArray(value.steps) || !Number.isFinite(total)) {
          drop("unexpected shape");
          return;
        }
        setEstimate({ ...value, total_seconds: total });
      })
      .catch(drop);
  }, [analysisId, analysisPlan]);

  // Each calculation that saves results refreshes them, so the key figure card can show its value.
  const calculated = events.filter(
    (event) =>
      event.type === "step_finished" &&
      Array.isArray((event.data as { results?: unknown }).results) &&
      ((event.data as { results: string[] }).results).some((id) => !id.endsWith("-series")),
  ).length;
  useEffect(() => {
    if (calculated === 0) return;
    let live = true;
    getAnalysisResults(analysisId)
      .then((items) => {
        if (live) setResults(items as StepResult[]);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [analysisId, calculated]);

  // A finished run opens its journalistic report by itself, after a short beat. Not when the person
  // asked to watch a replay, and never after a failure.
  const opening = !replay && attempt === 0 && finalStatus(events) === "finished" && !lastError(events);
  useEffect(() => {
    if (!opening) return;
    const timer = setTimeout(() => navigate(reportPath(analysisId), { replace: true }), openAfter);
    return () => clearTimeout(timer);
  }, [opening, analysisId, navigate, openAfter]);

  return (
    <RunView
      opening={opening}
      analysisId={analysisId}
      events={events}
      connected={connected}
      unreachable={unreachable}
      onReplay={() => setAttempt((value) => value + 1)}
      onCancel={cancel}
      cancelling={cancelling}
      cancelled={savedCancelled}
      onAskAgain={askAgain}
      analysisPlan={analysisPlan}
      operations={operations}
      sources={sources}
      variables={variables}
      results={results}
      estimate={estimate}
      openIn={replay && speed > 1 ? Math.max(300, Math.round(3000 / speed)) : 3000}
    />
  );
}
