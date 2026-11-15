/* Generated from docs/contratos by `npm run gen:types`. Do not edit. */

/**
 * One line per cached file (.jsonl). Pre-download and on-demand ingest write it the same way.
 */
export interface CacheManifestEntry {
  source: string;
  product?: string;
  version?: string;
  /**
   * Original id of the scene, granule, tile or document
   */
  item: string;
  /**
   * global, or the zone id
   */
  scope: string;
  /**
   * @minItems 4
   * @maxItems 4
   */
  bbox?: [number, number, number, number];
  period?: Period;
  /**
   * Relative to the data root
   */
  path: string;
  mb: number;
  sha256: string;
  format:
    | "cog"
    | "netcdf"
    | "hdf5"
    | "zarr"
    | "geoparquet"
    | "geojson"
    | "pmtiles"
    | "sqlite"
    | "png"
    | "pdf"
    | "csv"
    | "zip";
  /**
   * Store tier: raw as downloaded; ready for analysis (cube, COG, GeoParquet); derived and addressed by fingerprint
   */
  tier?: "raw" | "ready" | "derived";
  /**
   * Catalog variables this file holds; feeds the coverage index
   */
  variables?: string[];
  ingest: {
    date: string;
    origin: "predownload" | "on_demand";
    url: string;
    by?: string;
  };
  quality?: {
    cloud_pct?: number;
    valid_pct?: number;
  };
  license: string;
}
export interface Period {
  start: string;
  end: string;
  months?: number[];
}
