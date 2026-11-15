import { useEffect, useState } from "react";

import { getLegislation, type Legislation } from "../api/client";
import { useI18n } from "../i18n";
import { ABSENCE, localized, referenced, Section, type BlockProps } from "./shared";

// The route's explained cards and citizen routes (B9). All optional: an older answer still reads.
type Route = {
  id?: string;
  action: string;
  how?: string;
  who_label?: string;
  status?: string;
  legal_basis?: string;
  url?: string;
};
type Explained = {
  short_name?: string;
  plain?: string;
  in_spain?: string;
  citizen_options?: Route[];
};
type ExplainedItem = Legislation["items"][number] & { visibility?: "card" | "title" | "hidden"; explained?: Explained };
type ExplainedLegislation = Omit<Legislation, "items"> & {
  items: ExplainedItem[];
  disclaimer?: string;
  general_options?: Route[];
};

const LINK =
  "text-signal underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

function RouteList({ routes }: { routes: Route[] }) {
  const { t } = useI18n();
  return (
    <ul className="flex list-disc flex-col gap-6 pl-16">
      {routes.map((route, index) => (
        <li key={route.id ?? `${route.action}-${index}`} className="body">
          {route.url ? (
            <a href={route.url} target="_blank" rel="noreferrer noopener" className={LINK}>
              {route.action}
            </a>
          ) : (
            route.action
          )}
          {route.how && <span className="text-muted"> {route.how}</span>}
          {(route.who_label || route.status || route.legal_basis) && (
            <p className="caption text-muted">
              {[route.who_label && `${t("blocks.who")}: ${route.who_label}`, route.status, route.legal_basis]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

export function NormCard({
  block,
  results,
  language,
  load = getLegislation,
  expanded = false,
}: BlockProps & { load?: typeof getLegislation }) {
  const { t } = useI18n();
  const variable = referenced(block, results)[0]?.variable;
  const [data, setData] = useState<ExplainedLegislation | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    if (!variable) {
      setData({ items: [] });
      return;
    }
    load(variable, language)
      .then((found) => {
        if (live) setData(found as ExplainedLegislation);
      })
      .catch(() => {
        if (live) setData({ items: [], absence: { reason: "source_unreachable" } });
      });
    return () => {
      live = false;
    };
  }, [variable, language, load]);

  const note = data?.note ? localized(data.note, language) : "";
  const absence = data?.absence;
  const items = (data?.items ?? []).filter((item) => item.visibility !== "hidden");
  const general = data?.general_options ?? [];

  return (
    <Section title={t("blocks.legislation")}>
      <p className="small text-muted">{t("blocks.legislationNote")}</p>
      <details className="norm-details flex flex-col gap-8" open={expanded}>
        <summary className="small cursor-pointer font-semibold text-signal">{t("blocks.legislationOpen")}</summary>
        <div className="mt-8 flex flex-col gap-8">
      {!data && <p className="body text-muted">{t("blocks.loading")}</p>}
      {absence && (
        <div className="flex flex-col gap-2">
          <span className="label text-amber-ink">
            {ABSENCE[absence.reason] ? t(ABSENCE[absence.reason]) : absence.reason}
          </span>
          {absence.detail && <p className="small text-muted">{localized(absence.detail, language)}</p>}
        </div>
      )}
      {data && items.length === 0 && !absence && general.length === 0 && (
        <p className="body text-muted">{t("blocks.noLegislation")}</p>
      )}
      {items.length > 0 && (
        <ul className="flex flex-col gap-12">
          {items.map((item) => (
            <li key={item.celex} className="flex flex-col gap-4">
              {item.explained?.short_name && <h3 className="body font-semibold">{item.explained.short_name}</h3>}
              {item.explained?.plain && <p className="body">{item.explained.plain}</p>}
              {item.explained?.in_spain && (
                <p className="small text-muted">
                  {t("blocks.inSpain")}: {item.explained.in_spain}
                </p>
              )}
              {item.explained?.citizen_options && item.explained.citizen_options.length > 0 && (
                <RouteList routes={item.explained.citizen_options} />
              )}
              <a href={item.url} target="_blank" rel="noreferrer noopener" className={`small ${LINK}`}>
                {item.title}
              </a>
              <p className="data text-muted">
                {item.date} · CELEX {item.celex}
              </p>
            </li>
          ))}
        </ul>
      )}
      {general.length > 0 && (
        <div className="flex flex-col gap-6">
          <h3 className="body font-semibold">{t("blocks.whatYouCanDo")}</h3>
          <RouteList routes={general} />
        </div>
      )}
      {data?.disclaimer && <p className="caption text-muted">{data.disclaimer}</p>}
      {note && <p className="caption text-muted">{note}</p>}
        </div>
      </details>
    </Section>
  );
}
