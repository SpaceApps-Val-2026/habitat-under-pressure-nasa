import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "maplibre-gl-raster/style.css";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useParams } from "react-router-dom";

import {
  getAnalysis,
  getAnalysisLayers,
  getAnalysisResults,
  getCatalog,
  type AnalysisLayer,
  type LayerFrame,
} from "../api/client";
import { CatalogProvider, useCatalog, useVariableName } from "../blocks/catalog";
import {
  DataClassTag,
  VARIABLES,
  convertValue,
  formatMeasure,
  localized,
  resolutionText,
  sourceDisplayName,
  unitOf,
} from "../blocks/shared";
import { KeyFigure } from "../blocks/KeyFigure";
import { ScenarioFan } from "../blocks/ScenarioFan";
import { ScanMatrix, hasScanMatrix } from "../blocks/ScanMatrix";
import { TrendChart } from "../blocks/TrendChart";
import { UncertaintySplit, hasVarianceSplit } from "../blocks/UncertaintySplit";
import type { analysis, result } from "../contracts";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { useUnits, type Units } from "../units";
import { fallbackOpening } from "../blocks/opening";
import { AnalysisTabs } from "./AnalysisTabs";
import { PAGE } from "../layout/page";
import "./panel-map.css";
import { DataClassMark } from "../blocks/DataClassMark";

type SavedAnalysis = analysis.SavedAnalysis;
type StepResult = result.StepResult;
type Zone = SavedAnalysis["plan"]["zone"];

type LegendTick = { value: number; label: string; position: number };
type Legend = { title: string; ticks: LegendTick[] };

type RasterLayer = {
  url: string;
  range: [number, number];
  colormap: string;
  reversed?: boolean;
  legend: Legend;
};

// The two windows of one layer, drawn as a curtain over the same map view.
type CompareRasters = {
  before: RasterLayer;
  after: RasterLayer;
  labels: { before: string; after: string };
  onFailed: () => void;
};

type RasterControlType = import("maplibre-gl-raster").RasterControl;
type ColorbarType = import("maplibre-gl-raster").Colorbar;

// One MapLibre instance and the raster control it holds, so each pane can be torn down on its own.
type MapPane = {
  map: maplibregl.Map;
  node: HTMLDivElement;
  control: RasterControlType | null;
  colorbar: ColorbarType | null;
  rasterId: string | null;
};

const STATUS: Record<string, MessageKey> = {
  planned: "panel.statusPlanned",
  running: "panel.statusRunning",
  failed: "panel.statusFailed",
};

// Named colormaps of the raster reader; the legend uses the same name, so it matches the
// image. The exact system ramp would need a palette embedded in the COG.
const COLORMAP: Record<string, string> = {
  green: "greens",
  heat: "rdylbu",
  water: "blues",
};

// EPSG:4326, the CRS the layer route writes. Avoids asking epsg.io for it.
const WGS84 =
  'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]';

function coordinate(value: number, language: string): string {
  return new Intl.NumberFormat(language, { maximumFractionDigits: 4 }).format(value).replace("-", "−");
}

// Round steps per decade; consecutive ones differ by at most 2.5×, so one of them leaves 3 to 5 marks.
const STEPS = [1, 2, 2.5, 5];

function decimalsOf(step: number): number {
  let decimals = 0;
  while (decimals < 12 && Math.abs(Math.round(step * 10 ** decimals) - step * 10 ** decimals) > 1e-6) decimals += 1;
  return decimals;
}

