/* Generated from docs/contratos by `npm run gen:types`. Do not edit. */

export type Celex = string;
export type Who = "anyone" | "interested" | "affected" | "ngo";
export type Link = string;

/**
 * Everything that can be asked for. A versioned file: the assistant and the executor read the same one.
 */
export interface CatalogSourcesVariablesAndOperations {
  version: string;
  sources: {
    id: string;
    name: string;
    provider: string;
    product?: string;
    version?: string;
    resolution?: string;
    since?: string;
    access: {
      /**
       * api: a subset is requested on the spot; package: the file must already be downloaded; manual: a person downloads it
       */
      mode: "api" | "package" | "manual";
      account: "none" | "earthdata" | "copernicus" | "api_key" | "registration";
      /**
       * What our cache holds: the whole world, only the prepared cases, or whatever gets requested
       */
      coverage: "global" | "cases" | "on_demand";
      url?: string;
    };
    /**
     * Licence and its conditions. A plain string, or text by language (en required). Shown as written.
     */
    license: string | Text;
    /**
     * Attribution line the licence asks for, shown next to the data. A plain string, or text by language (en required). Literal footers keep the holder's language in every version. Bracketed markers ([year], [date], [fecha], [AOI]...) are filled by the code that shows it.
     */
    citation: string | Text;
    /**
     * May we serve the data as is from our server?
     */
    redistributable: boolean;
  }[];
  variables: {
    id: string;
    name: Text;
    unit: string;
    /**
     * Class of the values this variable delivers. A reading whose operation says inherit copies it. Absent means measured.
     */
    data_class?: "measured" | "reanalysis" | "extrapolated" | "approximated" | "illustrative";
    layer?: "heat" | "green" | "water" | "sea" | "energy" | "land" | "population" | "climate" | "risk";
    /**
     * @minItems 1
     */
    sources: [string, ...string[]];
    limit?: Text1;
    /**
     * Everyday words this variable answers to; read by the assistant
     */
    answers?: string[];
    /**
     * Text by language code. English is required; other languages are optional.
     */
    not?: {
      [k: string]: string;
    };
    support?: Support;
    /**
     * Link of the frame that joins the sources: driver, pressure, state, exposure, response.
     */
    frame?: "driver" | "pressure" | "state" | "exposure" | "response";
    /**
     * Study designs this variable supports
     */
    designs?: ("scan" | "state" | "trend" | "impact" | "event" | "scenario" | "exposure")[];
    /**
     * For a projected variable: the observed variable that gives its reference period, and how its values combine into a year. Without it, a scenario shows the absence of an observed reference.
     */
    baseline?: {
      variable: string;
      statistic?: "mean" | "sum" | "median";
    };
    /**
     * EuroVoc concept ids (http://eurovoc.europa.eu/<id>) this variable relates to; used to list related legislation from official APIs
     */
    eurovoc?: string[];
  }[];
  operations: {
    id: string;
    version: string;
    type: "ingest" | "compute" | "write" | "review";
    visual_type: "zone" | "satellite" | "compute" | "document";
    phrase: Text;
    /**
     * JSON Schema of the step params
     */
    params: {};
    output: {
      type: "value" | "series" | "layer" | "table" | "document";
      /**
       * A class, or inherit. A reading (ingest) inherits the class of its variable; any other operation inherits the least direct class of its inputs, in the order measured, reanalysis, extrapolated, approximated, illustrative.
       */
      data_class: ("measured" | "reanalysis" | "extrapolated" | "approximated" | "illustrative") | "inherit";
      unit?: string;
    };
    limits?: Text[];
    /**
     * Highest level a result of this operation can claim
     */
    evidence_level?: "E0" | "E1" | "E2" | "E3" | "E4" | "E5";
    /**
     * Names of the diagnostics this operation writes into its results
     */
    diagnostics?: string[];
  }[];
  /**
   * Mechanisms declared before looking at the data. evaluate_chain checks each link.
   */
  chains?: {
    id: string;
    name?: Text;
    /**
     * @minItems 1
     */
    links: [
      {
        from: string;
        to: string;
        sign: "+" | "-";
        /**
         * Shortest and longest expected lag
         *
         * @minItems 2
         * @maxItems 2
         */
        lag_years?: [number, number];
        /**
         * Published reference for the link; null until it has one, and then the link is not evaluated
         */
        ref: string | null;
      },
      ...{
        from: string;
        to: string;
        sign: "+" | "-";
        /**
         * Shortest and longest expected lag
         *
         * @minItems 2
         * @maxItems 2
         */
        lag_years?: [number, number];
        /**
         * Published reference for the link; null until it has one, and then the link is not evaluated
         */
        ref: string | null;
      }[]
    ];
  }[];
  /**
   * Curated table of rules. The code inserts the literal quote; the LLM never rewrites it.
   */
  norms?: {
    id: string;
    title: string;
    scope?: {
      type: "site" | "municipality" | "basin" | "region" | "country" | "eu" | "global";
      id?: string;
    };
    /**
     * @minItems 1
     */
    applies_to: [string, ...string[]];
    clause: {
      /**
       * Id of a catalog source
       */
      document: string;
      article?: string;
      page?: string;
      /**
       * Literal quote, in the language of the document; null until a person has copied it
       */
      quote: string | null;
    };
    threshold?: {
      kind: "limit" | "target" | "requirement";
      value?: number | null;
      unit?: string;
    };
    /**
     * Person who checked the quote against the document; null means not reviewed and not shown
     */
    reviewed_by: string | null;
  }[];
  /**
   * Unit consumptions with a range, for resource_estimate. A missing figure is null, never a guess.
   */
  archetypes?: {
    id: string;
    name: Text;
    resource: "water" | "energy" | "land";
    unit: string;
    /**
     * What one unit refers to: inhabitant, GWh of capacity, hectare...
     */
    per: string;
    low: number | null;
    central?: number | null;
    high: number | null;
    /**
     * Published reference; null until it has one
     */
    ref: string | null;
    /**
     * Limits of the coefficients and their use, carried into the estimate.
     */
    limits?: Text[];
    /**
     * Optional. Typical size of one installation, counted in the `per` unit, with its own published reference. With it, whatif answers for a typical installation; without it, per unit of `per`. Never a figure the person types.
     */
    typical?: {
      low: number | null;
      central?: number | null;
      high: number | null;
      /**
       * Published reference of the size; null until it has one
       */
      ref: string | null;
    } | null;
  }[];
  /**
   * Report blocks. The composer can only pick a block whose needs are met by existing results.
   */
  blocks?: {
    /**
     * Component name
     */
    id: string;
    /**
     * Empty means always available
     */
    needs: {
      result_types?: ("value" | "series" | "layer" | "table" | "document")[];
      /**
       * Result fields that must be present
       */
      fields?: string[];
      operations?: string[];
      min_results?: number;
    };
    /**
     * @minItems 1
     *
     * Items: The three report formats built from one analysis.
     */
    styles: ["journalistic" | "institutional" | "scientific", ...("journalistic" | "institutional" | "scientific")[]];
  }[];
  /**
   * Plain-language cards for EU acts, the citizen routes the law gives, the disclaimer, the visibility of acts without a card and the 'Coming soon' text. Written and reviewed by the legal front; the code shows it as written and never judges compliance.
   */
  legislation?: {
    /**
     * Label shown next to each route, by who can use it
     */
    who_labels: {
      [k: string]: Bilingual;
    };
    disclaimer: Bilingual1;
    acts: {
      celex: Celex;
      /**
       * Other CELEX that show this card (an amending act Cellar lists instead of the act); may repeat across cards
       */
      also_for?: Celex[];
      short_name: Bilingual;
      plain: Bilingual2;
      in_spain: Bilingual3;
      citizen_options: Route[];
      /**
       * Ids of general_options that also apply
       */
      general: string[];
      sources: Link[];
      /**
       * Person who checked the card against the official texts; null means not shown (the act falls back to its title)
       */
      reviewed_by: string | null;
    }[];
    /**
     * Routes that apply to any law, shown once
     */
    general_options: (Route1 & {
      id: string;
    })[];
    /**
     * Acts Cellar lists that have no card. Anything not listed shows as title; a CELEX in also_for takes its card's visibility.
     */
    visibility?: {
      title?: {
        celex: Celex;
        /**
         * Acts this title is listed under: a card, or another title
         */
        under?: Celex[];
      }[];
      hidden?: Celex[];
    };
    coming_soon?: Bilingual5;
  };
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
/**
 * Text in Spanish and English, both required; other languages optional
 */
export interface Bilingual {
  [k: string]: string;
}
/**
 * Text in Spanish and English, both required; other languages optional
 */
export interface Bilingual1 {
  [k: string]: string;
}
/**
 * Text in Spanish and English, both required; other languages optional
 */
export interface Bilingual2 {
  [k: string]: string;
}
/**
 * Text in Spanish and English, both required; other languages optional
 */
export interface Bilingual3 {
  [k: string]: string;
}
/**
 * Something a person can do under the law, who can do it and where
 */
export interface Route {
  action: Bilingual;
  how: Bilingual;
  who: Who;
  status?: Bilingual4;
  legal_basis: string;
  url: Link;
}
/**
 * Text in Spanish and English, both required; other languages optional
 */
export interface Bilingual4 {
  [k: string]: string;
}
/**
 * Something a person can do under the law, who can do it and where
 */
export interface Route1 {
  action: Bilingual;
  how: Bilingual;
  who: Who;
  status?: Bilingual4;
  legal_basis: string;
  url: Link;
}
/**
 * Text in Spanish and English, both required; other languages optional
 */
export interface Bilingual5 {
  [k: string]: string;
}
