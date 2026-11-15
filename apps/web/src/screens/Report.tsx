import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { useParams } from "react-router-dom";

import {
  getAnalysis,
  getAnalysisLayers,
  getAnalysisEditorial,
  getAnalysisResults,
  getCatalog,
  type AnalysisLayer,
  type LayerFrame,
} from "../api/client";
import { Block, FIGURES, ReportBlocks } from "../blocks";
import { CatalogProvider, useCatalog, useVariableName } from "../blocks/catalog";
import { EstimateRange } from "../blocks/EstimateRange";
import { operationKey } from "../blocks/MethodBox";
import { editorialOf, fallbackOpening } from "../blocks/opening";
import { RampLegend, RasterCurtain, RasterImage } from "../blocks/RasterImage";
import { fetchPlaceImage, PlaceImage, placeFigureWidth, type PlaceImageInfo } from "../blocks/PlaceImage";
import {
  DataClassTag,
  isMarked,
  evidenceOf,
  figureName,
  fillText,
  formatMeasure,
  formatNumber,
  convertValue,
  GROUPING,
  localized,
  localizedUnit,
  noClearDifference,
  periodText,
  resourceRow,
  sourceDisplayName,
  trendPresentation,
  VARIABLES,
  type StoryBlock,
} from "../blocks/shared";
import type { CatalogSource } from "../catalog/sources";
import type { analysis, result } from "../contracts";
import { DESIGN } from "../design";
import { useI18n } from "../i18n";
import { useUnits } from "../units";
import { Notes, numberSources, ReportToolbar, SourcesSection, useLightPrint } from "./report-common";
import { InstitutionalView, ScientificView } from "./ReportDocument";

import { PAGE } from "../layout/page";
import { CancelledAnalysis } from "./Cancelled";
import "./report-flow.css";
import { DataClassLegend, DataClassMark } from "../blocks/DataClassMark";

type SavedAnalysis = analysis.SavedAnalysis;
type StepResult = result.StepResult;

function fallbackBlocks(results: StepResult[]): StoryBlock[] {
  const blocks: StoryBlock[] = [];
  for (const item of results) {
    if (item.type === "value" || item.absence) blocks.push({ block: "KeyFigure", results: [item.id] });
    else if (item.type === "series") blocks.push({ block: "TrendChart", results: [item.id] });
  }
  blocks.push({ block: "LimitsBox" }, { block: "SourcesTable" });
  return blocks;
}

// Figures drawn on an axis that need the whole width to read.
const WIDE_BLOCKS = new Set(["TrendChart", "ScenarioFan", "RingsDecay", "BeforeAfterMap"]);

function windowOf(item: StepResult): unknown {
  return (item.method?.params as { window?: unknown } | undefined)?.window;
}

// The story's picture, by one rule for every design: a before-and-after pair of map frames when the
// analysis has one; else the chart that draws the result the story features (or its first chart); else
// the range of the estimates; else a frame of the analysis' map layer.
type Visual =
  | { kind: "maps"; variable?: string; pair: boolean }
  | { kind: "block"; block: StoryBlock }
  | { kind: "estimates"; items: StepResult[] };

function pickVisual(flow: StoryBlock[], results: StepResult[], featured: string | undefined): Visual | null {
  const frames = results.filter((item) => item.type === "layer" && !item.absence);
  const before = frames.find((item) => windowOf(item) === "before");
  if (before && frames.some((item) => windowOf(item) === "after")) {
    return { kind: "maps", variable: before.variable, pair: true };
  }
  const charts = flow.filter((block) => FIGURES.has(block.block) && block.block !== "BeforeAfterMap");
  const named = featured ? charts.find((block) => (block.results ?? []).includes(featured)) : undefined;
  if (named ?? charts[0]) return { kind: "block", block: (named ?? charts[0]) as StoryBlock };
  const estimates = results.filter((item) => resourceRow(item) !== null && !item.absence);
  if (estimates.length > 0) return { kind: "estimates", items: estimates };
  const series = results.find(
    (item) => item.type === "series" && !item.absence && item.method?.operation !== "climate_delta" && (item.series ?? []).length > 1,
  );
  if (series) return { kind: "block", block: { block: "TrendChart", results: [series.id] } };
  if (frames.length > 0) return { kind: "maps", variable: frames[0].variable, pair: false };
  return null;
}