// The map legend: the unit once in the title (none for a dimensionless index), and three to five
// marks at round values inside the range, written in the screen language. The range stays the
// colour stretch of the raster; a mark's position is its share of that range, so the bar is unchanged.
export function rasterLegend(
  range: [number, number],
  unit: string | undefined,
  name: string,
  language: string,
  units: Units,
): Legend {
  const shown = unitOf({ unit } as StepResult, units);
  const title = shown ? `${name} (${shown})` : name;
  const low = convertValue(range[0], unit, units);
  const high = convertValue(range[1], unit, units);
  const span = high - low;
  const format = (value: number, decimals: number) =>
    new Intl.NumberFormat(language, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
      .format(value)
      .replace("-", "−");
  if (!(span > 0) || !Number.isFinite(span)) {
    return { title, ticks: [{ value: low, label: format(low, decimalsOf(Math.abs(low) || 1)), position: 0 }] };
  }
  // The smallest round step that leaves at most five marks; failing three to five, the count nearest four.
  const magnitude = 10 ** Math.floor(Math.log10(span / 5));
  let best: number[] = [];
  let bestStep = magnitude;
  for (const step of [magnitude, magnitude * 10, magnitude * 100].flatMap((decade) => STEPS.map((f) => f * decade))) {
    const first = Math.ceil(low / step - 1e-9);
    const last = Math.floor(high / step + 1e-9);
    const values = Array.from({ length: Math.max(0, last - first + 1) }, (_, i) => (first + i) * step);
    if (values.length > 0 && Math.abs(values.length - 4) < Math.abs(best.length - 4)) {
      best = values;
      bestStep = step;
    }
    if (values.length >= 3 && values.length <= 5) {
      best = values;
      bestStep = step;
      break;
    }
  }
  const decimals = decimalsOf(bestStep);
  return {
    title,
    ticks: best.map((raw) => {
      const value = Number(raw.toFixed(decimals)) + 0;
      return { value, label: format(value, decimals), position: Math.min(1, Math.max(0, (value - low) / span)) };
    }),
  };
}

function frameLabel(frame: LayerFrame, t: (key: MessageKey) => string): string {
  if (frame.window === "before") return `${t("panel.before")} · ${frame.t}`;
  if (frame.window === "after") return `${t("panel.after")} · ${frame.t}`;
  return frame.t;
}

// The curtain needs a usable picture on both sides: a missing frame or a declared absence falls
// back to the frame selector, which already says the absence in the layer details.
export function compareFrames(frames: LayerFrame[]): { before: LayerFrame; after: LayerFrame } | null {
  const before = frames.find((item) => item.window === "before");
  const after = frames.find((item) => item.window === "after");
  if (!before?.url || !after?.url || before.absence || after.absence) return null;
  return { before, after };
}

// One raster spec per frame. Both curtains share range, colormap and legend, so the two sides can be
// read against the same scale; only the window changes.
function rasterFor(layer: AnalysisLayer, frame: LayerFrame, language: string, units: Units): RasterLayer | null {
  if (!layer.range || !frame.url) return null;
  return {
    url: frame.url,
    range: layer.range,
    colormap: COLORMAP[layer.scale ?? ""] ?? "viridis",
    reversed: layer.scale === "heat",
    legend: rasterLegend(
      layer.range,
      layer.unit,
      localized(layer.name, language) || layer.variable,
      language,
      units,
    ),
  };
}

function LayerDetails({ layer, frame }: { layer: AnalysisLayer; frame?: LayerFrame }) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const catalog = useCatalog();
  const title = localized(layer.name, language) || layer.variable;
  const absence = layer.absence ?? frame?.absence;
  const low = layer.range
    ? formatMeasure(layer.range[0], layer.unit, language, units, undefined, false)
    : null;
  const high = layer.range
    ? formatMeasure(layer.range[1], layer.unit, language, units, undefined, false)
    : null;

  // This sits on the dark stage in both themes, so its text uses the stage tokens (orbit-*), never the paper ones.
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-6">
        {layer.data_class && (
          <span className="contents [&>span]:border-orbit-line [&>span]:text-orbit-muted">
            <DataClassTag value={layer.data_class} />
          </span>
        )}
        {low && high && (
          <span className="data text-orbit-ink">
            {t("panel.layerRange")}: {low.text} {low.unit} – {high.text} {high.unit}
            <DataClassMark value={layer.data_class} />
          </span>
        )}
        {layer.support?.resolution_m && (
          <span className="caption text-orbit-muted">
            · {t("blocks.resolution")}: {resolutionText(layer.support.resolution_m, language)}
          </span>
        )}
      </div>

      {absence && (
        <p className="small border-l-2 border-amber pl-8 text-orbit-ink">
          {absence.detail ? localized(absence.detail, language) : absence.reason}
        </p>
      )}

      {layer.sources && layer.sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-6">
          {layer.sources.filter((source) => source.source !== "none").map((source) => (
            <a
              key={source.url}
              href={source.url}
              target="_blank"
              rel="noreferrer noopener"
              className="small text-orbit-focus underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orbit-focus"
            >
              {t("panel.layerSource")}: {sourceDisplayName(source, [], catalog?.sources, language)}
            </a>
          ))}
        </div>
      )}

      {layer.limits && layer.limits.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="label text-orbit-muted">{t("panel.layerLimits")}</span>
          <ul className="small flex list-disc flex-col gap-2 pl-16 text-orbit-ink">
            {layer.limits.map((limit, index) => (
              <li key={index}>{localized(limit, language)}</li>
            ))}
          </ul>
        </div>
      )}

      {!absence && title && layer.frames.length === 0 && (
        <p className="small text-orbit-muted">{t("panel.layerNoFrames")}</p>
      )}
    </div>
  );
}

