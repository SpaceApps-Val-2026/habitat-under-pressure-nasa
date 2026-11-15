import { useI18n } from "../i18n";
import { useUnits } from "../units";
import { Absence, fillText, localized, referenced, Section, type BlockProps } from "./shared";

export function LimitsBox({ block, results, language }: BlockProps) {
  const { t } = useI18n();
  const { units } = useUnits();
  const targets = referenced(block, results);
  const limits = [...new Set(targets.flatMap((item) => (item.limits ?? []).map((limit) => localized(limit, language))))];
  const absences = [];
  const seen = new Set<string>();
  for (const item of targets) {
    if (!item.absence) continue;
    const key = [item.absence.reason, item.absence.detail?.en ?? "", item.absence.detail?.es ?? ""].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    absences.push(item);
  }
  const title = fillText(block.text, targets, language, units) ?? t("blocks.limits");

  return (
    <Section title={title}>
      <ul className="flex list-disc flex-col gap-4 pl-16">
        <li className="body">{t("blocks.notCausal")}</li>
        {limits.map((limit, index) => (
          <li key={index} className="body">
            {limit}
          </li>
        ))}
      </ul>
      {absences.map((item) => (
        <Absence key={item.id} item={item} />
      ))}
      {limits.length === 0 && absences.length === 0 && <p className="small text-muted">{t("blocks.noLimits")}</p>}
    </Section>
  );
}
