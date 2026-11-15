import { manifestoEn } from "../content/manifesto.en";
import { manifestoEs } from "../content/manifesto.es";
import { useI18n } from "../i18n";
import { EditorialPage } from "./Editorial";

// The manifesto: numbered principles in plain words. The words are a draft in content/manifesto.*.ts.
export function Manifesto() {
  const { language } = useI18n();
  const content = language === "es" ? manifestoEs : manifestoEn;
  const number = new Intl.NumberFormat(language, { minimumIntegerDigits: 2 });

  return (
    <EditorialPage kicker={content.kicker} title={content.title} lead={content.lead}>
      <ol className="flex flex-col">
        {content.principles.map((principle, index) => (
          <li key={principle.title} className="grid grid-cols-[48px_1fr] gap-12 border-b border-line py-22 first:pt-0">
            <span aria-hidden="true" className="data pt-4 text-signal">
              {number.format(index + 1)}
            </span>
            <div className="flex flex-col gap-6">
              <h2 className="title-2">{principle.title}</h2>
              <p className="body text-lg leading-relaxed text-muted">{principle.text}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="title-2 font-normal text-balance">{content.closing}</p>
    </EditorialPage>
  );
}
