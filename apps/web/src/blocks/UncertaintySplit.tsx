import { useI18n } from "../i18n";
import { useUnits } from "../units";
import { Absence, fillNumber, localized, referenced, Section, type BlockProps, type StepResult } from "./shared";

const FIELDS = ["horizon", "model_variance", "scenario_variance"] as const;

type SplitRow = { horizon: number; model: number; scenario: number; models?: string[]; scenarios?: string[] };

function readSplit(item: StepResult): { rows: SplitRow[]; unit: string } | null {
  const table = item.table;
  if (!table) return null;
  const columns = table.columns.map(String);
  if (!FIELDS.every((field) => columns.filter((column) => column === field).length === 1)) return null;
  const at = (field: string) => columns.indexOf(field);
  const models = at("models");
  const scenarios = at("scenarios");
  const rows: SplitRow[] = [];
  for (const row of table.rows) {
    if (!Array.isArray(row) || row.length !== columns.length) return null;
    const [horizon, model, scenario] = FIELDS.map((field) => row[at(field)]);
    if (
      typeof horizon !== "number" ||
      !Number.isInteger(horizon) ||
      typeof model !== "number" ||
      typeof scenario !== "number" ||
      !Number.isFinite(model) ||
      !Number.isFinite(scenario) ||
      model < 0 ||
      scenario < 0
    ) {
      return null;
    }
    const names = (index: number) => {
      const value = index === -1 ? undefined : row[index];
      return Array.isArray(value) ? value.map(String) : undefined;
    };
    rows.push({ horizon, model, scenario, models: names(models), scenarios: names(scenarios) });
  }
  return { rows, unit: item.unit ?? "" };
}

export function hasVarianceSplit(item: StepResult): boolean {
  return readSplit(item) !== null;
}

// The variance split by horizon: how much of the spread comes from the models and how much from
// the scenarios. The counts travel with the numbers, because they are what makes them indicative.
export function UncertaintySplit({ block, results }: BlockProps) {
  const { t, language } = useI18n();
  const { units } = useUnits();
  const target = referenced(block, results).find(
    (item) => item.type === "table" && readSplit(item) !== null,
  );
  const split = target ? readSplit(target) : null;
  const squared = (split?.unit ?? "").includes("°C^2");
  const factor = units === "imperial" && squared ? (9 / 5) ** 2 : 1;
  const unit = units === "imperial" && squared ? split?.unit.replace("°C^2", "°F^2") : split?.unit;
  const counts = split?.rows[0];
  const count = (names: string[] | undefined) => (names && names.length > 0 ? names.length : undefined);

  return (
    <Section title={[t("blocks.split"), unit].filter(Boolean).join(" · ")}>
      {!target && <p className="body text-muted">{t("blocks.splitNoRows")}</p>}
      {target?.absence && <Absence item={target} />}
      {split && split.rows.length === 0 && <p className="body text-muted">{t("blocks.splitNoRows")}</p>}
      {split && split.rows.length > 0 && (
        <>
          <div className="w-full overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-line">
                  <th scope="col" className="label py-6 text-muted">
                    {t("blocks.horizon")}
                  </th>
                  <th scope="col" className="label py-6 text-muted">
                    {t("blocks.modelVariance")}
                  </th>
                  <th scope="col" className="label py-6 text-muted">
                    {t("blocks.scenarioVariance")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {split.rows.map((row) => (
                  <tr key={row.horizon} className="border-b border-line">
                    <th scope="row" className="data py-6 text-left">
                      {row.horizon}
                    </th>
                    <td className="data py-6 tabular-nums">{fillNumber(row.model * factor, language)}</td>
                    <td className="data py-6 tabular-nums">{fillNumber(row.scenario * factor, language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="caption text-muted">
            {[
              count(counts?.models) !== undefined
                ? `${count(counts?.models)} ${t("blocks.fill.models")}`
                : "",
              count(counts?.scenarios) !== undefined
                ? `${count(counts?.scenarios)} ${t("blocks.fill.scenarios")}`
                : "",
              t("blocks.varianceNote"),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {target?.limits && target.limits.length > 0 && (
            <ul className="small flex list-disc flex-col gap-2 pl-16 text-muted">
              {[...new Set(target.limits.map((limit) => localized(limit, language)))].map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  );
}
