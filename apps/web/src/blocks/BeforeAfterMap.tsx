import { useI18n } from "../i18n";
import { useUnits } from "../units";
import {
  Absence,
  DataClassTag,
  fillText,
  formatMeasure,
  localized,
  periodText,
  referenced,
  resolutionText,
  sourceDisplayName,
  Section,
  type BlockProps,
  type StepResult,
} from "./shared";
import { useCatalog } from "./catalog";
import { DataClassMark } from "./DataClassMark";

function fileWindow(item: StepResult): "before" | "after" | null {
  const window = (item.method?.params as { window?: unknown } | undefined)?.window;
  return window === "before" || window === "after" ? window : null;
}

function frameRank(item: StepResult): number {
  const window = fileWindow(item);
  return window === "before" ? 0 : window === "after" ? 1 : 2;
}

// The before and after of an impact. The report is read on paper: the interactive raster is drawn
// by the panel, which owns the map and its colour bar, and this block states the two windows, the
// shared colour range and the sources, and links to it. No tile server, no second map engine and no
// invented image in print.
export function BeforeAfterMap({ block, results, findings, language }: BlockProps) {
  const { t } = useI18n();
  const catalog = useCatalog();
  const { units } = useUnits();
  const targets = referenced(block, results).filter((item) => item.type === "layer");
  const windows = targets.filter((item) => fileWindow(item) !== null);
  // Two windowed layers are the before and after; a plain pair is taken as one when no window is
  // recorded. Anything else belongs to the panel, which draws it frame by frame.
  const frames = [...(windows.length > 0 ? windows : targets.length === 2 ? targets : [])]
    .sort(
      (left, right) =>
        frameRank(left) - frameRank(right) || (left.period?.start ?? "").localeCompare(right.period?.start ?? ""),
    )
    .slice(0, 2);
  const absences = [
    ...new Map(targets.filter((item) => item.absence).map((item) => [JSON.stringify(item.absence), item])).values(),
  ];
  const ranges = frames
    .map((item) => item.layer?.range)
    .filter((range): range is [number, number] => Array.isArray(range) && range.length === 2);
  const range: [number, number] | undefined =
    ranges.length === 0
      ? undefined
      : [Math.min(...ranges.map((edge) => edge[0])), Math.max(...ranges.map((edge) => edge[1]))];
  const low = range ? formatMeasure(range[0], frames[0]?.unit, language, units, undefined, false) : null;
  const high = range ? formatMeasure(range[1], frames[0]?.unit, language, units, undefined, false) : null;
  const limits = [...new Set(targets.flatMap((item) => (item.limits ?? []).map((limit) => localized(limit, language))))];
  const analysis = targets.find((item) => fileWindow(item) !== null)?.analysis ?? targets[0]?.analysis;
  const finding = findings.find((candidate) => candidate.id === block.finding);
  const sentence =
    fillText(block.text, results, language, units) ?? fillText(finding?.text, results, language, units);

  return (
    <Section title={t("design.impact")}>
      {frames.length === 0 && absences.length === 0 && <p className="body text-muted">{t("panel.layerNoFrames")}</p>}
      {frames.length === 0 && absences.map((item) => <Absence key={item.id} item={item} />)}
      {frames.length > 0 && (
        <>
          <div className="grid gap-12 sm:grid-cols-2">
            {frames.map((item) => {
              const window = fileWindow(item);
              const label = window === "before" ? t("panel.before") : window === "after" ? t("panel.after") : item.variable;
              return (
                <figure key={item.id} className="flex flex-col gap-6 rounded-lg border border-line bg-paper p-12">
                  <figcaption className="label text-muted">
                    {label}
                    {item.period && (
                      <span className="caption ml-6 text-muted">
                        {periodText(item.period, language)}
                      </span>
                    )}
                  </figcaption>
                  {item.absence ? (
                    <Absence item={item} />
                  ) : (
                    <>
                      {low && high && (
                        <p className="data">
                          {t("panel.layerRange")}: {low.text} {low.unit} – {high.text} {high.unit}
                          <DataClassMark value={item.data_class} />
                        </p>
                      )}
                      <DataClassTag value={item.data_class} />
                      <p className="caption text-muted">
                        {[
                          ...new Set(
                            item.sources
                              .filter((source) => source.source !== "none")
                              .map((source) => sourceDisplayName(source, results, catalog?.sources, language)),
                          ),
                        ].join(" · ")}
                      </p>
                      {item.support?.resolution_m !== undefined && (
                        <p className="caption text-muted">
                          {t("blocks.resolution")}:{" "}
                          {resolutionText(item.support.resolution_m, language)}
                        </p>
                      )}
                    </>
                  )}
                </figure>
              );
            })}
          </div>
          {limits.length > 0 && (
            <div className="flex flex-col gap-2">
              <span className="label text-muted">{t("blocks.limits")}</span>
              <ul className="small flex list-disc flex-col gap-2 pl-16 text-muted">
                {limits.map((limit) => (
                  <li key={limit}>{limit}</li>
                ))}
              </ul>
            </div>
          )}
          {analysis && (
            <a
              href={`/a/${encodeURIComponent(analysis)}/panel`}
              className="small w-fit text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              {t("report.back")}
            </a>
          )}
        </>
      )}
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
