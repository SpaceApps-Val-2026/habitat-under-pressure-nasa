import { Link } from "react-router-dom";

import { useI18n } from "../i18n";
import { PAGE } from "../layout/page";
import { buttonClass } from "../ui/controls";

// An analysis that was cancelled has no answer: its question, that it was cancelled, and a way to ask again.
export function CancelledAnalysis({ question }: { question: string }) {
  const { t } = useI18n();
  return (
    <main className={`${PAGE} py-40`}>
      <section className="mx-auto flex max-w-[68ch] flex-col gap-16">
        <span className="label w-fit rounded-xs border border-line-strong px-6 py-2 text-muted">{t("run.status.cancelled")}</span>
        {question && <h1 className="title-1 text-balance">{question}</h1>}
        <p className="body text-muted">{t("run.stage.cancelled")}</p>
        <Link to="/" state={{ question }} className={`${buttonClass("primary")} w-fit`}>
          {t("run.askAgain")}
        </Link>
      </section>
    </main>
  );
}
