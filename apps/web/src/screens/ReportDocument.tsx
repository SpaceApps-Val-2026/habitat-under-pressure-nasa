// The institutional briefing note and the scientific technical report. Same editorial system as the story
// (type, rules, sources), a different document for a different reader: the officer who must decide reads
// a summary, tables and the law; the reader who checks or reproduces reads numbered sections, every
// result, the method with its parameters, the diagnostics and how to run it again.
import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

import { Block, FIGURES, ReportBlocks } from "../blocks";
import { useCatalog, useVariableName } from "../blocks/catalog";
import { DataClassLegend, DataClassMark } from "../blocks/DataClassMark";
import { operationKey } from "../blocks/MethodBox";
import { editorialOf, fallbackOpening, placeName, subjectNames } from "../blocks/opening";
import { PlaceImage } from "../blocks/PlaceImage";
import {
  absenceDetail,
  absenceLabel,
  convertValue,
  DATA_CLASS,
  diagnosticEntries,
  evidenceOf,
  figureName,
  fillText,
  formatNumber,
  formatP,
  GROUPING,
  isMarked,
  localized,
  localizedUnit,
  noClearDifference,
  periodText,
  resolutionText,
  resourceRow,
  sourceDisplayName,
  trendPresentation,
  VARIABLES,
  type StepResult,
  type StoryBlock,
} from "../blocks/shared";
import { DESIGN } from "../design";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";
import { useUnits, type Units } from "../units";
import { numberSources, ReportToolbar, SourcesSection, useLightPrint } from "./report-common";
import type { ReportProps } from "./Report";

type Translate = (key: MessageKey) => string;

// A story without blocks for this format still shows its figures: values and series, then limits.
function storyBlocks(saved: ReportProps["analysis"], results: StepResult[], style: string): StoryBlock[] {
  const story = saved.story?.[style] ?? [];
  if (story.length > 0) return story;
  return results.flatMap((item): StoryBlock[] =>
    item.type === "series" && !item.absence ? [{ block: "TrendChart", results: [item.id] }] : [],
  );
}

// A figure's value, unit and interval in words, three significant figures, the same rule as the story.
function figureParts(item: StepResult, units: Units, language: string, t: Translate) {
  const trend = trendPresentation(item, units, language);
  const operation = item.method?.operation;
  const signed = operation === "trend" || operation === "baci_contrast";
  const number = (value: number, withSign: boolean) =>
    new Intl.NumberFormat(language, { ...GROUPING, maximumSignificantDigits: 3, signDisplay: withSign ? "exceptZero" : "auto" })
      .format(convertValue(value, item.unit, units, operation))
      .replace("-", "−");
  const percentile = item.unit === "percentile" && item.value !== undefined;
  const unit = percentile ? "" : (trend?.unit ?? localizedUnit(item.unit, units, language));
  const resource = resourceRow(item);
  const value = noClearDifference(item)
    ? t("blocks.noClearDifference")
    : item.absence
      ? absenceLabel(item, t)
      : percentile
        ? `P${Math.round(item.value as number)}`
        : item.value !== undefined
          ? (trend?.value ?? number(item.value, signed))
          : "";
  const interval = item.interval
    ? `${trend?.low ?? number(item.interval.low, false)} – ${trend?.high ?? number(item.interval.high, false)}${unit ? ` ${unit}` : ""}${
        item.interval.level ? ` (${new Intl.NumberFormat(language, { style: "percent" }).format(item.interval.level)})` : ""
      }`
    : resource
      ? `${number(resource.low, false)} – ${number(resource.high, false)}${unit ? ` ${unit}` : ""}`
      : "";
  return { value, unit: noClearDifference(item) || item.absence ? "" : unit, interval };
}

// The value cell of a table: the number, its unit, and the asterisk when the class is not a measurement.
function ValueCell({ item }: { item: StepResult }) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const parts = figureParts(item, units, language, t);
  if (item.type === "series" && !item.absence) {
    const count = (item.series ?? []).filter((point) => point.v !== undefined && point.v !== null).length;
    return <span className="report-nowrap">{t("report.doc.seriesOf").replace("{n}", formatNumber(count, language))}</span>;
  }
  return (
    <span className="report-nowrap">
      {parts.value}
      {parts.unit && <span className="unit"> {parts.unit}</span>}
      {!item.absence && <DataClassMark value={item.data_class} />}
    </span>
  );
}

