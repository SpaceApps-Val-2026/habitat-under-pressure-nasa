// The only module that talks to the network. With VITE_API=mock it serves the contract examples,
// so the web front can work without the API front.
import type { analysis, catalog, plan, result } from "../contracts";
import { parseEvents, RUN_EVENT_TYPES, type RunEvent } from "./events";

export type Health = { status: string; version: string; catalog_version: string | null };

// The exact answer of POST /api/plans/validate (interfaces.md, section 7).
export type PlanValidation = {
  valid: boolean;
  errors: string[];
  plan: plan.AnalysisPlan;
  plan_fingerprint: string | null;
  existing_analysis: string | null;
};

// One item of GET /api/analyses (B8). What the analysis does not have arrives as null.
export type AnalysisSummary = {
  id: string;
  title: { en: string; [language: string]: string } | null;
  summary: { en: string; [language: string]: string } | null;
  design: plan.AnalysisPlan["design"];
  zone: { id?: string | null; name: string; bbox: number[] } | null;
  created: string;
  status: analysis.SavedAnalysis["status"];
  featured: boolean;
};


export function isMock(): boolean {
  return import.meta.env.VITE_API === "mock";
}

// A refusal from the server may carry a bilingual sentence written for the person (`text`), as the
// rate-limited and busy answers do. Screens show it instead of a generic failure.
export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly text?: Record<string, string>;

  constructor(status: number, code?: string, text?: Record<string, string>) {
    super(`API ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.text = text;
  }
}

export function apiMessage(error: unknown, language: string, fallback: string): string {
  if (error instanceof ApiError && error.text) return error.text[language] ?? error.text.en ?? fallback;
  return fallback;
}

async function refusal(response: Response): Promise<never> {
  const record = (await response.json().catch(() => null) ?? {}) as {
    code?: string;
    text?: unknown;
    error?: { code?: string; text?: unknown };
    detail?: { code?: string; text?: unknown };
  };
  // The API answers {"error": {"code", "text"}}; keep the older {"detail": …} shape too.
  const detail = (record.error ?? record.detail ?? record) as { code?: string; text?: unknown };
  const text =
    typeof detail.text === "string"
      ? { en: detail.text, es: detail.text }
      : (detail.text as Record<string, string> | undefined);
  throw new ApiError(response.status, detail.code, text);
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) await refusal(response);
  return (await response.json()) as T;
}

// The opening of the institutional or scientific format, written on demand by the server (the first call
// per style takes a few seconds, later ones are cached there). Remembered per analysis and style for the
// session; a failed or cancelled request is not remembered, so the next opening of the tab asks again.
const editorialMemo = new Map<string, unknown>();

export async function getAnalysisEditorial(id: string, style: string, signal?: AbortSignal): Promise<unknown> {
  const key = `${id}|${style}`;
  if (editorialMemo.has(key)) return editorialMemo.get(key);
  if (isMock()) return null;
  const response = await fetch(
    `/api/analyses/${encodeURIComponent(id)}?editorial_style=${encodeURIComponent(style)}`,
    { signal },
  );
  if (!response.ok) await refusal(response);
  const saved = (await response.json()) as analysis.SavedAnalysis;
  const piece = ((saved.story as unknown as { editorial?: Record<string, unknown> } | undefined)?.editorial ?? {})[style] ?? null;
  editorialMemo.set(key, piece);
  return piece;
}

async function sendJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) await refusal(response);
  return (await response.json()) as T;
}


export function getHealth(): Promise<Health> {
  return getJson<Health>("/health");
}

export function getCatalog(): Promise<catalog.CatalogSourcesVariablesAndOperations> {
  if (isMock()) {
    return import("../../../../docs/contratos/examples/catalog.json").then(
      // The example is validated against the schema by docs/contratos/validate.py; the cast only
      // bridges JSON literals to the generated types.
      (example) => example.default as unknown as catalog.CatalogSourcesVariablesAndOperations,
    );
  }
  return getJson("/api/catalog");
}

export function getAnalysis(id: string): Promise<analysis.SavedAnalysis> {
  if (isMock()) {
    return import("../../../../docs/contratos/examples/analysis.json").then(
      (example) => example.default as unknown as analysis.SavedAnalysis,
    );
  }
  return getJson(`/api/analyses/${encodeURIComponent(id)}`);
}

export function getAnalysisResults(id: string): Promise<result.StepResult[]> {
  if (isMock()) {
    return import("../../../../docs/contratos/examples/results.json").then(
      (example) => example.default as unknown as result.StepResult[],
    );
  }
  return getJson(`/api/analyses/${encodeURIComponent(id)}/results`);
}

export type LayerFrame = {
  t: string;
  window?: "before" | "after";
  years?: number[];
  scenario?: string;
  url?: string | null;
  result?: unknown;
  absence?: { reason: string; detail?: { en: string; [language: string]: string } };
};

export type AnalysisLayer = {
  variable: string;
  name?: { en: string; [language: string]: string };
  unit?: string;
  data_class?: string;
  scale?: string;
  range?: [number, number];
  range_percentiles?: [number, number];
  bounds?: number[];
  crs?: string;
  nodata?: number;
  windows?: { before?: [number, number]; after?: [number, number] };
  margin_km?: number;
  support?: { resolution_m?: number; since?: number; cells?: number };
  sources?: { source: string; url: string; license?: string }[];
  limits?: { en: string; [language: string]: string }[];
  absence?: { reason: string; detail?: { en: string; [language: string]: string } } | null;
  frames: LayerFrame[];
};

// The COG layers of an analysis. The route generates them on demand: the first call can take
// 10-20 s while it reads the scenes; the panel shows a loading state and never blocks the rest.
export function getAnalysisLayers(id: string): Promise<{ items: AnalysisLayer[] }> {
  if (isMock()) return Promise.resolve({ items: [] });
  return getJson(`/api/analyses/${encodeURIComponent(id)}/layers`);
}

export type LegislationItem = { celex: string; title: string; date: string; url: string };
export type Legislation = {
  items: LegislationItem[];
  language?: string;
  retrieved?: string | null;
  note?: { en: string; [language: string]: string };
  absence?: { reason: string; detail?: { en: string; [language: string]: string } };
};

export function getLegislation(variable: string, language: string): Promise<Legislation> {
  if (isMock()) return Promise.resolve({ items: [] });
  return getJson(`/api/legislation?variable=${encodeURIComponent(variable)}&language=${encodeURIComponent(language)}`);
}

export type AnalysisFilters = { q?: string; design?: string; featured?: boolean; limit?: number };

export function listAnalyses(filters: AnalysisFilters = {}): Promise<{ items: AnalysisSummary[] }> {
  if (isMock()) {
    return import("./mock/analyses.json").then(({ default: examples }) => {
      const q = (filters.q ?? "").trim().toLowerCase();
      const items = (examples as unknown as AnalysisSummary[]).filter((item) => {
        const texts = [item.title, item.summary].filter(
          (text): text is NonNullable<AnalysisSummary["title"]> => text !== null,
        );
        return (
          (!q || texts.some((text) => Object.values(text).some((value) => value.toLowerCase().includes(q)))) &&
          (!filters.design || item.design === filters.design) &&
          (!filters.featured || item.featured)
        );
      });
      return { items: filters.limit ? items.slice(0, filters.limit) : items };
    });
  }
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString();
  return getJson(`/api/analyses${suffix ? `?${suffix}` : ""}`);
}

export function validatePlan(candidate: plan.AnalysisPlan): Promise<PlanValidation> {  if (isMock()) {
    // No catalogue to normalise against and no cache to look a fingerprint up in: the mock answers
    // valid and returns the plan it was given. Field errors come from the real server.
    return Promise.resolve({
      valid: true,
      errors: [],
      plan: candidate,
      plan_fingerprint: null,
      existing_analysis: null,
    });
  }
  return sendJson<PlanValidation>("/api/plans/validate", { plan: candidate });
}

// The article Delfos writes when a question has no computable path (GET /api/questions/{id}): both languages
// of each text, so the page follows the interface language, not the one the question was asked in.
export type QuestionArticle = {
  id: string;
  question: string;
  language: string;
  headline: { en: string; [language: string]: string };
  lead: { en: string; [language: string]: string };
  cannot_say: { en: string; [language: string]: string };
  /** A place name today; a zone object (with its bbox) if the server adds one, which the page then uses. */
  zone: string | { name: string; bbox: number[]; geometry?: unknown } | null;
  sources: { name: string; url?: string }[];
  candidates: PlanCandidate[];
  clarify: boolean;
};

export function getQuestionArticle(id: string): Promise<QuestionArticle> {
  return getJson(`/api/questions/${encodeURIComponent(id)}`);
}

// Real cancellation (POST /api/analyses/{id}/cancel): 202 when the run is stopped now, 200 when it had already
// ended (then `status` says how), 404 when there is no such analysis.
export type CancelAnswer = { analysis_id: string; status: string; accepted: boolean };

export async function cancelAnalysis(id: string): Promise<CancelAnswer> {
  if (isMock()) return { analysis_id: id, status: "cancelled", accepted: true };
  const response = await fetch(`/api/analyses/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  if (!response.ok) await refusal(response);
  const body = (await response.json()) as { analysis_id: string; status: string };
  return { ...body, accepted: response.status === 202 };
}

// How long a plan is expected to take, from the measured times of each source (POST /api/plans/estimate).
// `total_seconds` is the critical path (reads run in parallel); each step carries its own estimate.
export type StepEstimate = {
  id: string;
  seconds: number;
  low_seconds: number;
  high_seconds: number;
  basis: "measured" | "prior" | "mixed";
  n: number;
  kind: "read" | "compute" | "write" | "review";
  source: string | null;
  source_name: { en: string; [language: string]: string } | null;
  expected_origin: "cold" | "cached" | "derived" | null;
};

export type PlanEstimate = {
  total_seconds: number;
  low_seconds: number;
  high_seconds: number;
  basis: "measured" | "prior" | "mixed";
  steps: StepEstimate[];
};

export function estimatePlan(candidate: plan.AnalysisPlan): Promise<PlanEstimate> {
  if (isMock()) return Promise.reject(new Error("No estimate in mock mode"));
  return sendJson<PlanEstimate>("/api/plans/estimate", { plan: candidate });
}

export function createAnalysis(candidate: plan.AnalysisPlan): Promise<{ analysis_id: string }> {
  if (isMock()) return Promise.resolve({ analysis_id: "sagunt-impact-example" });
  return sendJson<{ analysis_id: string }>("/api/analyses", { plan: candidate });
}

// C3 opens with the plan the cover (C2) sends as router state. Mock mode has no cover, so it
// falls back to the contract example; live mode has no route to fetch a plan by id.
export async function getStartingPlan(): Promise<plan.AnalysisPlan | null> {
  if (!isMock()) return null;
  const example = await import("../../../../docs/contratos/examples/plan.json");
  return example.default as unknown as plan.AnalysisPlan;
}

export type PlanReply =
  | {
      kind: "plan";
      plan: plan.AnalysisPlan;
      plan_fingerprint: string | null;
      existing_analysis: string | null;
    }
  | {
      kind: "clarification";
      question: { en: string; [language: string]: string };
      missing?: "place" | "before_after" | string;
      candidates?: PlanCandidate[];
      article_url?: string;
    }
  | { kind: "article"; article_url: string };

// What Delfos decided because the question did not say it (plan.answer.decided, contract 1.x). The
// generated types predate the field, so it is read here and nowhere else.
export type Decision = { field: string; text: { en: string; [language: string]: string } };

export function decisionsOf(candidate: plan.AnalysisPlan | null | undefined): Decision[] {
  const decided = (candidate?.answer as { decided?: unknown } | undefined)?.decided;
  return Array.isArray(decided)
    ? decided.filter((item): item is Decision => typeof item === "object" && item !== null && "text" in item)
    : [];
}

// The satellite picture of a zone (GET /api/places/image), at a given width in pixels; 404 when there is none.
export function placeImageUrl(bbox: number[], width: number): string {
  return `/api/places/image?bbox=${encodeURIComponent(bbox.join(","))}&width=${Math.round(width)}`;
}

// Where that picture comes from: the credit the provider asks to show next to it. Null when unavailable.
export type PlaceImageCredit = { credit?: Record<string, string>; license?: Record<string, string> };

export async function getPlaceImageCredit(bbox: number[]): Promise<PlaceImageCredit | null> {
  if (isMock() || bbox.length !== 4) return null;
  const info = await getJson<PlaceImageCredit & { available?: boolean }>(
    `/api/places/image/info?bbox=${encodeURIComponent(bbox.join(","))}`,
  );
  return info.available ? info : null;
}

// Where a finished analysis opens: the journalistic report.
// The data panel of an analysis, one of the tabs beside the three report formats.
export function panelPath(analysisId: string): string {
  return `/a/${encodeURIComponent(analysisId)}/panel`;
}

export function reportPath(analysisId: string): string {
  return `/a/${encodeURIComponent(analysisId)}/report/journalistic`;
}

export type PlanCandidate = {
  id: string;
  name: string;
  bbox?: number[];
  geometry?: unknown;
  source?: string;
};

export type PlanFormInput = {
  design: plan.AnalysisPlan["design"];
  place: string | Record<string, unknown>;
  variables: string[];
  period: { start: string; end: string };
  before?: [number, number];
  after?: [number, number];
  language: string;
};

// `place` is a zone the person chose (a candidate, or one /api/places/resolve found) or the words they typed.
export function createPlan(text: string, language: string, place?: PlanCandidate | string): Promise<PlanReply> {
  if (isMock()) {
    return import("../../../../docs/contratos/examples/plan.json").then(({ default: example }) => ({
      kind: "plan" as const,
      plan: example as unknown as plan.AnalysisPlan,
      plan_fingerprint: null,
      existing_analysis: null,
    }));
  }
  return sendJson<PlanReply>("/api/plans", place ? { text, language, place } : { text, language });
}

// A place name to a zone: found, ambiguous with its candidates, or not found (POST /api/places/resolve).
export type PlaceAnswer =
  | { status: "found"; zone: PlanCandidate }
  | { status: "ambiguous"; candidates: PlanCandidate[] }
  | { status: "not_found" };

export function resolvePlace(text: string, language: string): Promise<PlaceAnswer> {
  if (isMock()) return Promise.resolve({ status: "not_found" });
  return sendJson<PlaceAnswer>("/api/places/resolve", { text, language });
}

// The manual form: no assistant, no LLM. The server builds the plan from the design, place,
// variables and period, and asks for the ambiguous place or the before/after windows.
export function createPlanFromForm(input: PlanFormInput): Promise<PlanReply> {
  if (isMock()) {
    return import("../../../../docs/contratos/examples/plan.json").then(({ default: example }) => ({
      kind: "plan" as const,
      plan: example as unknown as plan.AnalysisPlan,
      plan_fingerprint: null,
      existing_analysis: null,
    }));
  }
  return sendJson<PlanReply>("/api/plans", input);
}

const EVENT_TYPES = RUN_EVENT_TYPES;

// The run stream: SSE in real mode, the saved JSONL replayed with pauses in mock mode. Reconnection
// is EventSource's own, with Last-Event-ID; the caller only dedupes by seq.
export function openRunEvents(
  analysisId: string,
  options: { replay?: boolean; speed?: number } = {},
  onEvent: (event: RunEvent) => void,
  onState?: (state: "open" | "reconnecting" | "error") => void,
): () => void {
  if (isMock()) {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    void import("../../../../docs/contratos/examples/events.jsonl?raw").then(({ default: raw }) => {
      if (cancelled) return;
      const events = parseEvents(raw);
      let index = 0;
      onState?.("open");
      const next = () => {
        if (cancelled || index >= events.length) return;
        const event = events[index];
        index += 1;
        onEvent(event);
        if (index < events.length) {
          const gap = Date.parse(events[index].ts) - Date.parse(event.ts);
          const delay = Math.min(800, Math.max(120, (Number.isFinite(gap) ? gap : 200) / (options.speed ?? 1)));
          timer = setTimeout(next, delay);
        }
      };
      next();
    });
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }
  const params = new URLSearchParams();
  if (options.replay) params.set("replay", "1");
  if (options.speed && options.speed !== 1) params.set("speed", String(options.speed));
  const query = params.toString();
  if (typeof EventSource === "undefined") {
    onState?.("error");
    return () => {};
  }
  const source = new EventSource(
    `/api/analyses/${encodeURIComponent(analysisId)}/events${query ? `?${query}` : ""}`,
  );
  const listener = (message: MessageEvent) => {
    try {
      const event = JSON.parse(message.data) as RunEvent;
      onEvent(event);
      onState?.("open");
      // The server ends the response after this event; without closing, EventSource reconnects forever.
      if (event.type === "analysis_finished" || event.type === "analysis_cancelled") source.close();
    } catch {
      // A broken frame is dropped; the stream continues.
    }
  };
  for (const type of EVENT_TYPES) source.addEventListener(type, listener as EventListener);
  source.onopen = () => onState?.("open");
  // EventSource retries by itself, sending Last-Event-ID so the server resumes after it: a dropped
  // connection is «reconnecting», only a closed source is an error.
  source.onerror = () => onState?.(source.readyState === EventSource.CLOSED ? "error" : "reconnecting");
  return () => source.close();
}

