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
