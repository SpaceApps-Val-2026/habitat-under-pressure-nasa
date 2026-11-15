// The run's expected time, from the server's estimate (measured times of each source). Pure functions:
// the bar's weighted progress, the time text and the list of what is being waited for.
import type { PlanEstimate, StepEstimate } from "../api/client";
import type { StepState } from "./run-model";

// A running step never counts more than this share of its estimate: only its step_finished completes it.
export const RUNNING_CAP = 0.95;
// The bar never claims the run is complete before the run says so.
export const BAR_CAP = 0.97;

function elapsedOf(step: StepState, now: number): number {
  if (step.startedAt === null) return 0;
  return Math.max(0, ((step.finishedAt ?? now) - step.startedAt) / 1000);
}

// Each plan step's estimate; a step the server did not estimate takes the mean of the others. Null when the
// estimate is missing or says nothing usable, so the screen falls back to counting finished steps.
export function estimatesFor(stepIds: string[], estimate: PlanEstimate | null): Map<string, StepEstimate> | null {
  if (!estimate || !Array.isArray(estimate.steps) || stepIds.length === 0) return null;
  const known = new Map(
    estimate.steps.filter((step) => Number.isFinite(step.seconds) && step.seconds >= 0).map((step) => [step.id, step]),
  );
  const values = stepIds.map((id) => known.get(id)).filter((step): step is StepEstimate => Boolean(step));
  if (values.length === 0) return null;
  const mean = values.reduce((sum, step) => sum + step.seconds, 0) / values.length;
  const all = new Map<string, StepEstimate>();
  for (const id of stepIds) {
    all.set(
      id,
      known.get(id) ?? {
        id,
        seconds: mean,
        low_seconds: mean,
        high_seconds: mean,
        basis: "prior",
        n: 0,
        kind: "compute",
        source: null,
        source_name: null,
        expected_origin: null,
      },
    );
  }
  return all;
}

// Progress weighted by expected work: a finished step counts its whole estimate, a running one its elapsed
// time up to 95 % of its estimate, a pending one nothing. Capped at 97 % until the run finishes. Null without
// a usable estimate (the caller then counts finished steps).
export function weightedProgress(
  stepIds: string[],
  states: Map<string, StepState>,
  estimate: PlanEstimate | null,
  now: number,
): number | null {
  const estimates = estimatesFor(stepIds, estimate);
  if (!estimates) return null;
  let total = 0;
  let counted = 0;
  for (const id of stepIds) {
    const seconds = estimates.get(id)?.seconds ?? 0;
    total += seconds;
    const state = states.get(id);
    if (!state?.started) continue;
    counted += state.finished ? seconds : Math.min(elapsedOf(state, now), RUNNING_CAP * seconds);
  }
  if (total <= 0) return null;
  return Math.min(BAR_CAP, counted / total);
}

type Translate = (key: string) => string;

// «40 s», «1 min 30 s», «12 min»: seconds under a minute to the nearest 5 (at least 5), then to the nearest
// 10 s, and whole minutes from ten minutes up.
export function duration(seconds: number): string {
  const value = Math.max(0, seconds);
  if (value < 57.5) return `${Math.max(5, Math.round(value / 5) * 5)} s`;
  if (value >= 600) return `${Math.round(value / 60)} min`;
  const rounded = Math.round(value / 10) * 10;
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}

export function about(seconds: number, t: Translate): string {
  if (seconds < 1) return t("run.time.underSecond");
  return t("run.time.about").replace("{n}", duration(seconds));
}

// The line under the bar. A range when low and high differ a lot; «initial estimate» when nothing has been
// measured yet; «taking longer than usual» past the high estimate. The remaining time never reaches zero
// while the run is still going.
export function timeText(estimate: PlanEstimate, elapsedSeconds: number, finished: boolean, t: Translate): string {
  const prefix = t(estimate.basis === "prior" ? "run.time.initial" : "run.time.estimate");
  const wide = estimate.high_seconds > 2 * Math.max(estimate.low_seconds, 1);
  const whole = wide
    ? t("run.time.range").replace("{low}", duration(estimate.low_seconds)).replace("{high}", duration(estimate.high_seconds))
    : about(estimate.total_seconds, t);
  const head = `${prefix} ${whole}`;
  if (finished) return head;
  if (elapsedSeconds > estimate.high_seconds) return `${head} · ${t("run.time.slower")}`;
  const left = estimate.total_seconds - elapsedSeconds;
  if (left < 5) return `${head} · ${t("run.time.almost")}`;
  return `${head} · ${t("run.time.left").replace("{about}", about(left, t))}`;
}

export type WaitingRow = {
  id: string;
  kind: StepEstimate["kind"];
  elapsed: number;
  estimate: StepEstimate | null;
  /** 0..1 of its estimate, capped at 1 (a step slower than usual stays full until it finishes). */
  fraction: number | null;
  done: boolean;
};

// What is being waited for now: every running step, from its step_started to its step_finished, plus the
// steps that finished in the last moment (shown briefly with a check).
export function waitingRows(
  stepIds: string[],
  states: Map<string, StepState>,
  estimates: Map<string, StepEstimate> | null,
  kindOf: (id: string) => StepEstimate["kind"],
  now: number,
  linger = 1500,
): WaitingRow[] {
  const rows: WaitingRow[] = [];
  for (const id of stepIds) {
    const state = states.get(id);
    if (!state?.started) continue;
    const recent = state.finished && state.finishedAt !== null && now - state.finishedAt < linger;
    if (state.finished && !recent) continue;
    const estimate = estimates?.get(id) ?? null;
    const elapsed = elapsedOf(state, now);
    rows.push({
      id,
      kind: estimate?.kind ?? kindOf(id),
      elapsed,
      estimate,
      fraction: state.finished ? 1 : estimate && estimate.seconds > 0 ? Math.min(1, elapsed / estimate.seconds) : null,
      done: state.finished,
    });
  }
  return rows;
}

// The hint under a waiting row: what this source usually takes, said for the case at hand. Once the step has
// run past its high estimate (3 s for one expected under a second), the forecast no longer holds and the hint
// says only that: no «less than 1 s» on a read that has been waiting for a minute, and no guessed reason.
export function waitingHint(estimate: StepEstimate, t: Translate, elapsed = 0): string {
  const limit = estimate.seconds < 1 ? 3 : Math.max(estimate.high_seconds, estimate.seconds);
  if (elapsed > limit) return t("run.time.overdue");
  if (estimate.expected_origin === "cached" || estimate.expected_origin === "derived") {
    return t("run.time.cached").replace("{about}", about(estimate.seconds, t));
  }
  if (estimate.expected_origin === "cold" && estimate.seconds >= 30) {
    return t("run.time.cold").replace("{about}", about(estimate.seconds, t));
  }
  return t("run.time.usual").replace("{about}", about(estimate.seconds, t));
}

// The steps not started yet and their summed estimate, for the «Next» line.
export function pending(
  stepIds: string[],
  states: Map<string, StepState>,
  estimates: Map<string, StepEstimate> | null,
): { count: number; seconds: number | null } {
  const ids = stepIds.filter((id) => !states.get(id)?.started);
  const seconds = estimates ? ids.reduce((sum, id) => sum + (estimates.get(id)?.seconds ?? 0), 0) : null;
  return { count: ids.length, seconds };
}
