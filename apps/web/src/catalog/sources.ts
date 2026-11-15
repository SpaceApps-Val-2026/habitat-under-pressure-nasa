// The catalogue is the one place that holds each source's license and the attribution line its
// holder asks for, in both languages. These helpers read it the way the interface needs: text by
// language, and the bracketed markers filled only when there is a datum.
import type { catalog } from "../contracts";

// The catalogue's source entry, as the generated types declare it.
export type CatalogSource = catalog.CatalogSourcesVariablesAndOperations["sources"][number];

export type SourceText = string | Record<string, string | undefined>;

export function sourceText(value: SourceText | undefined, language: string): string | undefined {
  if (!value) return undefined;
  if (typeof value === "string") return value;
  return value[language] ?? value.en ?? undefined;
}

export function catalogSource(sources: CatalogSource[] | undefined, id: string | undefined) {
  return id ? sources?.find((source) => source.id === id) : undefined;
}

// One year when both ends match; otherwise the range, as the citation writes it.
export function periodYears(period: { start: string; end: string } | undefined): string | undefined {
  if (!period?.start || !period?.end) return undefined;
  const start = period.start.slice(0, 4);
  const end = period.end.slice(0, 4);
  return start === end ? start : `${start}–${end}`;
}

// `[year]` and `[año]` become the data's years, or leave with their space; `[page]` and `[página]`
// become the reference page when there is one. Anything else between brackets stays as written.
// `[date]` and `[fecha]` are the access date: the day the analysis was created (Sara's decision).
export function fillMarkers(text: string, values: { years?: string; page?: string; date?: string }): string {
  let filled = text;
  if (values.date) filled = filled.replaceAll(/\[\s*(?:date|fecha)\s*\]/gi, values.date);
  filled = values.page
    ? filled.replaceAll(/\[\s*(?:page|página)\s*\]/gi, values.page)
    : filled;
  return values.years
    ? filled.replaceAll(/\[\s*(?:year|año)\s*\]/gi, values.years)
    : filled.replace(/[ \t]*\[\s*(?:year|año)\s*\]/gi, "");
}

export function sourceName(
  ref: { source: string },
  sources: CatalogSource[] | undefined,
  language: string,
): string {
  const found = catalogSource(sources, ref.source);
  return (found && sourceText(found.name as SourceText, language)) || ref.source;
}

export function sourceLicense(
  ref: { source: string; license?: string; page?: string },
  sources: CatalogSource[] | undefined,
  language: string,
  period?: { start: string; end: string },
  date?: string,
): string | undefined {
  const found = catalogSource(sources, ref.source);
  const license = sourceText(found?.license, language) ?? ref.license;
  return license ? fillMarkers(license, { years: periodYears(period), page: ref.page, date }) : undefined;
}

export function sourceCitation(
  ref: { source: string; page?: string },
  sources: CatalogSource[] | undefined,
  language: string,
  period?: { start: string; end: string },
  date?: string,
): string | undefined {
  const citation = sourceText(catalogSource(sources, ref.source)?.citation, language);
  return citation ? fillMarkers(citation, { years: periodYears(period), page: ref.page, date }) : undefined;
}
