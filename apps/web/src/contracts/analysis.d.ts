/* Generated from docs/contratos by `npm run gen:types`. Do not edit. */

/**
 * What the assistant (or the form) proposes and the person accepts before anything runs.
 */
export type AnalysisPlan = {
  contract_version: "1.0";
  id: string;
  request: {
    text: string;
    language: string;
    /**
     * Reading level
     */
    level?: "general" | "media" | "university";
    origin?: "assistant" | "form";
  };
  /**
   * Honest answer in three levels, stated before running.
   */
  answer: {
    level: "complete" | "with_limits" | "not_possible";
    limits?: Text[];
    alternative?: Text1;
  };
  design: "scan" | "state" | "trend" | "impact" | "event" | "scenario" | "exposure";
  zone: Zone;
  period: Period;
  /**
   * @minItems 1
   */
  variables: [string, ...string[]];
  comparison?: {
    type: "spatial_reference" | "baseline_period" | "region" | "threshold" | "with_and_without_project";
  };
  scenarios?: {
    horizons?: number[];
    climate?: ("ssp126" | "ssp245" | "ssp370" | "ssp585")[];
    human_pressure?: ("optimistic" | "central" | "pessimistic")[];
    industry?: ("optimistic" | "central" | "pessimistic")[];
  };
  catalog_version: string;
  /**
   * @minItems 1
   */
  steps: [
    {
      id: string;
      type: "ingest" | "compute" | "write" | "review";
      operation: string;
      version: string;
      params: {};
      depends_on: string[];
      sources?: string[];
      /**
       * Ingest steps only: where the data will come from and how much to expect.
       */
      data?: {
        /**
         * Rung of the read ladder the step will land on. derived: a result with the same fingerprint exists; cache: analysis-ready or cached file; package: pre-downloaded raw file; remote: asked from the source now
         */
        origin: "derived" | "cache" | "package" | "remote";
        estimated_seconds?: number;
        estimated_mb?: number;
      };
    },
    ...{
      id: string;
      type: "ingest" | "compute" | "write" | "review";
      operation: string;
      version: string;
      params: {};
      depends_on: string[];
      sources?: string[];
      /**
       * Ingest steps only: where the data will come from and how much to expect.
       */
      data?: {
        /**
         * Rung of the read ladder the step will land on. derived: a result with the same fingerprint exists; cache: analysis-ready or cached file; package: pre-downloaded raw file; remote: asked from the source now
         */
        origin: "derived" | "cache" | "package" | "remote";
        estimated_seconds?: number;
        estimated_mb?: number;
      };
    }[]
  ];
};

/**
 * The library object. The report (HTML, PDF and data package) is generated from it and from the results it references, and from nothing else.
 */
export interface SavedAnalysis {
  contract_version: "1.0";
  id: string;
  title: Text;
  summary?: Text;
  author?: string;
  created: string;
  status: "planned" | "running" | "finished" | "failed" | "cancelled";
  plan: AnalysisPlan;
  /**
   * Two analyses with the same fingerprint are equivalent.
   */
  fingerprint: {
    sha256: string;
    catalog_version: string;
    /**
     * Source → version, or the checksum of its manifest
     */
    data: {
      [k: string]: string;
    };
    seed?: number;
  };
  /**
   * sha256 of the canonical plan (see the README). Two wordings of one question share it: the plan cache key.
   */
  plan_fingerprint?: string;
  /**
   * Shown on the cover even when newer analyses exist
   */
  featured?: boolean;
  /**
   * Result ids
   */
  results: string[];
  findings: {
    id: string;
    order: number;
    headline?: boolean;
    text: Text2;
    /**
     * @minItems 1
     */
    results: [string, ...string[]];
    /**
     * Data class from the PRD (section 7). measured: observed by an instrument. reanalysis: output of a physical model fed with observations, with no projection into the future. extrapolated: an observed trend carried forward. approximated: estimated with archetypes or indirect indicators. illustrative: an example with no data value. The interface maps it to its label.
     */
    data_class: "measured" | "reanalysis" | "extrapolated" | "approximated" | "illustrative";
    /**
     * Level the sentence claims; never above the highest level of its results
     */
    evidence_level?: "E0" | "E1" | "E2" | "E3" | "E4" | "E5";
    review: {
      status: "pending" | "approved" | "rejected";
      /**
       * reviewer agent or person
       */
      by?: string;
      reason?: string;
    };
  }[];
  alerts: {
    rule: string;
    severity: "info" | "attention" | "critical";
    result: string;
    text: Text;
  }[];
  /**
   * The report as ordered blocks, one list per style. Built by the composer from the plan design and the results that exist.
   */
  story?: {
    [k: string]: {
      /**
       * Id of a catalog block
       */
      block: string;
      results?: string[];
      /**
       * Id of the finding this block shows
       */
      finding?: string;
      /**
       * Id of a catalog norm
       */
      norm?: string;
      text?: Text3;
    }[];
  };
  reports: {
    format: "html" | "pdf" | "data";
    /**
     * The three report formats built from one analysis.
     */
    style?: "journalistic" | "institutional" | "scientific";
    language: string;
    path: string;
  }[];
}
/**
 * Text by language code. English is required; other languages are optional.
 */
export interface Text {
  [k: string]: string;
}
/**
 * Text by language code. English is required; other languages are optional.
 */
export interface Text1 {
  [k: string]: string;
}
export interface Zone {
  id?: string;
  name: string;
  /**
   * west, south, east, north in EPSG:4326
   *
   * @minItems 4
   * @maxItems 4
   */
  bbox: [number, number, number, number];
  /**
   * GeoJSON in EPSG:4326
   */
  geometry?: {};
  source?: string;
}
export interface Period {
  start: string;
  end: string;
  months?: number[];
}
/**
 * Text by language code. English is required; other languages are optional.
 */
export interface Text2 {
  [k: string]: string;
}
/**
 * Text by language code. English is required; other languages are optional.
 */
export interface Text3 {
  [k: string]: string;
}
