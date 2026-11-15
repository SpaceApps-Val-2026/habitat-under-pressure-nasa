import { useI18n } from "../i18n";
import { useUnits } from "../units";
import {
  Absence,
  DATA_CLASS,
  DataClassTag,
  formatMeasure,
  formatP,
  fillText,
  localized,
  localizedUnit,
  referenced,
  Section,
  VARIABLES,
  periodText,
  resolutionText,
  sourceDisplayName,
  type BlockProps,
  type StepResult,
} from "./shared";
import { useCatalog, useVariableName } from "./catalog";
import { DataClassMark } from "./DataClassMark";

const FIELDS = ["input", "variable", "unit", "slope", "p_adjusted", "direction_assertable"] as const;

type ScanRow = {
  variable: string;
  unit: string;
  slope: number | null;
  p: number | null;
  assertable: boolean | null;
  item?: StepResult;
};

function readScan(item: StepResult): ScanRow[] | null {
  const table = item.table;
  if (!table) return null;
  const columns = table.columns.map(String);
  if (!FIELDS.every((field) => columns.filter((column) => column === field).length === 1)) return null;
  const at = (field: string) => columns.indexOf(field);
  const rows: ScanRow[] = [];
  for (const row of table.rows) {
    if (!Array.isArray(row) || row.length !== columns.length) return null;
    const variable = row[at("variable")];
    const unit = row[at("unit")];
    if (typeof variable !== "string") return null;
    const slope = row[at("slope")];
    const p = row[at("p_adjusted")];
    const assertable = row[at("direction_assertable")];
    rows.push({
      variable,
      unit: typeof unit === "string" ? unit : item.unit ?? "",
      slope: typeof slope === "number" && Number.isFinite(slope) ? slope : null,
      p: typeof p === "number" && Number.isFinite(p) ? p : null,
      assertable: typeof assertable === "boolean" ? assertable : null,
    });
  }
  return rows;
}

// The table names the input series by id; the per-variable test results carry the reason a
// direction cannot be asserted, so they are matched by variable and operation.
function detailFor(rows: ScanRow[], item: StepResult, results: StepResult[]): ScanRow[] {
  return rows.map((row) => ({
    ...row,
    item: results.find(
      (candidate) =>
        candidate.variable === row.variable &&
        candidate.method?.operation === item.method?.operation &&
        (candidate.absence !== undefined || candidate.value !== undefined),
    ),
  }));
}

export function hasScanMatrix(item: StepResult): boolean {
  return readScan(item) !== null;
}

// The scan of several variables at once: one cell per variable with the signed change, its test's
// p value and, in words, why a direction cannot be asserted when that is the case.
export function ScanMatrix({ block, results, findings, language, notes }: BlockProps) {
  const { t } = useI18n();
  const variableLabel = useVariableName(VARIABLES);
  const catalog = useCatalog();
  const { units } = useUnits();
  const target = referenced(block, results).find((item) => item.type === "table" && readScan(item) !== null);
  const rows = target ? detailFor(readScan(target) ?? [], target, results) : [];
  const finding = findings.find((candidate) => candidate.id === block.finding);
  const sentence =
    fillText(block.text, results, language, units) ?? fillText(finding?.text, results, language, units);
  const sources = [
    ...new Set(
      (target?.sources ?? [])
        .filter((source) => source.source !== "none")
        .map((source) => sourceDisplayName(source, results, catalog?.sources, language)),
    ),
  ];
  const limits = [...new Set((target?.limits ?? []).map((limit) => localized(limit, language)))];

  return (
    <Section title={t("design.scan")}>
      {target?.absence && <Absence item={target} />}
      {!target && <p className="body text-muted">{t("blocks.noValue")}</p>}
      {target && !target.absence && rows.length === 0 && (
        <p className="body text-muted">{t("blocks.noValue")}</p>
      )}
      {rows.length > 0 && (
        <>
          <div className="grid gap-12 md:grid-cols-2">
            {rows.map((row) => {
              const value = row.item?.value ?? row.slope;
              const measured =
                value === null || value === undefined ? null : formatMeasure(value, row.item?.unit ?? row.unit, language, units, target?.method?.operation, true, 3);
              const interval = row.item?.interval;
              const intervalUnit = row.item?.unit ?? row.unit;
              const low = interval ? formatMeasure(interval.low, intervalUnit, language, units, target?.method?.operation, false, 3) : null;
              const high = interval ? formatMeasure(interval.high, intervalUnit, language, units, target?.method?.operation, false, 3) : null;
              const name = variableLabel(row.variable);
              const dataClass = row.item?.data_class ?? target?.data_class;
              const knownClass = dataClass && dataClass in DATA_CLASS ? dataClass : undefined;
              const percent = interval ? new Intl.NumberFormat(language, { style: "percent" }).format(interval.level) : "";
              return (
                <article key={row.variable} className="flex flex-col gap-6 rounded-lg border border-line bg-paper p-12">
                  <h3 className="label text-muted">{name}</h3>
                  {row.item?.absence ? (
                    <Absence item={row.item} />
                  ) : (
                    <>
                      <p className="figure">
                        {measured ? measured.text : t("blocks.noValue")}{" "}
                        {measured && (
                          <span className="whitespace-nowrap">
                            <span className="small text-muted">{localizedUnit(row.item?.unit ?? row.unit, units, language)}</span>
                            <DataClassMark value={knownClass} />
                          </span>
                        )}
                      </p>
                      {low && high && (
                        <p className="data text-muted">
                          {[t("blocks.interval"), percent].filter(Boolean).join(" ")}: {low.text} – {high.text}{" "}
                          {localizedUnit(row.item?.unit ?? row.unit, units, language)}
                        </p>
                      )}
                    </>
                  )}
                  {row.p !== null && (
                    <p className="data">
                      {formatP(row.p, language).startsWith("<") ? `p ${formatP(row.p, language)}` : `p = ${formatP(row.p, language)}`}
                    </p>
                  )}
                  {row.assertable === false && !row.item?.absence && (
                    <p className="small text-amber-ink">{t("blocks.noClearDifference")}</p>
                  )}
                  {row.assertable === null && row.p !== null && (
                    <p className="small text-muted">{t("blocks.noValue")}</p>
                  )}
                  {knownClass && <DataClassTag value={knownClass} />}
                </article>
              );
            })}
          </div>
          <p className="caption text-muted">
            {[
              sources.join(" · "),
              target?.period ? `${t("blocks.period")}: ${periodText(target.period, language)}` : "",
              target?.support?.resolution_m !== undefined
                ? `${t("blocks.resolution")}: ${resolutionText(target.support.resolution_m, language)}`
                : "",
            ]
              .filter(Boolean)
              .join(" · ")}
            {notes}
          </p>
          {limits.length > 0 && (
            <ul className="small flex list-disc flex-col gap-2 pl-16 text-muted">
              {limits.map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          )}
        </>
      )}
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