function pairOf(frames: LayerFrame[]): LayerFrame[] {
  const before = frames.find((item) => item.window === "before" && item.url && !item.absence);
  const after = frames.find((item) => item.window === "after" && item.url && !item.absence);
  if (before && after) return [before, after];
  const usable = frames.filter((item) => item.url && !item.absence);
  return usable.length > 0 ? [usable[usable.length - 1]] : [];
}

// The nudo's first paragraph: a lone short paragraph is the article's voice, set as a standout; a long one
// opens with a drop cap; a medium one reads plain (a drop cap on two lines is as tall as the paragraph).
const STANDOUT_MAX = 220;
const DROP_CAP_MIN = 260;

export function openingClass(body: string[]): string | undefined {
  const first = body[0] ?? "";
  if (body.length === 1 && first.length < STANDOUT_MAX) return "standout";
  return first.length >= DROP_CAP_MIN ? "lead" : undefined;
}

// The last word and the citation markers never part: the markers stay at the end of the sentence.
function withNotes(text: string, notes: ReactNode): ReactNode {
  const cut = text.trimEnd().lastIndexOf(" ");
  if (cut === -1) return <span className="report-nowrap">{text}{notes}</span>;
  return (
    <>
      {text.slice(0, cut + 1)}
      <span className="report-nowrap">
        {text.slice(cut + 1).trimEnd()}
        {notes}
      </span>
    </>
  );
}

// One key figure the way the draft's figure row reads: its tags, the number, what it is, the interval.
function StatCard({ item, notes }: { item: StepResult; notes: ReactNode }) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const name = useVariableName(VARIABLES);
  const trend = trendPresentation(item, units, language);
  const operation = item.method?.operation;
  const signed = operation === "trend" || operation === "baci_contrast";
  // Three significant figures, the same rule as the composed sentences; a change carries its sign.
  const figure = (value: number, withSign: boolean) =>
    new Intl.NumberFormat(language, { ...GROUPING, maximumSignificantDigits: 3, signDisplay: withSign ? "exceptZero" : "auto" })
      .format(convertValue(value, item.unit, units, operation))
      .replace("-", "−");
  const percentile = item.unit === "percentile" && item.value !== undefined;
  const measured = item.value !== undefined ? { text: figure(item.value, signed) } : null;
  const unit = percentile ? "" : (trend?.unit ?? localizedUnit(item.unit, units, language));
  const resource = resourceRow(item);
  const evidence = resource ? { name: "blocks.estimate" as const } : evidenceOf(item);
  const interval = item.interval
    ? `${t("blocks.interval")} ${new Intl.NumberFormat(language, { style: "percent" }).format(item.interval.level ?? 0.95)}: ${
        trend?.low ?? figure(item.interval.low, false)
      } – ${trend?.high ?? figure(item.interval.high, false)} ${unit}`
    : resource
      ? `${t("blocks.range")}: ${figure(resource.low, false)} – ${figure(resource.high, false)} ${unit}`
      : percentile
        ? `${localizedUnit("percentile", units, language)} ${figure(item.value as number, false)}`
        : "";
  const flat = noClearDifference(item);
  return (
    <div className="report-stat">
      <span className="report-tags">
        <DataClassTag value={item.data_class} />
        {evidence && <span className="tag label evidence">{t(evidence.name)}</span>}
      </span>
      <b className={flat ? "flat" : undefined}>
        {flat
          ? t("blocks.noClearDifference")
          : percentile
            ? `P${Math.round(item.value as number)}`
            : (trend?.value ?? measured?.text ?? t("blocks.noValue"))}
        {/* The asterisk is a footnote mark of the value: it never leaves the number or its unit. */}
        {!flat && unit ? (
          <span className="report-nowrap">
            <span className="unit"> {unit}</span>
            <DataClassMark value={item.data_class} />
          </span>
        ) : (
          <DataClassMark value={item.data_class} />
        )}
      </b>
      <span className="what">
        {figureName(item, language, name)}
        {item.period ? `, ${periodText(item.period, language)}` : ""}
        {notes}
      </span>
      {interval && <small>{interval}</small>}
    </div>
  );
}

