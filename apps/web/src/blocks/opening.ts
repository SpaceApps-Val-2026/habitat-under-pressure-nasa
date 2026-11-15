import type { analysis } from "../contracts";
import { DESIGN } from "../design";
import type { MessageKey } from "../i18n/messages";
import { absenceDetail, absenceLabel, figureName, noClearDifference, resourceRow, type StepResult } from "./shared";

type SavedAnalysis = analysis.SavedAnalysis;


// The opening without the server's editorial piece, one rule for every design. The kicker is the
// person's question; the headline is composed from the design, the variables the results speak about
// and the place, so the question is never written twice. The lead is the first finding the web can fill; without one, the
// first result that says why there is no figure ("no clear difference", a missing source).
// What a report is about, in names: the estimated resources of an estimate, else the variables its
// results speak about (never the context ones of an estimate), else the plan's.
export function subjectNames(
  saved: SavedAnalysis,
  results: StepResult[],
  language: string,
  variableLabel: (id: string) => string,
): string[] {
  const estimates = results.filter((item) => !item.absence && resourceRow(item) !== null);
  const spoken = [
    ...new Set(
      (estimates.length > 0 ? estimates : results.filter((item) => !item.absence && item.type !== "layer")).map((item) =>
        figureName(item, language, variableLabel),
      ),
    ),
  ];
  return (spoken.length > 0 ? spoken : saved.plan.variables.map(variableLabel)).slice(0, 2);
}

export function placeName(name: string): string {
  return name.split(",")[0]?.trim() || name;
}

export function fallbackOpening({
  saved,
  results,
  sentences,
  language,
  t,
  variableLabel,
}: {
  saved: SavedAnalysis;
  results: StepResult[];
  sentences: string[];
  language: string;
  t: (key: MessageKey) => string;
  variableLabel: (id: string) => string;
}): { headline: string; lead: string; kicker: string } {
  const question = saved.plan.request?.text?.trim() ?? "";
  // An estimate's question is about the estimated resources: they name the headline, not the context.
  const estimates = results.filter((item) => !item.absence && resourceRow(item) !== null);
  const spoken = [
    ...new Set(
      (estimates.length > 0 ? estimates : results.filter((item) => !item.absence && item.type !== "layer")).map((item) =>
        figureName(item, language, variableLabel),
      ),
    ),
  ];
  const variables = (spoken.length > 0 ? spoken : saved.plan.variables.map(variableLabel)).slice(0, 2);
  const design = DESIGN[saved.plan.design] ? t(DESIGN[saved.plan.design]) : "";
  const composed = t("report.headline")
    .replace("{design}", design)
    .replace("{variables}", variables.join(language === "es" ? " y " : " and "))
    .replace("{place}", placeName(saved.plan.zone.name))
    .replace(/^:\s*/, "");
  const capitalise = (text: string) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);
  const missing =
    results.find((item) => noClearDifference(item)) ?? results.find((item) => item.absence && item.type !== "layer");
  const absenceLead = missing
    ? [absenceLabel(missing, t), absenceDetail(missing, language, variableLabel)].filter(Boolean).join(": ")
    : "";
  // The kicker is the person's own question, in their words; without one, the design and the place.
  const kicker = question || [design, placeName(saved.plan.zone.name)].filter(Boolean).join(" · ");
  return { headline: capitalise(composed), lead: sentences[0] ?? absenceLead, kicker };
}

// The editorial opening per style as the server writes it (contract `story.editorial`). `body`, `closing`
// and `featured` are optional and newer: the nudo, the desenlace and the result whose figure leads.
export type EditorialPiece = {
  headline?: Record<string, string>;
  lead?: Record<string, string>;
  meaning?: Record<string, string>;
  cannot_say?: Record<string, string>;
  body?: Record<string, string>[];
  closing?: Record<string, string>;
  featured?: string;
  results?: string[];
};

export function editorialOf(saved: SavedAnalysis, style: string): EditorialPiece | undefined {
  const story = saved.story as unknown as { editorial?: Record<string, EditorialPiece> } | undefined;
  return story?.editorial?.[style];
}