function sourceNames(items: StepResult[], language: string, catalogSources: ReportProps["catalogSources"]) {
  return [
    ...new Set(
      items.flatMap((item) =>
        item.sources
          .filter((source) => source.source !== "none")
          .map((source) => sourceDisplayName(source, items, catalogSources, language)),
      ),
    ),
  ];
}

// The descriptive title, one rule set keyed on design: an estimate names its resources and the place; a
// scan, recent trends; a question about an event that Delfos named, the event; otherwise the variables,
// at most two and short, or the design itself when they are many or long.
const TITLE_MAX = 60;

function descriptiveTitle(
  saved: ReportProps["analysis"],
  results: StepResult[],
  language: string,
  t: Translate,
  variableLabel: (id: string) => string,
  period: string,
): string {
  const place = placeName(saved.plan.zone.name);
  const and = language === "es" ? " y " : " and ";
  const fill = (key: MessageKey, values: Record<string, string>) =>
    Object.entries(values).reduce((text, [name, value]) => text.replace(`{${name}}`, value), t(key));
  const estimates = results.filter((item) => !item.absence && resourceRow(item) !== null);
  if (estimates.length > 0) {
    const resources = [
      ...new Set(
        estimates.map((item) => {
          const resource = (item.method?.params as { archetype?: { resource?: string } } | undefined)?.archetype?.resource;
          const key = `report.doc.resource.${resource}` as MessageKey;
          return resource && ["water", "energy", "land"].includes(resource) ? t(key) : figureName(item, language, variableLabel);
        }),
      ),
    ];
    return fill("report.doc.titleEstimate", { resources: resources.join(and), place });
  }
  const decided = (saved.plan.answer as { decided?: { field?: string; text?: Record<string, string> }[] }).decided ?? [];
  const event = localized(decided.find((entry) => entry.field === "event")?.text, language);
  if (event) return fill("report.doc.titleEvent", { event, place, period });
  if (saved.plan.design === "scan") return fill("report.doc.titleScan", { place, period });
  const names = subjectNames(saved, results, language, variableLabel);
  if (names.length > 2 || names.join(and).length > TITLE_MAX) {
    const design = DESIGN[saved.plan.design] ? t(DESIGN[saved.plan.design]) : saved.plan.design;
    return fill("report.doc.titleDesign", { design, place, period });
  }
  return fill("report.doc.title", { variables: names.join(and), place, period });
}

// A headline is specific when it names the place and at least one word of a subject's name.
export function specificHeadline(headline: string, place: string, subjects: string[]): boolean {
  const text = headline.toLocaleLowerCase();
  if (!place || !text.includes(place.toLocaleLowerCase())) return false;
  const words = subjects.flatMap((name) => name.toLocaleLowerCase().split(/[^\p{L}\p{N}]+/u)).filter((word) => word.length >= 4);
  return words.some((word) => text.includes(word));
}

