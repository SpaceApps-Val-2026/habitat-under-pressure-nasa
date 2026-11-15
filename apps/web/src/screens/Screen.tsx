import { useI18n } from "../i18n";
import type { MessageKey } from "../i18n/messages";
import { PAGE } from "../layout/page";

// C1 ships empty screens: a route and its title on paper. Each package fills its own screen.
export function Screen({ titleKey }: { titleKey: MessageKey }) {
  const { t } = useI18n();
  return (
    <main className={`${PAGE} py-22`}>
      <section className="rounded-xl border border-line bg-paper p-16">
        <h1 className="title-1 text-balance">{t(titleKey)}</h1>
      </section>
    </main>
  );
}
