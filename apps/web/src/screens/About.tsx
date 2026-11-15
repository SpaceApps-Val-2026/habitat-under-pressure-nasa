import { aboutEn } from "../content/about.en";
import { aboutEs } from "../content/about.es";
import { byAgency, pageOf, type Agency, type CatalogSourceLike } from "../content/sources";
import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { EditorialPage, EditorialSection, useCatalogSources } from "./Editorial";

const EVIDENCE: MessageKey[][] = [
  ["evidence.E0.name", "evidence.E0.note"],
  ["evidence.E1.name", "evidence.E1.note"],
  ["evidence.E1control.name", "evidence.E1control.note"],
  ["evidence.E2.name", "evidence.E2.note"],
  ["evidence.E3.name", "evidence.E3.note"],
  ["evidence.E4.name", "evidence.E4.note"],
  ["evidence.E5.name", "evidence.E5.note"],
];

const CLASSES: MessageKey[] = [
  "dataClass.measured",
  "dataClass.reanalysis",
  "dataClass.extrapolated",
  "dataClass.approximated",
  "dataClass.illustrative",
];

const AGENCY: Record<Agency, MessageKey> = {
  nasa: "footer.agency.nasa",
  esa: "footer.agency.esa",
  other: "footer.agency.other",
};

// «What is this?»: the words come from content/about.*.ts; the evidence levels, data classes and sources
// are built from the interface's texts and the live catalogue, so they never drift from what the app does.
export function About({ load }: { load?: () => Promise<{ sources?: CatalogSourceLike[] }> }) {
  const { t, language } = useI18n();
  const content = language === "es" ? aboutEs : aboutEn;
  const sources = useCatalogSources(load);
  const groups = byAgency(sources);

  return (
    <EditorialPage kicker={content.kicker} title={content.title} lead={content.lead}>
      {content.sections.map((section) => (
        <EditorialSection key={section.heading} heading={section.heading}>
          {section.paragraphs.map((paragraph) => (
            <p key={paragraph} className="body text-lg leading-relaxed">
              {paragraph}
            </p>
          ))}
        </EditorialSection>
      ))}

      <EditorialSection heading={content.evidenceHeading}>
        <p className="body text-lg leading-relaxed">{content.evidenceIntro}</p>
        <dl className="grid gap-x-16 gap-y-8 sm:grid-cols-[max-content_1fr]">
          {EVIDENCE.map(([name, note]) => (
            <div key={name} className="contents">
              <dt className="label pt-2 text-ink">{t(name)}</dt>
              <dd className="body text-muted">{t(note)}</dd>
            </div>
          ))}
        </dl>
        <p className="body text-lg leading-relaxed">{content.classesIntro}</p>
        <ul className="flex flex-wrap gap-6" aria-label={content.classesHeading}>
          {CLASSES.map((key) => (
            <li key={key} className="label rounded-xs border border-line-strong px-6 py-2 text-muted">
              {t(key)}
            </li>
          ))}
        </ul>
      </EditorialSection>

      <EditorialSection heading={content.sourcesHeading}>
        <p className="body text-lg leading-relaxed">{content.sourcesIntro}</p>
        {(["nasa", "esa", "other"] as const)
          .filter((agency) => groups[agency].length > 0)
          .map((agency) => (
            <div key={agency} className="flex flex-col gap-6">
              <h3 className="label text-signal">{t(AGENCY[agency])}</h3>
              <ul className="flex flex-col divide-y divide-line border-y border-line">
                {groups[agency].map((source) => {
                  const page = pageOf(source);
                  return (
                    <li key={source.id} className="flex flex-col gap-2 py-8">
                      {page ? (
                        <a
                          href={page}
                          target="_blank"
                          rel="noreferrer"
                          className="body font-semibold text-ink underline decoration-line-strong underline-offset-4 hover:text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
                        >
                          {source.name}
                        </a>
                      ) : (
                        <span className="body font-semibold">{source.name}</span>
                      )}
                      {source.provider && (
                        <span className="small text-muted">
                          {t("about.provider")}: {source.provider}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
      </EditorialSection>

      <EditorialSection heading={content.closing.heading}>
        {content.closing.paragraphs.map((paragraph) => (
          <p key={paragraph} className="body text-lg leading-relaxed">
            {paragraph}
          </p>
        ))}
      </EditorialSection>
    </EditorialPage>
  );
}