export type ReportProps = {
  analysis: SavedAnalysis;
  results: StepResult[];
  style: string;
  catalogSources?: CatalogSource[];
  layers?: AnalysisLayer[];
  layersLoading?: boolean;
  placeImage?: PlaceImageInfo | null;
};

// Three documents for three readers, in one editorial system: the story (journalistic), the briefing note
// (institutional) and the technical report (scientific).
export function ReportView(props: ReportProps) {
  if (props.style === "institutional") return <InstitutionalView {...props} />;
  if (props.style === "scientific") return <ScientificView {...props} />;
  return <JournalisticView {...props} />;
}

function JournalisticView({
  analysis: saved,
  results,
  style,
  catalogSources = [],
  layers = [],
  layersLoading = false,
  placeImage = null,
}: ReportProps) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const story = saved.story?.[style] ?? [];
  const fallback = story.length === 0;
  const blocks = fallback ? fallbackBlocks(results) : story;
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const journalistic = style === "journalistic";
  const date = new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(new Date(saved.created));
  const { sources, placeNumber, notesFor } = numberSources(results, placeImage);
  const fill = (text: Record<string, string> | undefined) => fillText(text, results, language, units);
  const editorial = editorialOf(saved, style);


  // The opening: the server's piece when it wrote one, each field filled from the results it cites; a
  // field that cannot be filled falls back to the generic opening.
  const filledFindings = saved.findings
    .map((finding) => ({ id: finding.id, results: [...finding.results] as string[], text: fillText(finding.text, results, language, units) }))
    .filter((entry): entry is { id: string; results: string[]; text: string } => Boolean(entry.text));
  const sentences = filledFindings.map((entry) => entry.text);
  const generic = fallbackOpening({ saved, results, sentences, language, t, variableLabel });
  const borrowed = editorial ?? editorialOf(saved, "journalistic");
  const headline = fill(borrowed?.headline) ?? generic.headline;
  const lead = fill(borrowed?.lead) ?? generic.lead;
  const meaning = fill(editorial?.meaning);
  const cannotSay = fill(editorial?.cannot_say);
  const closing = fill(editorial?.closing);
  const editorialBody = (editorial?.body ?? []).map(fill).filter((text): text is string => Boolean(text));
  // The nudo: the piece's body; without it, what it means; without a piece, the findings after the lead.
  const body = editorialBody.length > 0 ? editorialBody : meaning ? [meaning] : editorial ? [] : sentences.slice(1, 3);
  const endMeaning = editorialBody.length > 0 ? meaning : null;
  const kicker = generic.kicker && generic.kicker !== headline ? generic.kicker : "";
  // The results the story itself cites: the piece's, else the findings it tells.
  const cited =
    editorial?.results && editorial.results.length > 0
      ? editorial.results
      : filledFindings.slice(0, 3).flatMap((entry) => entry.results);

  // The blocks: the frame draws the figures, the picture and the sources; the rest explain.
  const storyFlow = blocks.filter((block) => block.block !== "KeyFigure" && block.block !== "SourcesTable");
  const visual = pickVisual(storyFlow, results, editorial?.featured);
  const limits = storyFlow.filter((block) => block.block === "LimitsBox");
  const subject = results.find(
    (item) =>
      !item.absence &&
      item.type !== "layer" &&
      resourceRow(item) === null &&
      (!catalog?.variables || catalog.variables.some((variable) => variable.id === item.variable)),
  );
  const norms = storyFlow.filter((block) => block.block === "NormCard");
  const norm = norms.length > 0 ? norms : subject ? [{ block: "NormCard", results: [subject.id] }] : [];
  // In the journalistic piece the evidence lives in each figure's tags and the limits in one short block.
  const dataBlocks = storyFlow.filter(
    (block) =>
      !(visual?.kind === "block" && block === visual.block) &&
      !(visual?.kind === "maps" && visual.pair && block.block === "BeforeAfterMap") &&
      block.block !== "LimitsBox" &&
      block.block !== "NormCard" &&
      !(journalistic && block.block === "EvidenceBadge"),
  );
  const figureBlocks = dataBlocks.filter((block) => FIGURES.has(block.block));
  const evidenceBlocks = dataBlocks.filter((block) => block.block === "EvidenceBadge");
  const evidenceGroup: StoryBlock[] =
    evidenceBlocks.length > 1
      ? [{ block: "EvidenceBadge", results: [...new Set(evidenceBlocks.flatMap((block) => block.results ?? []))] }]
      : evidenceBlocks;
  const proseBlocks = dataBlocks.flatMap((block) =>
    FIGURES.has(block.block)
      ? []
      : block.block !== "EvidenceBadge"
        ? [block]
        : block === evidenceBlocks[0]
          ? evidenceGroup
          : [],
  );

  // The figure row: values the story cites first, at most three in the piece and six in the others.
  const drawnElsewhere = new Set(
    dataBlocks.filter((block) => block.block === "ExposureCounter" || block.block === "ScanMatrix").flatMap((block) => block.results ?? []),
  );
  const candidates = results.filter(
    (item) => item.type === "value" && (!item.absence || noClearDifference(item)) && !drawnElsewhere.has(item.id),
  );
  const stats = [...candidates.filter((item) => cited.includes(item.id)), ...candidates.filter((item) => !cited.includes(item.id))].slice(
    0,
    journalistic ? 3 : 6,
  );

  // "What this does not say" in the piece: what the editor wrote, and at most three limits of the results
  // the story cites. The complete list stays in the scientific format and the data tab.
  const citedLimits = [
    ...new Set(
      results
        .filter((item) => cited.includes(item.id) || (visual?.kind === "block" && (visual.block.results ?? []).includes(item.id)))
        .flatMap((item) => (item.limits ?? []).map((limit) => localized(limit, language))),
    ),
  ].slice(0, 3);
  // The chain can have no finding to cite. Keep its own coverage and reason in the report.
  const chainLimits = results.flatMap((item) => {
    if (item.method.operation !== "evaluate_chain") return [];
    const step = saved.plan.steps.find((step) => step.id === item.step);
    const inherited = new Set(
      results
        .filter((input) => step?.depends_on.includes(input.step))
        .flatMap((input) => (input.limits ?? []).map((limit) => localized(limit, language))),
    );
    return [...(item.limits ?? []), item.absence?.detail]
      .map((limit) => localized(limit, language))
      .filter((limit) => limit && !inherited.has(limit));
  });
  const shortLimits = [...new Set([cannotSay, ...chainLimits, ...citedLimits])]
    .filter((text): text is string => Boolean(text));
  // "How we know": for each method the story's results used, its name and what its evidence level means.
  const howWeKnow = [
    ...new Map(
      results
        .filter((item) => cited.includes(item.id) && !item.absence)
        .map((item) => {
          const key = operationKey(item.method.operation);
          const resource = resourceRow(item) !== null;
          const level = evidenceOf(item);
          return [
            item.method.operation,
            {
              title: key ? t(key) : item.method.operation,
              text: resource ? t("blocks.estimateNote") : level ? `${t(level.name)}: ${t(level.note)}` : "",
              id: item.id,
            },
          ] as const;
        }),
    ).values(),
  ];

  const design = DESIGN[saved.plan.design] ? t(DESIGN[saved.plan.design]) : saved.plan.design;
  const brand = `${t("brand.habitat")} ${t("brand.underPressure")}`;
  // The data classes that show an asterisk somewhere in this report.
  const markedClasses = [...new Set(results.filter((item) => !item.absence && isMarked(item.data_class)).map((item) => item.data_class))];
  const blockProps = { results, findings: saved.findings, style, language, catalogSources };
  const compact = limits.length > 0;

  useLightPrint();

  const mapLayer =
    visual?.kind === "maps" ? (layers.find((layer) => layer.variable === visual.variable) ?? layers.find((layer) => layer.frames.length > 0)) : undefined;
  const mapFrames = mapLayer ? pairOf(mapLayer.frames) : [];
  const mapName = mapLayer ? localized(mapLayer.name, language) || variableLabel(mapLayer.variable) : "";
  const frameLabel = (frame: LayerFrame) =>
    frame.window === "before" ? `${t("panel.before")} · ${frame.t}` : frame.window === "after" ? `${t("panel.after")} · ${frame.t}` : frame.t;
  const mapSourceIds = results.filter((item) => item.type === "layer" && item.variable === mapLayer?.variable).map((item) => item.id);

  return (
    <div className="report-page fade-in">
      <main className={`${PAGE} print:max-w-none print:px-0`}>
        <article
          className="report report-sheet [overflow-wrap:anywhere]"
          data-style={style}
          style={{ "--figure-label": JSON.stringify(t("report.figure")) } as CSSProperties}
        >
          <ReportToolbar id={saved.id} style={style} count={sources.length + (placeImage ? 1 : 0)} />

          <header className="report-col report-head">
            {kicker && <p className="report-kicker">{kicker}</p>}
            <h1>{headline}</h1>
            {lead && <p className="report-standfirst">{lead}</p>}
            <p className="report-byline">
              {brand} · {t("report.analysis")} <span className="chip">{saved.id}</span> · {periodText(saved.plan.period, language)} ·{" "}
              <span className="chip">{design}</span> · {date}
            </p>
          </header>

          {stats.length > 0 && (
            <section className="report-wide report-figures" data-count={stats.length} aria-label={t("panel.figures")}>
              {stats.map((item) => (
                <StatCard key={item.id} item={item} notes={notesFor([item.id])} />
              ))}
              {/* An asterisk alone can be missed: one line under the figures, in the same block. */}
              {markedClasses.length > 0 && (
                <div className="report-mark-legend">
                  <DataClassLegend classes={markedClasses} />
                </div>
              )}
            </section>
          )}
          {stats.length === 0 && markedClasses.length > 0 && (
            <div className="report-wide report-mark-legend">
              <DataClassLegend classes={markedClasses} />
            </div>
          )}

          {(() => {
            // The figures of the story as nodes, so the place view can sit beside the block that follows it.
            const mapsNode =
              visual?.kind === "maps" && (layersLoading || mapFrames.length > 0) ? (
                <figure className="report-figure report-maps" style={{ width: placeFigureWidth(saved.plan.zone.bbox), marginInline: "auto" }}>
              <figcaption className="top">
                <b className="figure-number" /> {mapName}
              </figcaption>
              {layersLoading && <p className="small text-muted">{t("panel.layerLoading")}</p>}
              {mapLayer && mapFrames.length > 0 && mapLayer.range && (
                <>
                  {mapFrames.length > 1 ? (
                    <RasterCurtain
                      frames={mapFrames.map((frame) => ({ url: frame.url as string, label: frameLabel(frame) }))}
                      range={mapLayer.range as [number, number]}
                      scale={mapLayer.scale}
                      name={mapName}
                    />
                  ) : (
                    <RasterImage
                      url={mapFrames[0].url as string}
                      range={mapLayer.range as [number, number]}
                      scale={mapLayer.scale}
                      alt={`${mapName}, ${frameLabel(mapFrames[0])}`}
                      label={frameLabel(mapFrames[0])}
                    />
                  )}
                  <RampLegend
                    scale={mapLayer.scale}
                    low={`${formatMeasure(mapLayer.range[0], mapLayer.unit, language, units, undefined, false).text} ${localizedUnit(mapLayer.unit, units, language)}`}
                    high={`${formatMeasure(mapLayer.range[1], mapLayer.unit, language, units, undefined, false).text} ${localizedUnit(mapLayer.unit, units, language)}`}
                  />
                </>
              )}
              <p className="src">
                {mapLayer?.data_class && <DataClassTag value={mapLayer.data_class} />}
                <DataClassMark value={mapLayer?.data_class} />{" "}
                {[
                  mapLayer?.sources
                    ?.filter((source) => source.source !== "none")
                    .map((source) => sourceDisplayName({ source: source.source, url: source.url }, results, catalogSources, language))
                    .join(" · "),
                  mapLayer?.support?.resolution_m ? `${formatNumber(mapLayer.support.resolution_m, language)} m` : "",
                  t("report.blankNoData"),
                ]
                  .filter(Boolean)
                  .join(" · ")}
                {notesFor(mapSourceIds)}
              </p>
            </figure>
              ) : null;
            const blockNode =
              visual?.kind === "block" ? (
                <figure className="report-wide report-figure">
              <div className="report-block" data-block={visual.block.block}>
                <Block block={visual.block} {...blockProps} compact={compact} notes={notesFor(visual.block.results ?? [])} />
              </div>
            </figure>
              ) : null;
            const estimatesNode =
              visual?.kind === "estimates" ? (
                <figure className="report-wide report-figure">
              <figcaption className="top">
                <b className="figure-number" /> {t("report.estimateFigure")}
              </figcaption>
              <EstimateRange items={visual.items} />
              <p className="src">
                <DataClassTag value={visual.items[0].data_class} /> {t("blocks.estimateNote")}
                <DataClassMark value={visual.items[0].data_class} />
                {notesFor(visual.items.map((item) => item.id))}
              </p>
            </figure>
              ) : null;
            const proseNode =
              body.length > 0 || closing || endMeaning ? (
                <div className="report-col report-prose">
              {body.map((paragraph, index) => (
                <p key={paragraph} className={index === 0 ? openingClass(body) : undefined}>
                  {index === 0 ? withNotes(paragraph, notesFor(cited)) : paragraph}
                </p>
              ))}
              {(closing || endMeaning) && (
                <>
                  <h2>{t("report.meaning")}</h2>
                  {closing && <p>{closing}</p>}
                  {endMeaning && <p>{endMeaning}</p>}
                </>
              )}
            </div>
              ) : null;
            const visualNode = mapsNode ?? blockNode ?? estimatesNode;
            // A chart or a map needs the whole width: the place view then stands alone, centred. A narrower
            // block (a matrix, a counter, the estimate range), or the text when there is no figure, may sit
            // beside a narrow photo; the CSS wraps it below when the photo is wider than half the row.
            const wide = visual?.kind === "maps" || (visual?.kind === "block" && WIDE_BLOCKS.has(visual.block.block));
            const companion = !placeImage || wide ? null : visualNode ? "visual" : proseNode ? "prose" : null;
            return (
              <>
                {placeImage && (
                  <div className="report-wide report-place-row">
                    <PlaceImage zone={saved.plan.zone} info={placeImage} notes={<Notes numbers={[placeNumber]} />} />
                    {companion === "visual" && <div className="report-place-side">{visualNode}</div>}
                    {companion === "prose" && <div className="report-place-side">{proseNode}</div>}
                  </div>
                )}
                {companion !== "visual" && visualNode}
                {companion !== "prose" && proseNode}
              </>
            );
          })()}

          {fallback && <p className="report-col small text-muted">{t("report.noStory")}</p>}

          {figureBlocks.map((block, index) => (
            <figure key={`${block.block}-${index}`} className="report-wide report-figure">
              <div className="report-block" data-block={block.block}>
                <Block block={block} {...blockProps} compact={compact} notes={notesFor(block.results ?? [])} />
              </div>
            </figure>
          ))}

          {proseBlocks.length > 0 && (
            <div className="report-col report-boxes">
              <ReportBlocks blocks={proseBlocks} {...blockProps} accessed={date} />
            </div>
          )}

          {journalistic && howWeKnow.length > 0 && (
            <aside className="report-col report-box" aria-labelledby="report-how">
              <h2 id="report-how">{t("report.howWeKnow")}</h2>
              <ul>
                {howWeKnow.map((entry) => (
                  <li key={entry.title}>
                    <b>{entry.title}.</b> {entry.text}
                    {notesFor([entry.id])}
                  </li>
                ))}
                <li>
                  <b>{t("report.repeatable")}.</b> {t("report.repeatableNote")}
                </li>
              </ul>
            </aside>
          )}

          {journalistic && shortLimits.length > 0 && (
            <aside className="report-col report-box limits" aria-labelledby="report-limits">
              <h2 id="report-limits">{t("blocks.limits")}</h2>
              <ul>
                {shortLimits.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </aside>
          )}

          {!journalistic && limits.length > 0 && (
            <div className="report-col report-boxes report-limits">
              <ReportBlocks blocks={limits} {...blockProps} />
            </div>
          )}

          {norm.length > 0 && (
            <div className="report-col report-boxes print:hidden">
              <ReportBlocks blocks={norm} {...blockProps} />
            </div>
          )}

          <SourcesSection
            saved={saved}
            results={results}
            sources={sources}
            catalogSources={catalogSources}
            placeImage={placeImage}
            placeNumber={placeNumber}
            date={date}
          />
        </article>
      </main>
    </div>
  );
}

