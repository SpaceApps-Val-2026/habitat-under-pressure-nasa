import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getCatalog } from "../api/client";
import brandLogo from "../assets/logo.svg";
import { byAgency, pageOf, type Agency, type CatalogSourceLike } from "../content/sources";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "./page";

// Documented in README.md («Team repository»).
const REPOSITORY = "https://github.com/SpaceApps-Val-2026/Habitat-Under-Pressure";

const AGENCY: Record<Agency, MessageKey> = {
  nasa: "footer.agency.nasa",
  esa: "footer.agency.esa",
  other: "footer.agency.other",
};

// `file` serves both themes; `dark` / `light` are the owner's versions for each background.
export type LogoEntry = { key: string; alt: string; file?: string; dark?: string; light?: string };

type LoadCatalog = () => Promise<{ sources?: CatalogSourceLike[] }>;
type LoadLogos = () => Promise<LogoEntry[]>;

// The owners and missions whose logo can stand over a group of sources, found in each source's provider and name.
const MARKS: { key: string; test: RegExp }[] = [
  { key: "nasa", test: /\bNASA\b/ },
  { key: "usgs", test: /\bUSGS\b/ },
  { key: "gpm", test: /\bGPM\b/ },
  { key: "smap", test: /\bSMAP\b/ },
  { key: "esa", test: /\bESA\b|European Space Agency/ },
  { key: "copernicus", test: /Copernicus/ },
  { key: "copernicus-ems", test: /Copernicus EMS|Emergency Management Service/ },
  { key: "eox", test: /\bEOX\b/ },
  { key: "ec", test: /European Commission/ },
  { key: "eea", test: /European Environment Agency/ },
  { key: "ine-es", test: /Instituto Nacional de Estadística/ },
  { key: "chj", test: /Confederación Hidrográfica del Júcar/ },
  { key: "miteco", test: /\bMITECO\b/ },
  { key: "boe", test: /Boletín Oficial del Estado/ },
  { key: "gva", test: /Generalitat Valenciana/ },
  { key: "icv", test: /Institut Cartogràfic Valencià/ },
];

function isEntry(entry: unknown): entry is LogoEntry {
  const value = entry as Partial<LogoEntry> | null;
  const files = [value?.file, value?.dark, value?.light];
  return (
    typeof value?.key === "string" &&
    typeof value.alt === "string" &&
    files.some((file) => typeof file === "string") &&
    files.every((file) => file === undefined || typeof file === "string")
  );
}

// Only the logos listed under "show" in logos.json; "held" ones wait for permission (see its README).
async function loadLogos(): Promise<LogoEntry[]> {
  if (typeof fetch !== "function") return [];
  const response = await fetch("/logos/logos.json");
  if (!response.ok) return [];
  const manifest = (await response.json()) as { show?: unknown } | null;
  return Array.isArray(manifest?.show) ? manifest.show.filter(isEntry) : [];
}

// Logo heights: the event a step larger than the agencies and missions. On a plate the image loses the
// plate's padding so both keep the same outer height.
const SIZE = {
  mark: { image: "h-22 max-w-[110px]", plated: "h-[18px] max-w-[98px]", plate: "px-6 py-2" },
  event: { image: "h-40 max-w-[180px]", plated: "h-[32px] max-w-[164px]", plate: "px-8 py-4" },
};
// A version drawn for the other background sits on a small constant surface instead of vanishing:
// a dark-background logo on the orbit plate, a light-background one on the pale plate. Colours untouched.
const PLATE = { dark: "bg-orbit", light: "bg-orbit-ink" };

// A logo when one is listed and loads; otherwise the name as a text mark. With a version per theme each
// theme gets its own; with one for the other background only, that one goes on a plate.
function Mark({
  name,
  logo,
  className = "",
  size = "mark",
}: {
  name: string;
  logo?: LogoEntry;
  className?: string;
  size?: keyof typeof SIZE;
}) {
  const [failed, setFailed] = useState(false);
  if (!logo || failed) return <span className={className}>{name}</span>;
  const image = (file: string, display: string, plated = false) => (
    <img
      src={`/logos/${encodeURIComponent(file)}`}
      alt={logo.alt}
      title={logo.alt}
      className={`${display} w-auto object-contain ${plated ? SIZE[size].plated : SIZE[size].image}`}
      onError={() => setFailed(true)}
    />
  );
  const plate = (file: string, made: keyof typeof PLATE, display: string) => (
    <span className={`${display} items-center rounded-xs ${SIZE[size].plate} ${PLATE[made]}`}>{image(file, "block", true)}</span>
  );
  const light = logo.light ?? logo.file;
  const dark = logo.dark ?? logo.file;
  if (light && light === dark) return image(light, "block");
  return (
    <>
      {light ? image(light, "block dark:hidden") : dark && plate(dark, "dark", "inline-flex dark:hidden")}
      {dark ? image(dark, "hidden dark:block") : light && plate(light, "light", "hidden dark:inline-flex")}
    </>
  );
}

