import { useI18n } from "../i18n";
import { useUnits } from "../units";
import { useVariableName } from "./catalog";
import { figureName, formatMeasure, localizedUnit, resourceRow, VARIABLES, type StepResult } from "./shared";

// The figure of an estimate: for each resource, its range from published coefficients (low to high) with
// the central estimate marked, on its own scale from zero. One generic drawing for any estimate table.
export function EstimateRange({ items }: { items: StepResult[] }) {
  const { language } = useI18n();
  const { units } = useUnits();
  const name = useVariableName(VARIABLES);
  const rows = items.flatMap((item) => {
    const row = resourceRow(item);
    return row && item.value !== undefined ? [{ item, row, value: item.value }] : [];
  });
  if (rows.length === 0) return null;
  const width = 640;
  const left = 8;
  const right = 8;
  const rowHeight = 86;
  const format = (value: number, item: StepResult) =>
    formatMeasure(value, item.unit, language, units, undefined, false).text;

  return (
    <svg className="report-chart" viewBox={`0 0 ${width} ${rows.length * rowHeight}`} role="img" aria-label={rows.map(({ item }) => figureName(item, language, name)).join(" · ")}>
      {rows.map(({ item, row, value }, index) => {
        const top = index * rowHeight;
        const max = row.high * 1.08 || 1;
        const x = (number: number) => left + (Math.max(0, number) / max) * (width - left - right);
        const unit = localizedUnit(item.unit, units, language);
        return (
          <g key={item.id} transform={`translate(0 ${top})`}>
            <text className="note strong" x={left} y={16}>
              {figureName(item, language, name)}
              {unit ? ` · ${unit}` : ""}
            </text>
            <line className="grid" x1={left} x2={width - right} y1={44} y2={44} />
            <line className="range" x1={x(row.low)} x2={x(row.high)} y1={44} y2={44} />
            <line className="end" x1={x(row.low)} x2={x(row.low)} y1={36} y2={52} />
            <line className="end" x1={x(row.high)} x2={x(row.high)} y1={36} y2={52} />
            <circle className="mid" cx={x(value)} cy={44} r={6} />
            <text className="tick" x={x(row.low)} y={70} textAnchor="start">
              {format(row.low, item)}
            </text>
            <text className="tick strong" x={x(value)} y={28} textAnchor="middle">
              {format(value, item)}
            </text>
            <text className="tick" x={x(row.high)} y={70} textAnchor="end">
              {format(row.high, item)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
