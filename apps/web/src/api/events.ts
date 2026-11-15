import type { event } from "../contracts";

export type RunEvent = event.RunEvent;

// The event names of the contract (event.schema.json, `type`).
export const RUN_EVENT_TYPES = [
  "step_started",
  "data_read",
  "progress",
  "partial_result",
  "warning",
  "alert",
  "step_finished",
  "error",
  "analysis_finished",
  "analysis_cancelled",
] as const;

// The JSONL of events saved with an analysis, one event per line.
export function parseEvents(text: string): RunEvent[] {
  const bySeq = new Map<number, RunEvent>();
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const parsed = JSON.parse(trimmed) as RunEvent;
      if (typeof parsed.seq === "number") bySeq.set(parsed.seq, parsed);
    } catch {
      // A broken line is skipped; the rest of the run still reads.
    }
  }
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}
