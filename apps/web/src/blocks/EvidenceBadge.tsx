import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { useUnits } from "../units";
import { useVariableName } from "./catalog";
import { evidenceOf, figureName, fillText, isEstimate, referenced, Section, VARIABLES, type BlockProps } from "./shared";

// Each level paints its rule with a token: nothing measured is grey, a trend is water, a coincidence is
// the amber of caution, an association against a control is the signal, and the strongest are green.
const LEVEL_RULE: Record<string, string> = {
  E0: "border-line-strong",
  E1: "border-water",
  E2: "border-amber",
  E3: "border-signal",
  E4: "border-green",
  E5: "border-green",
};

const ESTIMATE = { name: "blocks.estimate", note: "blocks.estimateNote" } as const satisfies {
  name: MessageKey;
  note: MessageKey;
};

export function EvidenceBadge({ block, results, findings, language }: BlockProps) {
  const { t } = useI18n();
  const { units } = useUnits();
  const targets = referenced(block, results);
  // A finding carries one level: the level of the claim, not the lowest of every series it cites.
  const finding = findings.find((item) => item.id === block.finding);
  const levels = finding?.evidence_level
    ? [finding.evidence_level as string]
    : ([...new Set(targets.map((item) => item.evidence_level).filter(Boolean))] as string[]);
  // Figures computed from published coefficients are estimates, not descriptions with nothing
  // measured: E0 over estimates only reads as what they are.
  const cited = finding ? results.filter((item) => finding.results.includes(item.id)) : targets;
  const estimates = cited.length > 0 && cited.every(isEstimate);
  // One block for several results with the same level names the results it applies to.
  const name = useVariableName(VARIABLES);
  const appliesTo = (level: string | null) =>
    finding || targets.length < 2
      ? ""
      : [
          ...new Set(
            targets
              .filter((item) => (level === null ? isEstimate(item) : item.evidence_level === level && !isEstimate(item)))
              .map((item) => figureName(item, language, name)),
          ),
        ].join(" · ");
  const wording = (level: string) => evidenceOf(cited.find((item) => item.evidence_level === level) ?? cited[0], level);
  const sentence =
    fillText(block.text, results, language, units) ?? fillText(finding?.text, results, language, units);

  return (
    <Section title={t("blocks.evidence")}>
      {levels.length === 0 && !estimates && <p className="body text-muted">{t("blocks.noEvidence")}</p>}
      {estimates ? (
        <div className="flex flex-col gap-2 border-l-4 border-amber pl-10">
          <span className="label text-ink">{t(ESTIMATE.name)}</span>
          <p className="small text-muted">{t(ESTIMATE.note)}</p>
          {appliesTo(null) && (
            <p className="caption text-muted">
              {t("blocks.appliesTo")}: {appliesTo(null)}
            </p>
          )}
        </div>
      ) : (
        levels.map((level) => (
          <div key={level} className={`flex flex-col gap-2 border-l-4 pl-10 ${LEVEL_RULE[level] ?? "border-line-strong"}`}>
            <span className="label text-ink">{wording(level) ? t(wording(level)!.name) : level}</span>
            {wording(level) && <p className="small text-muted">{t(wording(level)!.note)}</p>}
            {appliesTo(level) && (
              <p className="caption text-muted">
                {t("blocks.appliesTo")}: {appliesTo(level)}
              </p>
            )}
          </div>
        ))
      )}
      {sentence && <p className="body-lg">{sentence}</p>}
    </Section>
  );
}
