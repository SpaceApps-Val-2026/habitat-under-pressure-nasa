import { sourceCitation, sourceLicense } from "../catalog/sources";
import { useI18n } from "../i18n";
import { useCatalog } from "./catalog";
import { distinctSources, formatNumber, referenced, Section, sourceDisplayName, type BlockProps } from "./shared";

export function SourcesTable({ block, results, language, catalogSources, accessed }: BlockProps) {
  const { t } = useI18n();
  const catalog = useCatalog();
  const known = catalogSources ?? catalog?.sources;
  // One line per distinct source, with the period of the first result that cites it: the citation's
  // `[year]` is the data's, not the catalogue's.
  const sources = distinctSources(referenced(block, results));
  if (sources.length === 0) return null;

  return (
    <Section title={t("blocks.sources")}>
      <div className="w-full overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className="label py-6 text-muted">{t("blocks.source")}</th>
              <th scope="col" className="label py-6 text-muted">{t("blocks.product")}</th>
              <th scope="col" className="label py-6 text-muted">{t("blocks.items")}</th>
              <th scope="col" className="label py-6 text-muted">{t("blocks.license")}</th>
            </tr>
          </thead>
          <tbody>
            {sources.map(({ source, period }) => {
              const license = sourceLicense(source, known, language, period, accessed);
              const citation = sourceCitation(source, known, language, period, accessed);
              return (
                <tr key={`${source.source}-${source.url}`} className="border-b border-line align-top">
                  <td className="body py-6">
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                    >
                      {sourceDisplayName(source, results, known, language)}
                    </a>
                  </td>
                  <td className="small py-6">{[source.product, source.version].filter(Boolean).join(" · ")}</td>
                  <td className="data py-6">
                    {source.n_items !== undefined ? formatNumber(source.n_items, language) : t("blocks.noValue")}
                  </td>
                  <td className="small py-6">
                    {license ?? t("blocks.noValue")}
                    {citation && <p className="caption mt-4 text-muted">{citation}</p>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
