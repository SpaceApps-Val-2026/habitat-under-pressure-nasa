/* Generated from docs/contratos by `npm run gen:types`. Do not edit. */

/**
 * The only source of figures for the panel, the report and the assistant. It carries data or an absence, never both.
 */
export type StepResult = {
  id: string;
  analysis: string;
  step: string;
  type: "value" | "series" | "layer" | "table" | "document";
  variable: string;
  zone?: string;
  period?: Period;
  /**
   * Data class from the PRD (section 7). measured: observed by an instrument. reanalysis: output of a physical model fed with observations, with no projection into the future. extrapolated: an observed trend carried forward. approximated: estimated with archetypes or indirect indicators. illustrative: an example with no data value. The interface maps it to its label.
   */
  data_class: "measured" | "reanalysis" | "extrapolated" | "approximated" | "illustrative";
  unit?: string;
  value?: number;
  interval?: {
    /**
     * 0.95 means 95 %
     */
    level: number;
    low: number;
    high: number;
    method?: string;
  };
  /**
   * false when the interval includes both improving and worsening: no central figure is given
   */
  direction_assertable?: boolean;
  statistic?: {
    name: string;
    value?: number;
    p?: number;
    p_adjusted?: number;
    n?: number;
  };
  series?: {
    t: string;
    v?: number | null;
    low?: number;
    high?: number;
    /**
     * Data class from the PRD (section 7). measured: observed by an instrument. reanalysis: output of a physical model fed with observations, with no projection into the future. extrapolated: an observed trend carried forward. approximated: estimated with archetypes or indirect indicators. illustrative: an example with no data value. The interface maps it to its label.
     */
    data_class?: "measured" | "reanalysis" | "extrapolated" | "approximated" | "illustrative";
    scenario?: string;
    /**
     * Climate model or ensemble member the value comes from
     */
    model?: string;
  }[];
  layer?: {
    path: string;
    format: "cog" | "pmtiles" | "geojson" | "png";
    /**
     * @minItems 2
     * @maxItems 2
     */
    range?: [number, number];
    /**
     * Name of the design-system scale
     */
    scale?: string;
  };
  table?: {
    columns: unknown[];
    rows: unknown[];
  };
  document?: {
    /**
     * Literal quote
     */
    quote: string;
  };
  absence?: Absence;
  coverage?: {
    valid_pct?: number;
    n_dates?: number;
  };
  /**
   * @minItems 1
   */
  sources: [SourceRef, ...SourceRef[]];
  method: {
    operation: string;
    version: string;
    params?: {};
  };
  limits?: Text[];
  /**
   * Sets the verbs a sentence may use. Never above the level of the operation; the statistical reviewer can lower it.
   */
  evidence_level?: "E0" | "E1" | "E2" | "E3" | "E4" | "E5";
  support?: Support;
  /**
   * What the statistical reviewer reads: tests run, correction applied, autocorrelation, pre-trend in the control...
   */
  diagnostics?: {
    [k: string]: number | boolean | string;
  };
  /**
   * sha256 of operation, version, params and input fingerprints. Same fingerprint, same result: the step cache key.
   */
  fingerprint?: string;
} & {
  [k: string]: unknown;
};

export interface Period {
  start: string;
  end: string;
  months?: number[];
}
/**
 * Missing data carries a reason. Never zero and never an invented value.
 */
export interface Absence {
  reason: "no_coverage" | "insufficient_qa" | "source_unreachable" | "needs_package" | "not_computed";
  detail?: Text;
}
/**
 * Text by language code. English is required; other languages are optional.
 */
export interface Text {
  [k: string]: string;
}
/**
 * Where a figure comes from, with what is needed to open it in one click.
 */
export interface SourceRef {
  source: string;
  product?: string;
  version?: string;
  /**
   * Specific scenes, granules or documents
   */
  items?: string[];
  n_items?: number;
  page?: string;
  url: string;
  license?: string;
}
/**
 * Real size of what was measured. Grids are never mixed without resampling to the coarsest one.
 */
export interface Support {
  resolution_m?: number;
  /**
   * First year with data
   */
  since?: number;
  revisit_days?: number;
  /**
   * Grid cells inside the zone; 1 or fewer means the cell is larger than the zone
   */
  cells?: number;
}
