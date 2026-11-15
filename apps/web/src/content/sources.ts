// The catalogue's sources, as the footer and «What is this?» show them: grouped by the agency behind them,
// with a link only when the catalogue's address is a page a person can read (not an API endpoint or a file).
export type CatalogSourceLike = {
  id: string;
  name: string;
  provider?: string;
  access?: { url?: string };
};

export type Agency = "nasa" | "esa" | "other";

export function agencyOf(source: CatalogSourceLike): Agency {
  const text = `${source.provider ?? ""} ${source.name}`;
  if (/\bNASA\b/.test(text)) return "nasa";
  if (/\bESA\b|European Space Agency|Copernicus|Sentinel/i.test(text)) return "esa";
  return "other";
}

const NOT_A_PAGE = /\/api\/|\/stac\/|\.zip$|\.cgi$|\/zarr$|\/wms$|ImageServer$|\/ftp\/|\/temporal\//i;

export function pageOf(source: CatalogSourceLike): string | null {
  const url = source.access?.url;
  if (!url || !/^https:\/\//.test(url) || NOT_A_PAGE.test(url)) return null;
  return url;
}

export function byAgency(sources: CatalogSourceLike[]): Record<Agency, CatalogSourceLike[]> {
  const groups: Record<Agency, CatalogSourceLike[]> = { nasa: [], esa: [], other: [] };
  for (const source of sources) groups[agencyOf(source)].push(source);
  return groups;
}
