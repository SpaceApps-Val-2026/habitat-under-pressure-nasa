// What a list of analyses shows for each one (cover and library): a readable title, a short place and the
// question the person asked, read from the saved plan.
import type { AnalysisSummary } from "../api/client";
import type { plan } from "../contracts";

export type ReadPlan = (id: string) => Promise<{ plan?: plan.AnalysisPlan | null }>;

// What an analysis' saved plan offers a list: the question the person asked (plan.request.text), and what a
// template question needs (its first variable and its place) when it was made by the form.
export type Asked = { text: string; variable: string | null; place: string };

// The first part of a geocoder name («Algemesí, La Ribera Alta, Valencia…» → «Algemesí»).
export function shortPlace(name: string | undefined | null): string {
  return (name ?? "").split(",")[0].trim();
}

// Town and region of a geocoder name: its first part and the last named part before the country
// («Algemesí, La Ribera Alta, Valencia, Comunidad Valenciana, 46680, España» → «Algemesí, Comunidad Valenciana»).
export function placeLine(name: string | undefined | null): string {
  const parts = (name ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part && !/\d/.test(part));
  if (parts.length <= 2) return parts.join(", ");
  return `${parts[0]}, ${parts[parts.length - 2]}`;
}

// The title a list shows: the person's question; else the saved title cut to its place, with the design
// («Peñíscola: Vegetation greenness, Land surface temperature, …» → «Peñíscola · Trend»).
export function cardTitle(
  item: AnalysisSummary,
  asked: Asked | undefined,
  language: string,
  designLabel: string,
  untitled: string,
): string {
  if (asked?.text) return asked.text;
  const saved = item.title ? (item.title[language] ?? item.title.en ?? "") : "";
  if (!saved) return shortPlace(item.zone?.name) ? `${shortPlace(item.zone?.name)} · ${designLabel}` : untitled;
  const head = saved.split(":")[0].trim();
  return head && head !== saved ? `${head} · ${designLabel}` : saved;
}

// Reads the saved plans a few at a time, so a long list renders at once and its titles improve as the
// questions arrive. Returns a function that stops it.
export function readAsked(
  items: AnalysisSummary[],
  read: ReadPlan,
  onRead: (id: string, asked: Asked) => void,
  limit = 4,
): () => void {
  let live = true;
  let next = 0;
  const worker = async () => {
    while (live && next < items.length) {
      const item = items[next];
      next += 1;
      const saved = await read(item.id).then((answer) => answer?.plan ?? null).catch(() => null);
      if (!live || !saved) continue;
      onRead(item.id, {
        text: (saved.request?.text ?? "").trim(),
        variable: saved.variables?.[0] ?? null,
        place: shortPlace(saved.zone?.name ?? item.zone?.name),
      });
    }
  };
  for (let index = 0; index < Math.min(limit, items.length); index += 1) void worker();
  return () => {
    live = false;
  };
}
