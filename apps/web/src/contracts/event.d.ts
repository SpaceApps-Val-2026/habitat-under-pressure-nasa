/* Generated from docs/contratos by `npm run gen:types`. Do not edit. */

/**
 * One line per event (Server-Sent Events, and the .jsonl file saved with the analysis).
 */
export type RunEvent = {
  analysis: string;
  /**
   * Absent only in analysis_finished and in general errors
   */
  step?: string;
  /**
   * Strict order within the analysis, starting at 1
   */
  seq: number;
  type:
    | "step_started"
    | "data_read"
    | "progress"
    | "partial_result"
    | "warning"
    | "alert"
    | "step_finished"
    | "error"
    | "analysis_finished"
    | "analysis_cancelled";
  ts: string;
  data: {};
};