function LayerSection({
  layers,
  active,
  frame,
  comparing,
  onLayer,
  onFrame,
}: {
  layers: AnalysisLayer[];
  active: number;
  frame: number;
  comparing: boolean;
  onLayer: (index: number) => void;
  onFrame: (index: number) => void;
}) {
  const { t, language } = useI18n();
  const layer = layers[active];
  if (!layer) return null;
  const frames = layer.frames;
  // A curtain needs both windows; when one of them has no data the selector returns and this says which
  // side is missing, so the empty half is never a mystery.
  const missing = frames.filter((item) => item.window && item.absence && item !== frames[frame]);

  return (
    <section aria-labelledby="panel-layers" className="flex flex-col gap-8">
      <h3 id="panel-layers" className="title-2">
        {t("panel.layers")}
      </h3>
      <div className="flex flex-wrap gap-12">
        <label className="flex flex-col gap-4">
          <span className="label text-orbit-muted">{t("panel.layerVariable")}</span>
          <select
            value={active}
            onChange={(event) => onLayer(Number(event.target.value))}
            className="body rounded-md border border-orbit-line bg-orbit-2 px-10 py-8 text-orbit-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orbit-focus"
          >
            {layers.map((item, index) => (
              <option key={item.variable} value={index}>
                {localized(item.name, language) || item.variable}
              </option>
            ))}
          </select>
        </label>
        {frames.length > 0 && !comparing && (
          <label className="flex flex-col gap-4">
            <span className="label text-orbit-muted">{t("panel.layerFrame")}</span>
            <select
              value={frame}
              onChange={(event) => onFrame(Number(event.target.value))}
              className="body rounded-md border border-orbit-line bg-orbit-2 px-10 py-8 text-orbit-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orbit-focus"
            >
              {frames.map((item, index) => (
                <option key={`${item.t}-${item.window ?? index}`} value={index}>
                  {frameLabel(item, t)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <LayerDetails layer={layer} frame={comparing ? undefined : frames[frame]} />
      {!comparing &&
        missing.map((item) => (
          <p
            key={`${item.window}-${item.t}`}
            className="small border-l-2 border-amber pl-8 text-orbit-ink"
          >
            {frameLabel(item, t)}:{" "}
            {item.absence?.detail ? localized(item.absence.detail, language) : item.absence?.reason}
          </p>
        ))}
    </section>
  );
}

function ZoneMap({
  zone,
  raster,
  compare,
  rasterFailed,
  onRasterFailed,
}: {
  zone: Zone;
  raster?: RasterLayer | null;
  compare?: CompareRasters | null;
  rasterFailed?: boolean;
  onRasterFailed?: () => void;
}) {
  const { t, language } = useI18n();
  const stage = useRef<HTMLDivElement>(null);
  const baseNode = useRef<HTMLDivElement>(null);
  const splitNode = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [failed, setFailed] = useState(false);
  const [split, setSplit] = useState(50);
  const [west, south, east, north] = zone.bbox;
  const hasGeometry = Boolean(zone.geometry);

  useEffect(() => {
    const base = baseNode.current;
    if (!base) return;
    const root = getComputedStyle(document.documentElement);
    const orbit = root.getPropertyValue("--orbit").trim();
    const focus = root.getPropertyValue("--orbit-focus").trim();
    if (!orbit || !focus) {
      setFailed(true);
      return;
    }
    let cancelled = false;
    const panes: MapPane[] = [];

    async function applyRaster(pane: MapPane, spec: RasterLayer, withColorbar: boolean) {
      try {
        const [{ RasterControl, Colorbar }, { parseWkt }] = await Promise.all([
          import("maplibre-gl-raster"),
          import("@developmentseed/proj"),
        ]);
        if (cancelled) return;
        if (!pane.control) {
          pane.control = new RasterControl({
            collapsed: true,
            epsgResolver: async (code: number) => {
              if (code === 4326) return parseWkt(WGS84);
              throw new Error(`EPSG:${code} is not bundled offline`);
            },
          });
          pane.map.addControl(pane.control, "top-right");
        }
        if (pane.rasterId) pane.control.removeRaster(pane.rasterId);
        const [min, max] =
          spec.range[0] === spec.range[1] ? [spec.range[0], spec.range[0] + 1e-6] : spec.range;
        pane.rasterId = await pane.control.addRaster(spec.url, {
          id: "panel-layer",
          name: spec.legend.title,
          zoomTo: false,
          beforeId: "zone-fill",
          state: {
            mode: "single",
            bands: [1],
            colormap: spec.colormap,
            reversed: spec.reversed ?? false,
            rescale: [[min, max]],
            nodata: "auto",
            opacity: 0.85,
          },
        });
        if (cancelled) return;
        // Only the top map carries the legend in curtain mode: the lower one would be painted over.
        if (!withColorbar) return;
        if (pane.colorbar) pane.map.removeControl(pane.colorbar);
        const { title, ticks } = spec.legend;
        pane.colorbar = new Colorbar({
          colormap: spec.colormap,
          reversed: spec.reversed ?? false,
          min,
          max,
          title,
          units: "",
          tickValues: ticks.map((tick) => tick.value),
          orientation: "horizontal",
          position: "bottom-left",
        });
        pane.map.addControl(pane.colorbar, "bottom-left");
        // The package spaces its marks evenly and writes them with a point; ours sit at their value and
        // speak the screen language. The bar and its colours stay the package's own.
        pane.node.querySelectorAll<HTMLElement>(".mlr-colorbar-tick").forEach((mark, index) => {
          const tick = ticks[index];
          if (!tick) return;
          mark.textContent = tick.label;
          mark.style.left = `${tick.position * 100}%`;
        });
      } catch {
        if (cancelled) return;
        // A failed curtain steps back to the frame selector instead of leaving half a comparison.
        compare?.onFailed();
        onRasterFailed?.();
      }
    }

    function createPane(node: HTMLDivElement, spec: RasterLayer | null, withColorbar: boolean): MapPane {
      const polygon =
        zone.geometry ?? {
          type: "Polygon",
          coordinates: [
            [
              [west, south],
              [east, south],
              [east, north],
              [west, north],
              [west, south],
            ],
          ],
        };
      const map = new maplibregl.Map({
        container: node,
        style: {
          version: 8,
          sources: {},
          layers: [{ id: "background", type: "background", paint: { "background-color": orbit } }],
        },
        bounds: [
          [west, south],
          [east, north],
        ],
        fitBoundsOptions: { padding: 24 },
        attributionControl: false,
        interactive: false,
      });
      const pane: MapPane = { map, node, control: null, colorbar: null, rasterId: null };
      // The frame can change size after the map is made (fonts, the column settling, a rotated phone):
      // the zone is fitted again each time, so its outline is never left clipped in a corner.
      if (typeof ResizeObserver !== "undefined") {
        const observer = new ResizeObserver(() => {
          map.resize();
          map.fitBounds(
            [
              [west, south],
              [east, north],
            ],
            { padding: 24, animate: false },
          );
        });
        observer.observe(node);
        map.once("remove", () => observer.disconnect());
      }
      map.on("load", () => {
        map.addSource("zone", {
          type: "geojson",
          data: { type: "Feature", properties: {}, geometry: polygon } as never,
        });
        map.addLayer({
          id: "zone-fill",
          type: "fill",
          source: "zone",
          paint: { "fill-color": focus, "fill-opacity": 0.08 },
        });
        map.addLayer({
          id: "zone-line",
          type: "line",
          source: "zone",
          paint: { "line-color": focus, "line-width": 2 },
        });
        if (spec) void applyRaster(pane, spec, withColorbar);
      });
      return pane;
    }

    try {
      // The "after" map stays at the bottom; the "before" map sits on top and is clipped at the divider.
      panes.push(createPane(base, compare ? compare.after : (raster ?? null), !compare));
      if (compare && splitNode.current) panes.push(createPane(splitNode.current, compare.before, true));
    } catch {
      // No WebGL (or no browser): the caption below still says which zone it is.
      setFailed(true);
    }
    return () => {
      cancelled = true;
      for (const pane of panes) {
        if (pane.rasterId && pane.control) pane.control.removeRaster(pane.rasterId);
        if (pane.colorbar) pane.map.removeControl(pane.colorbar);
        pane.map.remove();
      }
    };
  }, [zone, west, south, east, north, raster, compare, onRasterFailed]);

  function moveSplit(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = stage.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const next = Math.round(((event.clientX - rect.left) / rect.width) * 100);
    setSplit(Math.min(100, Math.max(0, next)));
  }

  const swipe = compare
    ? {
        onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
          dragging.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          moveSplit(event);
        },
        onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
          if (dragging.current) moveSplit(event);
        },
        onPointerUp: () => {
          dragging.current = false;
        },
        onPointerCancel: () => {
          dragging.current = false;
        },
      }
    : {};

  return (
    <div className="panel-map flex flex-col gap-8">
      <div
        ref={stage}
        className={`panel-map-stage relative aspect-video max-h-[min(60vh,440px)] w-full overflow-hidden rounded-lg border border-orbit-line${compare ? " panel-map-swipe" : ""}`}
        {...swipe}
      >
        <div ref={baseNode} role="img" aria-label={t("panel.mapAlt")} className="panel-map-pane" />
        {compare && (
          <div
            ref={splitNode}
            aria-hidden="true"
            className="panel-map-pane panel-map-split"
            style={{ "--panel-map-split": `${split}%` } as CSSProperties}
          />
        )}
        {compare && (
          <>
            <span className="caption panel-map-label panel-map-label-before">{compare.labels.before}</span>
            <span className="caption panel-map-label panel-map-label-after">{compare.labels.after}</span>
            <span aria-hidden="true" className="panel-map-divider" style={{ left: `${split}%` }}>
              <span className="panel-map-grip" />
            </span>
          </>
        )}
      </div>
      {compare && (
        <div className="flex items-center gap-8">
          <span className="caption text-orbit-muted">{t("panel.before")}</span>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={split}
            onChange={(event) => setSplit(Number(event.target.value))}
            aria-label={`${t("panel.before")} / ${t("panel.after")}`}
            className="panel-map-range flex-1"
          />
          <span className="caption text-orbit-muted">{t("panel.after")}</span>
        </div>
      )}
      {failed && <p className="small text-orbit-muted">{t("panel.mapFallback")}</p>}
      {rasterFailed && <p className="fade-in small border-l-2 border-amber pl-8 text-orbit-ink">{t("panel.layerFailed")}</p>}
      {!hasGeometry && <p className="small text-orbit-muted">{t("panel.bboxOnly")}</p>}
      <p className="caption text-orbit-muted">
        {t("panel.source")}: {zone.source ?? t("blocks.noValue")} · ({coordinate(west, language)},{" "}
        {coordinate(south, language)}) – ({coordinate(east, language)}, {coordinate(north, language)})
      </p>
    </div>
  );
}

export function PanelView({
  analysis: saved,
  results,
  layers = [],
  layersLoading = false,
  layersFailed = false,
}: {
  analysis: SavedAnalysis;
  results: StepResult[];
  layers?: AnalysisLayer[];
  layersLoading?: boolean;
  layersFailed?: boolean;
}) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const variableLabel = useVariableName(VARIABLES);
  const date = new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(new Date(saved.created));
  // The person's question names the panel when it is in the screen's language; otherwise the saved
  // title, and, when that title is a long list of variables, the report's composed headline.
  const question = saved.plan.request?.language === language ? saved.plan.request.text?.trim() : "";
  const title = saved.title[language] ?? saved.title.en;
  const headline =
    question ||
    (title.length <= 90 ? title : fallbackOpening({ saved, results, sentences: [], language, t, variableLabel }).headline);
  const values = results.filter((item) => item.type === "value");
  const series = results.filter((item) => item.type === "series");
  // A series with scenarios is a fan over horizons, not a line over years; the variance table has
  // its own block. A series that carries models (the raw ensemble) is summarised by the fan, never
  // drawn as one line through every model.
  const fans = series.filter((item) => item.method?.operation === "climate_delta");
  const lines = series.filter(
    (item) =>
      item.method?.operation !== "climate_delta" &&
      !(item.series ?? []).some((point) => point.model || point.scenario),
  );
  const splits = results.filter((item) => item.type === "table" && hasVarianceSplit(item));
  const scans = results.filter((item) => item.type === "table" && hasScanMatrix(item));
  const drawn = results.filter(
    (item) =>
      item.type === "value" ||
      item.type === "series" ||
      item.type === "layer" ||
      (item.type === "table" && (hasVarianceSplit(item) || hasScanMatrix(item))),
  ).length;
  // The panel shows the reviewed sentence that cites each figure, the same one the report shows. A result with no
  // finding keeps its figure without a sentence: the block then says it is not filled in, never invents one.
  const findingFor = (id: string) =>
    saved.findings.find((item) => item.results.includes(id) && item.text)?.id;
  const referenceKm = (saved.plan.comparison as { reference_km?: number } | undefined)?.reference_km;
  const zoneLabels: Record<string, string> = {};
  for (const item of series) {
    if (!item.zone || zoneLabels[item.zone]) continue;
    zoneLabels[item.zone] =
      item.zone === saved.plan.zone.id
        ? saved.plan.zone.name
        : `${t("blocks.referenceZone")}${referenceKm ? ` · ${referenceKm} km` : ""}`;
  }

  const [layerIndex, setLayerIndex] = useState(0);
  const [frameIndex, setFrameIndex] = useState(0);
  const [compareFailed, setCompareFailed] = useState(false);
  const [rasterFailed, setRasterFailed] = useState(false);
  useEffect(() => {
    setFrameIndex(0);
    setCompareFailed(false);
    setRasterFailed(false);
  }, [layerIndex, layers]);
  useEffect(() => {
    setRasterFailed(false);
  }, [frameIndex]);
  const activeLayer = layers[layerIndex];
  const activeFrame = activeLayer?.frames[frameIndex];
  const raster = useMemo<RasterLayer | null>(() => {
    if (!activeLayer || !activeFrame) return null;
    return rasterFor(activeLayer, activeFrame, language, units);
  }, [activeLayer, activeFrame, language, units]);
  // The curtain replaces the frame selector when the active layer carries the same variable in a
  // "before" and an "after" window. A failed raster steps back to the selector and says so.
  const onCompareFailed = useCallback(() => setCompareFailed(true), []);
  const onRasterFailed = useCallback(() => setRasterFailed(true), []);
  const pair = useMemo(() => compareFrames(activeLayer?.frames ?? []), [activeLayer]);
  const compare = useMemo<CompareRasters | null>(() => {
    if (!pair || !activeLayer || compareFailed) return null;
    const before = rasterFor(activeLayer, pair.before, language, units);
    const after = rasterFor(activeLayer, pair.after, language, units);
    if (!before || !after) return null;
    return {
      before,
      after,
      labels: { before: frameLabel(pair.before, t), after: frameLabel(pair.after, t) },
      onFailed: onCompareFailed,
    };
  }, [pair, activeLayer, compareFailed, language, units, t, onCompareFailed]);
  const comparing = compare !== null;

  return (
    <main className={`${PAGE} fade-in flex flex-col gap-16 py-22`}>
      <header className="flex flex-col gap-10">
        <AnalysisTabs id={saved.id} current="panel" />
        <span className="label text-signal">{t("panel.kicker")}</span>
        {/* .title-1 is unlayered and sets line-height 1.15, which clips descenders on a wrapped title; the
            important leading wins over it. Long words break instead of pushing the page sideways. */}
        <h1 className="title-1 text-balance leading-tight! wrap-break-word">{headline}</h1>
        <p className="small text-muted">
          {saved.plan.zone.name} · {date}
        </p>
      </header>

      {STATUS[saved.status] && <p className="small text-amber-ink">{t(STATUS[saved.status])}</p>}

      {results.length === 0 && saved.status === "finished" && <p className="body text-muted">{t("panel.noResults")}</p>}
      {results.length > 0 && drawn === 0 && <p className="body text-muted">{t("panel.otherResults")}</p>}
      {drawn > 0 && results.length > drawn && <p className="small text-muted">{t("panel.otherResults")}</p>}

      {values.length > 0 && (
        <section className="grid gap-12 md:grid-cols-2" aria-label={t("panel.figures")}>
          {values.map((item) => (
            <KeyFigure
              key={item.id}
              block={{ block: "KeyFigure", results: [item.id], finding: findingFor(item.id) }}
              results={results}
              findings={saved.findings}
              style={saved.plan.design}
              language={language}
            />
          ))}
        </section>
      )}

      {lines.map((item) => (
        <TrendChart
          key={item.id}
          block={{ block: "TrendChart", results: [item.id], finding: findingFor(item.id) }}
          results={results}
          findings={saved.findings}
          style={saved.plan.design}
          language={language}
          zoneLabels={zoneLabels}
        />
      ))}

      {fans.map((item) => (
        <ScenarioFan
          key={item.id}
          block={{ block: "ScenarioFan", results: [item.id], finding: findingFor(item.id) }}
          results={results}
          findings={saved.findings}
          style={saved.plan.design}
          language={language}
        />
      ))}

      {splits.map((item) => (
        <UncertaintySplit
          key={item.id}
          block={{ block: "UncertaintySplit", results: [item.id], finding: findingFor(item.id) }}
          results={results}
          findings={saved.findings}
          style={saved.plan.design}
          language={language}
        />
      ))}

      {scans.map((item) => (
        <ScanMatrix
          key={item.id}
          block={{ block: "ScanMatrix", results: [item.id], finding: findingFor(item.id) }}
          results={results}
          findings={saved.findings}
          style={saved.plan.design}
          language={language}
        />
      ))}

      <section
        aria-labelledby="panel-map"
        className="flex flex-col gap-8 rounded-xl border border-orbit-line bg-orbit p-16 text-orbit-ink"
      >
        <h2 id="panel-map" className="title-2">
          {t("panel.zone")}
        </h2>
        {layersLoading && <p className="fade-in small text-orbit-muted">{t("panel.layerLoading")}</p>}
        {layersFailed && <p className="fade-in small border-l-2 border-amber pl-8 text-orbit-ink">{t("panel.layerFailed")}</p>}
        <ZoneMap
          zone={saved.plan.zone}
          raster={raster}
          compare={compare}
          rasterFailed={rasterFailed || compareFailed}
          onRasterFailed={onRasterFailed}
        />
        {layers.length > 0 && (
          <LayerSection
            layers={layers}
            active={layerIndex}
            frame={frameIndex}
            comparing={comparing}
            onLayer={setLayerIndex}
            onFrame={setFrameIndex}
          />
        )}
      </section>
    </main>
  );
}

export function Panel() {
  const { analysisId = "" } = useParams();
  const { t } = useI18n();
  const [data, setData] = useState<{ analysis: SavedAnalysis; results: StepResult[] } | null>(null);
  const [catalog, setCatalog] = useState<Awaited<ReturnType<typeof getCatalog>> | null>(null);
  const [layers, setLayers] = useState<AnalysisLayer[]>([]);
  const [layersLoading, setLayersLoading] = useState(true);
  const [layersFailed, setLayersFailed] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setFailed(false);
    Promise.all([getAnalysis(analysisId), getAnalysisResults(analysisId)])
      .then(([analysis, results]) => {
        if (live) setData({ analysis, results });
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [analysisId]);

  useEffect(() => {
    let live = true;
    // Names only: without the catalogue the panel still reads, with the locale copy and the ids.
    getCatalog()
      .then((value) => {
        if (live) setCatalog(value);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setLayers([]);
    setLayersLoading(true);
    setLayersFailed(false);
    getAnalysisLayers(analysisId)
      .then((response) => {
        if (!live) return;
        setLayers(Array.isArray(response.items) ? response.items : []);
      })
      .catch(() => {
        if (live) setLayersFailed(true);
      })
      .finally(() => {
        if (live) setLayersLoading(false);
      });
    return () => {
      live = false;
    };
  }, [analysisId]);

  if (failed) {
    return (
      <main className={`${PAGE} py-22`}>
        <h1 className="title-1">{t("panel.kicker")}</h1>
        <p className="body text-alert">{t("panel.failed")}</p>
      </main>
    );
  }

  if (!data) {
    return (
      <main className={`${PAGE} py-22`}>
        <h1 className="title-1">{t("panel.kicker")}</h1>
        <p className="body text-muted">{t("panel.loading")}</p>
      </main>
    );
  }

  return (
    <CatalogProvider value={catalog}>
      <PanelView
        analysis={data.analysis}
        results={data.results}
        layers={layers}
        layersLoading={layersLoading}
        layersFailed={layersFailed}
      />
    </CatalogProvider>
  );
}