export function Report() {
  const { analysisId = "", style = "journalistic" } = useParams();
  const { t } = useI18n();
  const [data, setData] = useState<{
    analysis: SavedAnalysis;
    results: StepResult[];
    catalogSources: CatalogSource[];
    catalog: Awaited<ReturnType<typeof getCatalog>> | null;
  } | null>(null);
  const [layers, setLayers] = useState<AnalysisLayer[]>([]);
  const [placeImage, setPlaceImage] = useState<PlaceImageInfo | null>(null);
  const [layersLoading, setLayersLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setFailed(false);
    setData(null);
    setLayers([]);
    setPlaceImage(null);
    Promise.all([
      getAnalysis(analysisId),
      getAnalysisResults(analysisId),
      // The license and the attribution line live in the catalogue; without it the report still reads.
      getCatalog().catch(() => null),
    ])
      .then(([analysis, results, catalog]) => {
        if (!live) return;
        setData({ analysis, results, catalogSources: catalog?.sources ?? [], catalog });
        // The satellite view of the place, when the image route has one; a missing route leaves no frame.
        fetchPlaceImage(analysis.plan.zone.bbox)
          .then((info) => {
            if (live) setPlaceImage(info);
          })
          .catch(() => {});
        // The map layers are generated on demand and can take a while: only an analysis with map frames
        // asks for them, and the report reads meanwhile.
        const frames = results.filter((item) => item.type === "layer");
        const pair = frames.some((item) => windowOf(item) === "before") && frames.some((item) => windowOf(item) === "after");
        const drawable = results.some((item) => item.type === "series" || resourceRow(item) !== null);
        if (frames.length === 0 || (!pair && drawable)) return;
        setLayersLoading(true);
        getAnalysisLayers(analysisId)
          .then((response) => {
            if (live) setLayers(Array.isArray(response.items) ? response.items : []);
          })
          .catch(() => {})
          .finally(() => {
            if (live) setLayersLoading(false);
          });
      })
      .catch(() => {
        if (live) setFailed(true);
      });
    return () => {
      live = false;
    };
  }, [analysisId]);

  // The institutional and scientific openings are written on demand: the document renders at once with what
  // the data give, and the piece replaces those lines in place when it arrives. A failure changes nothing.
  const loadedId = data?.analysis.id;
  const finished = data?.analysis.status === "finished";
  const hasPiece = data ? Boolean(editorialOf(data.analysis, style)) : true;
  useEffect(() => {
    if (!loadedId || !finished || hasPiece || (style !== "institutional" && style !== "scientific")) return;
    const controller = new AbortController();
    getAnalysisEditorial(loadedId, style, controller.signal)
      .then((piece) => {
        if (controller.signal.aborted || !piece) return;
        setData((current) => {
          if (!current || current.analysis.id !== loadedId) return current;
          const story = (current.analysis.story ?? {}) as unknown as { editorial?: Record<string, unknown> };
          const merged = { ...story, editorial: { ...(story.editorial ?? {}), [style]: piece } };
          return { ...current, analysis: { ...current.analysis, story: merged as unknown as typeof current.analysis.story } };
        });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [loadedId, finished, hasPiece, style]);

  if (failed) {
    return (
      <main className={`${PAGE} py-22`}>
        <h1 className="title-1">{t("report.kicker")}</h1>
        <p className="body text-alert">{t("report.failed")}</p>
      </main>
    );
  }

  if (!data) {
    return (
      <main className={`${PAGE} py-22`}>
        <h1 className="title-1">{t("report.kicker")}</h1>
        <p className="body text-muted">{t("report.loading")}</p>
      </main>
    );
  }

  // A cancelled analysis has no answer to report: its question and a way to ask again.
  if (data.analysis.status === "cancelled") {
    return <CancelledAnalysis question={data.analysis.plan?.request?.text ?? ""} />;
  }

  return (
    <CatalogProvider value={data.catalog}>
      <ReportView
        analysis={data.analysis}
        results={data.results}
        style={style}
        catalogSources={data.catalogSources}
        layers={layers}
        layersLoading={layersLoading}
        placeImage={placeImage}
      />
    </CatalogProvider>
  );
}
