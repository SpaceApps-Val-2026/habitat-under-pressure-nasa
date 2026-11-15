import type { RunEvent } from "../api/events";

export type DataRead = {
  source: string | null;
  origin: string;
  nItems: number | null;
  mb?: number;
  period: { start: string; end: string } | null;
};

export type StepState = {
  id: string;
  operation: string;
  phrase: Record<string, string> | null;
  visualType: string;
  started: boolean;
  finished: boolean;
  seconds: number | null;
  /** When the step started and finished, from the events' own timestamps (ms), so a reload keeps them. */
  startedAt: number | null;
  finishedAt: number | null;
  fraction: number | null;
  item: string | null;
  phase: string | null;
  results: string[];
  dataReads: DataRead[];
  warnings: RunEvent[];
  alerts: RunEvent[];
  error: RunEvent | null;
};

function text(value: unknown): Record<string, string> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, string>) : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function periodOf(value: unknown): { start: string; end: string } | null {
  if (typeof value !== "object" || value === null) return null;
  const period = value as Record<string, unknown>;
  if (typeof period.start !== "string" || typeof period.end !== "string") return null;
  return { start: period.start, end: period.end };
}

function timeOf(ts: string | undefined): number | null {
  const value = ts ? Date.parse(ts) : Number.NaN;
  return Number.isFinite(value) ? value : null;
}

export function stepsOf(events: RunEvent[]): StepState[] {
  const steps = new Map<string, StepState>();
  const step = (id: string) => {
    let current = steps.get(id);
    if (!current) {
      current = {
        id,
        operation: "",
        phrase: null,
        visualType: "",
        started: false,
        finished: false,
        seconds: null,
        startedAt: null,
        finishedAt: null,
        fraction: null,
        item: null,
        phase: null,
        results: [],
        dataReads: [],
        warnings: [],
        alerts: [],
        error: null,
      };
      steps.set(id, current);
    }
    return current;
  };

  for (const event of [...new Map(events.map((item) => [item.seq, item])).values()].sort((a, b) => a.seq - b.seq)) {
    if (!event.step) continue;
    const data = event.data as Record<string, unknown>;
    const current = step(event.step);
    switch (event.type) {
      case "step_started":
        current.started = true;
        current.startedAt = timeOf(event.ts) ?? current.startedAt;
        current.operation = String(data.operation ?? current.operation);
        current.visualType = String(data.visual_type ?? current.visualType);
        current.phrase = text(data.phrase) ?? current.phrase;
        break;
      case "data_read":
        current.dataReads.push({
          source: data.source ? String(data.source) : null,
          origin: String(data.origin ?? ""),
          nItems: number(data.n_items),
          mb: number(data.mb) ?? undefined,
          period: periodOf(data.period),
        });
        break;
      case "progress":
        current.fraction = number(data.fraction);
        current.item = data.item ? String(data.item) : null;
        current.phase = data.phase ? String(data.phase) : null;
        break;
      case "partial_result": {
        const result = String(data.result ?? "");
        if (result && !current.results.includes(result)) current.results.push(result);
        break;
      }
      case "step_finished":
        current.finished = true;
        current.finishedAt = timeOf(event.ts) ?? current.finishedAt;
        current.seconds = number(data.seconds);
        for (const result of (data.results as string[]) ?? []) {
          if (!current.results.includes(result)) current.results.push(result);
        }
        break;
      case "warning":
        current.warnings.push(event);
        break;
      case "alert":
        current.alerts.push(event);
        break;
      case "error":
        current.error = event;
        break;
    }
  }
  return [...steps.values()];
}

export function finalStatus(events: RunEvent[]): "running" | "finished" | "failed" | "cancelled" {
  // A run cancelled from this or another tab ends with its own event.
  if (events.some((event) => event.type === "analysis_cancelled")) return "cancelled";
  const finished = [...events].reverse().find((event) => event.type === "analysis_finished");
  if (!finished) return "running";
  return (finished.data as Record<string, unknown>).status === "failed" ? "failed" : "finished";
}

export function lastError(events: RunEvent[]): RunEvent | null {
  return [...events].reverse().find((event) => event.type === "error") ?? null;
}

// Errors that belong to the whole run, not to a step: those are shown once, above the log.
export function generalError(events: RunEvent[]): RunEvent | null {
  return [...events].reverse().find((event) => event.type === "error" && !event.step) ?? null;
}

// The counter of an informant: the last count the executor reported for the step.
export function countOf(step: StepState): number | null {
  for (let index = step.dataReads.length - 1; index >= 0; index -= 1) {
    const items = step.dataReads[index].nItems;
    if (items !== null) return items;
  }
  return null;
}

// A source that did not answer leaves the step without data, never with a zero: the executor says
// why in a warning. The node stays off and the reason is the warning text.
const ABSENCE_CODES = new Set(["needs_package", "source_unreachable", "no_coverage", "insufficient_qa"]);

export function absenceOf(step: StepState): RunEvent | null {
  return (
    step.warnings.find((warning) => {
      const code = (warning.data as Record<string, unknown>).code;
      return typeof code === "string" && ABSENCE_CODES.has(code);
    }) ?? null
  );
}
