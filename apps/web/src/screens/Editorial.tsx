// The house editorial page: one reading column inside the page container, a kicker, a headline, a lead
// and sections divided by thin rules. No cards. Used by «What is this?» and the manifesto.
import { useEffect, useState, type ReactNode } from "react";

import { getCatalog } from "../api/client";
import type { CatalogSourceLike } from "../content/sources";
import { PAGE } from "../layout/page";

export function EditorialPage({
  kicker,
  title,
  lead,
  children,
}: {
  kicker: string;
  title: string;
  lead: string;
  children: ReactNode;
}) {
  return (
    <main className={`${PAGE} py-40`}>
      <article className="mx-auto flex max-w-[68ch] flex-col gap-28 text-ink">
        <header className="flex flex-col gap-12 border-b border-line pb-28">
          <p className="label text-signal">{kicker}</p>
          <h1 className="display text-balance max-sm:text-[34px]!">{title}</h1>
          <p className="text-xl leading-snug text-muted">{lead}</p>
        </header>
        {children}
      </article>
    </main>
  );
}

export function EditorialSection({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-12 border-b border-line pb-28 last:border-b-0">
      <h2 className="title-2">{heading}</h2>
      {children}
    </section>
  );
}

// The catalogue's sources, once per mount; an empty list when the catalogue cannot be read.
export function useCatalogSources(load: () => Promise<{ sources?: CatalogSourceLike[] }> = getCatalog) {
  const [sources, setSources] = useState<CatalogSourceLike[]>([]);
  useEffect(() => {
    let live = true;
    load()
      .then((catalog) => {
        if (live && Array.isArray(catalog?.sources)) setSources(catalog.sources);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [load]);
  return sources;
}