export function Footer({ catalog = getCatalog, logos = loadLogos }: { catalog?: LoadCatalog; logos?: LoadLogos }) {
  const { t } = useI18n();
  const [sources, setSources] = useState<CatalogSourceLike[]>([]);
  const [listed, setListed] = useState<LogoEntry[]>([]);

  useEffect(() => {
    let live = true;
    catalog()
      .then((value) => {
        if (live && Array.isArray(value?.sources)) setSources(value.sources);
      })
      .catch(() => {});
    logos()
      .then((value) => {
        if (live) setListed(value);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [catalog, logos]);

  const logo = (key: string) => listed.find((entry) => entry.key === key);
  const groups = byAgency(sources);
  // The listed logos of the owners and missions behind a group's sources, in MARKS order.
  const marksOf = (group: CatalogSourceLike[]) =>
    MARKS.filter(({ test }) => group.some((source) => test.test(`${source.provider ?? ""} ${source.name}`)))
      .map(({ key }) => logo(key))
      .filter((entry): entry is LogoEntry => entry !== undefined);
  const link =
    "underline decoration-line-strong underline-offset-4 hover:text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

  return (
    <footer aria-label={t("footer.label")} className="mt-40 border-t border-line bg-paper text-ink print:hidden">
      <div className={`${PAGE} flex flex-col gap-22 py-28`}>
        {/* The same three columns as the «Data from» strip below, so both rows share one grid. */}
        <div className="grid gap-22 md:grid-cols-3 md:gap-x-40">
          <div className="flex items-start gap-12">
            <img src={brandLogo} alt="" className="size-40 shrink-0" />
            <div className="flex max-w-[44ch] flex-col gap-6">
              <p className="title-2">
                {t("brand.habitat")} {t("brand.underPressure")}
              </p>
              <p className="small text-muted">{t("footer.tagline")}</p>
            </div>
          </div>

          <div className="flex flex-col items-start gap-8">
            {/* The global event's logo cannot say «2026 · València», so the line stays as text below it. */}
            {logo("space-apps") && <Mark name="" logo={logo("space-apps")} size="event" />}
            <Mark name={t("footer.event")} logo={logo("space-apps-valencia")} className="label text-ink" />
          </div>

          <nav aria-labelledby="footer-pages" className="flex flex-col items-start gap-10">
            <p id="footer-pages" className="label text-muted">
              {t("footer.sections")}
            </p>
            <Link to="/about" className={`small text-muted ${link}`}>
              {t("nav.about")}
            </Link>
            <Link to="/manifesto" className={`small text-muted ${link}`}>
              {t("nav.manifesto")}
            </Link>
            <Link to="/library" className={`small text-muted ${link}`}>
              {t("nav.library")}
            </Link>
            <a href={REPOSITORY} target="_blank" rel="noreferrer" className={`small text-muted ${link}`}>
              {t("footer.code")}
            </a>
          </nav>
        </div>

        {sources.length > 0 && (
          <section aria-labelledby="footer-data" className="flex flex-col gap-16 border-t border-line pt-22">
            <h2 id="footer-data" className="label text-muted">
              {t("footer.dataFrom")}
            </h2>
            <div className="grid gap-22 md:grid-cols-3 md:gap-x-40">
              {(["nasa", "esa", "other"] as const)
                .filter((agency) => groups[agency].length > 0)
                .map((agency) => (
                  <div key={agency} className="flex flex-col gap-10">
                    <span className="label text-signal">{t(AGENCY[agency])}</span>
                    {marksOf(groups[agency]).length > 0 && (
                      <div className="flex min-h-28 flex-wrap items-center gap-x-16 gap-y-10">
                        {marksOf(groups[agency]).map((entry) => (
                          <Mark key={entry.key} name={entry.alt} logo={entry} className="small text-muted" />
                        ))}
                      </div>
                    )}
                    <ul className="flex flex-wrap gap-x-12 gap-y-4">
                      {groups[agency].map((source) => {
                        const page = pageOf(source);
                        const mark = <Mark name={source.name} logo={logo(source.id)} />;
                        return (
                          <li key={source.id} className="small text-muted">
                            {page ? (
                              <a href={page} target="_blank" rel="noreferrer" className={link}>
                                {mark}
                              </a>
                            ) : (
                              mark
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ))}
            </div>
          </section>
        )}

        <div className="flex flex-col gap-4 border-t border-line pt-16">
          <p className="caption text-muted">{t("footer.noEndorsement")}</p>
          <p className="caption text-muted">{t("footer.licence")}</p>
        </div>
      </div>
    </footer>
  );
}