// Shared by both documents: the title, the main result and the figures of the format.
function useDocument(props: ReportProps) {
  const { analysis: saved, results, style, placeImage = null } = props;
  const { t, language } = useI18n();
  const { units } = useUnits();
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const date = new Intl.DateTimeFormat(language, { dateStyle: "medium" }).format(new Date(saved.created));
  const numbering = numberSources(results, placeImage);
  const editorial = editorialOf(saved, style);
  const fill = (text: Record<string, string> | undefined) => fillText(text, results, language, units);
  const findings = saved.findings
    .map((finding) => ({ results: [...finding.results] as string[], text: fillText(finding.text, results, language, units) }))
    .filter((entry): entry is { results: string[]; text: string } => Boolean(entry.text));
  const generic = fallbackOpening({ saved, results, sentences: findings.map((entry) => entry.text), language, t, variableLabel });
  // A sober descriptive title: what, where, when. The format's own editorial headline when the server writes one.
  const period = periodText(saved.plan.period, language);
  // The server's headline only when it is specific: it names the place and one of the subjects. A generic
  // one ("Environmental results for X") gives less than the descriptive title, which then stays. The
  // technical report keeps its descriptive title always.
  const descriptive = descriptiveTitle(saved, results, language, t, variableLabel, period);
  const served = style === "scientific" ? null : fill(editorial?.headline);
  const title = served && specificHeadline(served, placeName(saved.plan.zone.name), subjectNames(saved, results, language, variableLabel))
    ? served
    : descriptive;
  const cited =
    editorial?.results && editorial.results.length > 0 ? editorial.results : findings.slice(0, 3).flatMap((entry) => entry.results);
  // The main result is a figure: one the story cites, else the first value, else the first series.
  const isFigure = (item: StepResult) => item.type === "value" && (!item.absence || noClearDifference(item));
  const main =
    results.find((item) => cited.includes(item.id) && isFigure(item)) ??
    results.find(isFigure) ??
    results.find((item) => !item.absence && item.type !== "layer");
  const blocks = storyBlocks(saved, results, style);
  const figures = blocks.filter((block) => FIGURES.has(block.block));
  const limits = blocks.filter((block) => block.block === "LimitsBox");
  const subject = results.find(
    (item) =>
      !item.absence &&
      item.type !== "layer" &&
      resourceRow(item) === null &&
      (!catalog?.variables || catalog.variables.some((variable) => variable.id === item.variable)),
  );
  const norms = blocks.filter((block) => block.block === "NormCard");
  const norm: StoryBlock[] = norms.length > 0 ? norms : subject ? [{ block: "NormCard", results: [subject.id] }] : [];
  const values = results.filter((item) => item.type === "value");
  const markedClasses = [...new Set(results.filter((item) => !item.absence && isMarked(item.data_class)).map((item) => item.data_class))];
  const design = DESIGN[saved.plan.design] ? t(DESIGN[saved.plan.design]) : saved.plan.design;
  return {
    saved,
    t,
    language,
    units,
    date,
    numbering,
    editorial,
    fill,
    findings,
    generic,
    title,
    period,
    cited,
    main,
    figures,
    limits,
    norm,
    values,
    markedClasses,
    design,
    variableLabel,
  };
}

function DocumentFrame({
  props,
  label,
  title,
  context,
  children,
}: {
  props: ReportProps;
  label: string;
  title: string;
  context: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const { sources } = numberSources(props.results, props.placeImage ?? null);
  useLightPrint();
  return (
    <div className="report-page fade-in">
      <main className={`${PAGE} print:max-w-none print:px-0`}>
        <article
          className="report report-sheet report-document [overflow-wrap:anywhere]"
          data-style={props.style}
          style={{ "--figure-label": JSON.stringify(t("report.figure")) } as CSSProperties}
        >
          <ReportToolbar id={props.analysis.id} style={props.style} count={sources.length + (props.placeImage ? 1 : 0)} />
          <header className="report-col report-doc-head">
            <p className="report-doc-label">{label}</p>
            <h1>{title}</h1>
            <p className="report-doc-context">{context}</p>
          </header>
          {children}
        </article>
      </main>
    </div>
  );
}

function Figures({ doc, props, compact }: { doc: ReturnType<typeof useDocument>; props: ReportProps; compact: boolean }) {
  const blockProps = {
    results: props.results,
    findings: props.analysis.findings,
    style: props.style,
    language: doc.language,
    catalogSources: props.catalogSources,
  };
  return (
    <>
      {doc.figures.map((block, index) => (
        <figure key={`${block.block}-${index}`} className="report-wide report-figure">
          <div className="report-block" data-block={block.block}>
            <Block
              block={block}
              {...blockProps}
              compact={compact}
              expanded={props.style === "scientific"}
              notes={doc.numbering.notesFor(block.results ?? [])}
            />
          </div>
        </figure>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------------------------------------
// Institutional: a briefing note for whoever must decide.

const DATA_OPERATIONS = new Set(["read_series", "zone_layer", "read_exposure", "validate_against"]);

export function InstitutionalView(props: ReportProps) {
  const doc = useDocument(props);
  const { saved, t, language, units, numbering, editorial, fill, main } = doc;
  const results = props.results;
  const catalogSources = props.catalogSources ?? [];
  const placeImage = props.placeImage ?? null;

  // The summary: what was found, how solid it is, what it does not allow to conclude.
  const found = fill(editorial?.lead) ?? doc.findings[0]?.text ?? doc.generic.lead;
  const evidence = main ? (resourceRow(main) ? { name: "blocks.estimate" as MessageKey, note: "blocks.estimateNote" as MessageKey } : evidenceOf(main)) : undefined;
  const dataClass = main && DATA_CLASS[main.data_class] ? t(DATA_CLASS[main.data_class]) : main?.data_class;
  const generatedSolid = [evidence ? `${t(evidence.name)}: ${t(evidence.note)}` : "", dataClass ? `${t("report.doc.dataClass")}: ${dataClass}.` : ""]
    .filter(Boolean)
    .join(" ");
  // The server's piece, when it has arrived: what it means feeds how solid it is, with the evidence kept.
  const meaningText = fill(editorial?.meaning);
  const solid = meaningText ? [meaningText, evidence ? `${t(evidence.name)}.` : ""].filter(Boolean).join(" ") : generatedSolid;
  const firstLimit = main?.limits?.[0] ? localized(main.limits[0], language) : "";
  const cannot = fill(editorial?.cannot_say) ?? (firstLimit || t("blocks.notCausal"));
  const analysedItems = results.filter((item) => !item.absence && item.type !== "layer");
  const resolution = main?.support?.resolution_m;
  const analysed = [
    subjectNames(saved, results, language, doc.variableLabel).join(", "),
    sourceNames(analysedItems, language, catalogSources).join(", "),
    doc.period,
    saved.plan.zone.name,
    resolution !== undefined ? `${t("blocks.resolution")}: ${resolutionText(resolution, language)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  // Limits grouped by where they come from: the data, the method, and what cannot be attributed.
  const unique = (texts: string[]) => [...new Set(texts.filter(Boolean))];
  const limitsData = unique([
    ...results
      .filter((item) => DATA_OPERATIONS.has(item.method?.operation))
      .flatMap((item) => (item.limits ?? []).map((limit) => localized(limit, language))),
    ...results
      .filter((item) => item.absence && !noClearDifference(item))
      .map((item) => `${figureName(item, language, doc.variableLabel)}: ${absenceLabel(item, t)}. ${absenceDetail(item, language, doc.variableLabel)}`),
  ]);
  const limitsMethod = unique(
    results
      .filter((item) => !DATA_OPERATIONS.has(item.method?.operation))
      .flatMap((item) => (item.limits ?? []).map((limit) => localized(limit, language))),
  ).filter((text) => !limitsData.includes(text));
  const attribution = unique([
    t("blocks.notCausal"),
    fill(editorial?.cannot_say) ?? "",
    main && evidenceOf(main)?.name === "evidence.E1control.name" ? t("evidence.E1control.note") : "",
  ]);

  // What this figure is for, and what it is not for: its evidence and its scale.
  const place = placeName(saved.plan.zone.name);
  const scale =
    resolution === undefined
      ? ""
      : resolution >= 1000
        ? t("report.doc.useScale").replace("{res}", resolutionText(resolution, language)).replace("{place}", place)
        : t("report.doc.useFine").replace("{res}", resolutionText(resolution, language));
  const useFor = [evidence ? t(evidence.note) : "", scale].filter(Boolean);
  const notFor = unique([t("blocks.notCausal"), cannot]);

  const rows = doc.values.filter((item) => !item.absence || noClearDifference(item));
  const blockProps = { results, findings: saved.findings, style: props.style, language, catalogSources };
  const context = [saved.plan.request?.text ? `«${saved.plan.request.text}»` : "", saved.plan.zone.name, doc.period, doc.date]
    .filter(Boolean)
    .join(" · ");

  return (
    <DocumentFrame props={props} label={t("report.doc.institutional")} title={doc.title} context={context}>
      <section className="report-col report-summary" aria-labelledby="report-summary">
        <h2 id="report-summary">{t("report.doc.summary")}</h2>
        <ul>
          <li>
            <b>{t("report.doc.found")}.</b> {found}
            {numbering.notesFor(doc.cited)}
          </li>
          {solid && (
            <li>
              <b>{t("report.doc.solid")}.</b> {solid}
            </li>
          )}
          <li>
            <b>{t("report.doc.cannotConclude")}.</b> {cannot}
          </li>
        </ul>
        <p className="report-analysed">
          <b>{t("report.doc.analysed")}:</b> {analysed}
        </p>
      </section>

      {rows.length > 0 && (
        <section className="report-col report-table-block" aria-labelledby="report-indicators">
          <h2 id="report-indicators">{t("report.doc.indicators")}</h2>
          <div className="report-table-scroll">
            <table className="report-table">
              <thead>
                <tr>
                  <th scope="col">{t("report.doc.colIndicator")}</th>
                  <th scope="col">{t("report.doc.colValue")}</th>
                  <th scope="col">{t("report.doc.colInterval")}</th>
                  <th scope="col">{t("report.doc.colEvidence")}</th>
                  <th scope="col">{t("report.doc.colSource")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => {
                  const level = resourceRow(item) ? { name: "blocks.estimate" as MessageKey } : evidenceOf(item);
                  return (
                    <tr key={item.id}>
                      <th scope="row">
                        {figureName(item, language, doc.variableLabel)}
                        {item.period && <span className="sub">{periodText(item.period, language)}</span>}
                      </th>
                      <td>
                        <ValueCell item={item} />
                      </td>
                      <td className="num">{figureParts(item, units, language, t).interval || "—"}</td>
                      <td className="num">{level ? t(level.name) : "—"}</td>
                      <td>
                        {sourceNames([item], language, catalogSources).join(", ") || "—"}
                        {numbering.notesFor([item.id])}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {doc.markedClasses.length > 0 && <DataClassLegend classes={doc.markedClasses} />}
        </section>
      )}

      {placeImage && (
        <div className="report-wide report-place-row">
          <PlaceImage zone={saved.plan.zone} info={placeImage} notes={numbering.placeNumber ? <NotesOf n={numbering.placeNumber} /> : null} />
        </div>
      )}
      <Figures doc={doc} props={props} compact />

      {doc.norm.length > 0 && (
        <section className="report-col report-law" aria-labelledby="report-law">
          <h2 id="report-law">{t("report.doc.lawTitle")}</h2>
          <div className="report-boxes">
            <ReportBlocks blocks={doc.norm.map((block) => block)} {...blockProps} expanded />
          </div>
        </section>
      )}

      {(limitsData.length > 0 || limitsMethod.length > 0 || attribution.length > 0) && (
        <section className="report-col report-limit-groups" aria-labelledby="report-limit-groups">
          <h2 id="report-limit-groups">{t("blocks.limits")}</h2>
          {[
            ["report.doc.limitsData", limitsData],
            ["report.doc.limitsMethod", limitsMethod],
            ["report.doc.limitsAttribution", attribution],
          ].map(([key, list]) =>
            (list as string[]).length > 0 ? (
              <div key={key as string}>
                <h3>{t(key as MessageKey)}</h3>
                <ul>
                  {(list as string[]).map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
        </section>
      )}

      <section className="report-col report-use" aria-labelledby="report-use">
        <h2 id="report-use">{t("report.doc.useTitle")}</h2>
        <div className="report-use-grid">
          {useFor.length > 0 && (
          <div>
            <h3>{t("report.doc.useFor")}</h3>
            <ul>
              {useFor.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </div>
          )}
          <div>
            <h3>{t("report.doc.notFor")}</h3>
            <ul>
              {notFor.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <SourcesSection
        saved={saved}
        results={results}
        sources={numbering.sources}
        catalogSources={catalogSources}
        placeImage={placeImage}
        placeNumber={numbering.placeNumber}
        date={doc.date}
      />
    </DocumentFrame>
  );
}

function NotesOf({ n }: { n: number }) {
  const { t } = useI18n();
  return (
    <a className="fn" href={`#source-${n}`} aria-label={`${t("blocks.source")} ${n}`}>
      {n}
    </a>
  );
}

// ---------------------------------------------------------------------------------------------------------
// Scientific: a technical report for whoever checks or reproduces.

// Steps that write and review the text, not the data: they stay in the method section, not the abstract.
const WRITING_OPERATIONS = new Set(["write_findings", "review_findings"]);

function paramsText(params: unknown): string {
  if (!params || typeof params !== "object") return "";
  return Object.entries(params as Record<string, unknown>)
    .map(([key, value]) => {
      const shown = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
      return `${key}: ${shown.length > 90 ? `${shown.slice(0, 87)}…` : shown}`;
    })
    .join(" · ");
}

export function ScientificView(props: ReportProps) {
  const doc = useDocument(props);
  const { saved, t, language, units, numbering, main } = doc;
  const results = props.results;
  const catalogSources = props.catalogSources ?? [];
  const placeImage = props.placeImage ?? null;
  const rows = results.filter((item) => item.type !== "layer");
  const absences = results.filter((item) => item.absence && !noClearDifference(item));
  const diagnosed = results.filter((item) => item.diagnostics && Object.keys(item.diagnostics).length > 0);
  const steps = saved.plan.steps ?? [];
  const decided = ((saved.plan.answer as { decided?: { field?: string; text?: Record<string, string> }[] }).decided ?? [])
    .map((entry) => localized(entry.text, language))
    .filter(Boolean);
  const nOf = (item: StepResult) =>
    item.statistic?.n ?? item.coverage?.n_dates ?? (item.diagnostics?.n_dates as number | undefined) ?? undefined;
  const statisticOf = (item: StepResult) =>
    item.statistic?.name
      ? `${item.statistic.name}${
          typeof item.statistic.value === "number"
            ? ` = ${new Intl.NumberFormat(language, { maximumSignificantDigits: 3 }).format(item.statistic.value).replace("-", "−")}`
            : ""
        }`
      : "";
  const pOf = (item: StepResult) => {
    if (typeof item.statistic?.p_adjusted === "number") return `${formatP(item.statistic.p_adjusted, language)} (${t("report.doc.adjusted")})`;
    return typeof item.statistic?.p === "number" ? formatP(item.statistic.p, language) : "";
  };

  // The abstract, assembled from the fields: objective, data, method, result, main limitation.
  const dataSources = sourceNames(rows.filter((item) => !item.absence), language, catalogSources);
  const mainParts = main ? figureParts(main, units, language, t) : null;
  const methods = [
    ...new Map(
      steps.filter((step) => !WRITING_OPERATIONS.has(step.operation)).map((step) => {
        const key = operationKey(step.operation);
        return [step.operation, `${key ? t(key) : step.operation} (v${step.version})`];
      }),
    ).values(),
  ];
  const resolution = main?.support?.resolution_m;
  const abstract = [
    saved.plan.request?.text ? `${t("report.doc.objective")}: ${saved.plan.request.text}` : "",
    `${t("report.doc.data")}: ${[
      dataSources.join(", "),
      doc.period,
      main && nOf(main) !== undefined ? `n = ${formatNumber(nOf(main) as number, language)}` : "",
      resolution !== undefined ? `${t("blocks.resolution").toLowerCase()} ${resolutionText(resolution, language)}` : "",
    ]
      .filter(Boolean)
      .join("; ")}.`,
    methods.length > 0 ? `${t("report.doc.method")}: ${methods.join(", ")}.` : "",
    main && mainParts && mainParts.value
      ? `${t("report.doc.result")}: ${figureName(main, language, doc.variableLabel)} ${mainParts.value}${mainParts.unit ? ` ${mainParts.unit}` : ""}${
          mainParts.interval ? ` [${mainParts.interval}]` : ""
        }${statisticOf(main) ? `; ${statisticOf(main)}` : ""}${pOf(main) ? `; p ${pOf(main).startsWith("<") ? "" : "= "}${pOf(main)}` : ""}.`
      : "",
    // The server's "cannot say" is the main limitation when its piece has arrived.
    doc.fill(doc.editorial?.cannot_say)
      ? `${t("report.doc.mainLimit")}: ${doc.fill(doc.editorial?.cannot_say)}`
      : main?.limits?.[0]
        ? `${t("report.doc.mainLimit")}: ${localized(main.limits[0], language)}`
        : "",
  ]
    .filter(Boolean)
    .join(" ");

  const context = [t("report.analysis"), saved.id, doc.design, doc.period, doc.date].join(" · ");
  const blockProps = { results, findings: saved.findings, style: props.style, language, catalogSources };
  const versions = Object.entries(saved.fingerprint.data);

  return (
    <DocumentFrame props={props} label={t("report.doc.scientific")} title={doc.title} context={context}>
      <section className="report-col report-abstract" aria-labelledby="report-abstract">
        <h2 id="report-abstract">{t("report.doc.abstract")}</h2>
        <p>{abstract}</p>
      </section>

      {placeImage && (
        <div className="report-col report-study-area">
          <PlaceImage
            zone={saved.plan.zone}
            info={placeImage}
            title={t("report.doc.studyArea")}
            notes={numbering.placeNumber ? <NotesOf n={numbering.placeNumber} /> : null}
          />
        </div>
      )}

      <section className="report-col report-section" aria-labelledby="sci-data">
        <h2 id="sci-data">{t("report.doc.secData")}</h2>
        <div className="report-table-scroll">
          <table className="report-table dense">
            <thead>
              <tr>
                <th scope="col">{t("blocks.source")}</th>
                <th scope="col">{t("report.doc.colProduct")}</th>
                <th scope="col">{t("report.doc.colItems")}</th>
                <th scope="col">{t("report.doc.colPeriod")}</th>
                <th scope="col">{t("report.doc.colResolution")}</th>
              </tr>
            </thead>
            <tbody>
              {numbering.sources.map(({ source, period }, index) => {
                const supported = rows.find((item) => item.sources.some((entry) => entry.source === source.source && entry.url === source.url));
                return (
                  <tr key={`${source.source}-${source.url}`}>
                    <th scope="row">
                      {sourceDisplayName(source, results, catalogSources, language)}
                      <a className="fn" href={`#source-${index + 1}`}>
                        {index + 1}
                      </a>
                      <code className="sub">{source.source}</code>
                    </th>
                    <td>{[source.product, source.version].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{source.n_items !== undefined ? formatNumber(source.n_items, language) : "—"}</td>
                    <td>{period ? periodText(period, language) : "—"}</td>
                    <td>
                      {supported?.support?.resolution_m !== undefined ? resolutionText(supported.support.resolution_m, language) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="report-col report-section" aria-labelledby="sci-method">
        <h2 id="sci-method">{t("report.doc.secMethod")}</h2>
        <ol className="report-steps">
          {steps.map((step) => {
            const key = operationKey(step.operation);
            return (
              <li key={step.id}>
                <b>{key ? t(key) : step.operation}</b>{" "}
                <code>
                  {step.operation} v{step.version}
                </code>
                {paramsText(step.params) && <span className="sub">{paramsText(step.params)}</span>}
              </li>
            );
          })}
        </ol>
        {decided.length > 0 && (
          <>
            <h3>{t("report.doc.decided")}</h3>
            <ul>
              {decided.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="report-wide report-section" aria-labelledby="sci-results">
        <h2 id="sci-results">{t("report.doc.secResults")}</h2>
        <div className="report-table-scroll">
          <table className="report-table dense">
            <thead>
              <tr>
                <th scope="col">{t("report.doc.colVariable")}</th>
                <th scope="col">{t("report.doc.colEstimate")}</th>
                <th scope="col">{t("report.doc.colCI")}</th>
                <th scope="col">{t("report.doc.colStatistic")}</th>
                <th scope="col">p</th>
                <th scope="col">n</th>
                <th scope="col">{t("report.doc.colPeriod")}</th>
                <th scope="col">{t("report.doc.colEvidence")}</th>
                <th scope="col">{t("report.doc.colClass")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => {
                const level = resourceRow(item) ? { name: "blocks.estimate" as MessageKey } : evidenceOf(item);
                const parts = figureParts(item, units, language, t);
                return (
                  <tr key={item.id} className={item.absence && !noClearDifference(item) ? "absent" : undefined}>
                    <th scope="row">
                      {figureName(item, language, doc.variableLabel)}
                      <code className="sub">{item.id}</code>
                    </th>
                    <td>
                      <ValueCell item={item} />
                      {item.absence && <code className="sub">{item.absence.reason}</code>}
                    </td>
                    <td className="num">{parts.interval || "—"}</td>
                    <td className="wrap">{statisticOf(item) || "—"}</td>
                    <td className="num">{pOf(item) || "—"}</td>
                    <td className="num">{nOf(item) !== undefined ? formatNumber(nOf(item) as number, language) : "—"}</td>
                    <td className="num">{item.period ? periodText(item.period, language) : "—"}</td>
                    <td className="num">{level ? t(level.name) : (item.evidence_level ?? "—")}</td>
                    <td className="num">{DATA_CLASS[item.data_class] ? t(DATA_CLASS[item.data_class]) : item.data_class}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {doc.markedClasses.length > 0 && <DataClassLegend classes={doc.markedClasses} />}
      </section>

      <Figures doc={doc} props={props} compact={false} />

      <section className="report-col report-section" aria-labelledby="sci-diagnostics">
        <h2 id="sci-diagnostics">{t("report.doc.secDiagnostics")}</h2>
        {diagnosed.length === 0 && <p className="small text-muted">{t("report.doc.noDiagnostics")}</p>}
        {diagnosed.length > 0 && (
          <dl className="report-dl">
            {diagnosed.map((item) => (
              <div key={item.id}>
                <dt>
                  {figureName(item, language, doc.variableLabel)} <code>{item.id}</code>
                </dt>
                <dd>
                  {diagnosticEntries(item, language)
                    .map(([label, value]) => `${label}: ${value}`)
                    .join(" · ")}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {absences.length > 0 && (
          <>
            <h3>{t("report.doc.absences")}</h3>
            <dl className="report-dl">
              {absences.map((item) => (
                <div key={item.id}>
                  <dt>
                    {figureName(item, language, doc.variableLabel)} <code>{item.absence?.reason}</code>
                  </dt>
                  <dd>{absenceDetail(item, language, doc.variableLabel) || absenceLabel(item, t)}</dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </section>

      <section className="report-col report-section" aria-labelledby="sci-limits">
        <h2 id="sci-limits">{t("report.doc.secLimits")}</h2>
        <div className="report-boxes">
          <ReportBlocks blocks={doc.limits.length > 0 ? doc.limits : [{ block: "LimitsBox" }]} {...blockProps} />
        </div>
      </section>

      <section className="report-col report-section" aria-labelledby="sci-repro">
        <h2 id="sci-repro">{t("report.doc.secRepro")}</h2>
        <dl className="report-dl">
          <div>
            <dt>{t("report.doc.reproId")}</dt>
            <dd>
              <code>{saved.id}</code>
            </dd>
          </div>
          {(saved as { plan_fingerprint?: string }).plan_fingerprint && (
            <div>
              <dt>{t("report.doc.reproPlan")}</dt>
              <dd>
                <code>{(saved as { plan_fingerprint?: string }).plan_fingerprint}</code>
              </dd>
            </div>
          )}
          <div>
            <dt>{t("report.doc.reproResults")}</dt>
            <dd>
              <code>{saved.fingerprint.sha256}</code>
            </dd>
          </div>
          <div>
            <dt>{t("report.doc.reproCatalog")}</dt>
            <dd>
              <code>{saved.plan.catalog_version}</code>
            </dd>
          </div>
          {versions.length > 0 && (
            <div>
              <dt>{t("report.doc.reproData")}</dt>
              <dd>
                {versions.map(([source, version]) => (
                  <code key={source} className="chip-code">
                    {source} {version}
                  </code>
                ))}
              </dd>
            </div>
          )}
          <div>
            <dt>{t("report.doc.reproDate")}</dt>
            <dd>
              {doc.date} <code>{saved.created}</code>
            </dd>
          </div>
          {saved.plan.request?.text && (
            <div>
              <dt>{t("report.doc.reproQuestion")}</dt>
              <dd>{saved.plan.request.text}</dd>
            </div>
          )}
        </dl>
        <p className="small">
          {t("report.doc.reproHow")}{" "}
          <Link to={`/run/${encodeURIComponent(saved.id)}?replay=1`} className="text-signal underline">
            {t("report.doc.reproRerun")}
          </Link>
        </p>
      </section>

      {doc.norm.length > 0 && (
        <div className="report-col report-boxes print:hidden">
          <ReportBlocks blocks={doc.norm} {...blockProps} />
        </div>
      )}

      <SourcesSection
        saved={saved}
        results={results}
        sources={numbering.sources}
        catalogSources={catalogSources}
        placeImage={placeImage}
        placeNumber={numbering.placeNumber}
        date={doc.date}
        title={t("report.doc.references")}
      />
    </DocumentFrame>
  );
}
