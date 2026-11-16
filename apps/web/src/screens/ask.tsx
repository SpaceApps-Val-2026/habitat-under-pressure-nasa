// Asking Delfos, shared by the cover and the question article: send a question (plan → run, or the report
// that already answers it, or the article page), and the box where Delfos asks which place is meant.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import {
  apiMessage,
  createAnalysis,
  createPlan,
  reportPath,
  resolvePlace,
  type PlaceAnswer,
  type PlanCandidate,
  type PlanReply,
} from "../api/client";
import type { plan } from "../contracts";
import { useI18n } from "../i18n";
import { Button } from "../ui/controls";

export type Create = (text: string, language: string, place?: PlanCandidate | string) => Promise<PlanReply>;
export type Start = (candidate: plan.AnalysisPlan) => Promise<{ analysis_id: string }>;
export type Resolve = (text: string, language: string) => Promise<PlaceAnswer>;
export type Phase = "idle" | "planning" | "starting";
export type NotPossible = { limits: string[]; alternative: string };
export type Clarification = Extract<PlanReply, { kind: "clarification" }>;

const ARTICLE = /^\/q\/[0-9a-f]{32}$/;

export function useAsk({
  create = createPlan,
  start = createAnalysis,
  resolve = resolvePlace,
}: {
  create?: Create;
  start?: Start;
  resolve?: Resolve;
} = {}) {
  const { t, language } = useI18n();
  const navigate = useNavigate();
  const [phase, setPhaseState] = useState<Phase>("idle");
  // One question at a time: Enter in the box, or a second click, while Delfos reads is ignored. A ref, because
  // two key presses can arrive before the re-render that would disable anything.
  const busy = useRef(false);
  const setPhase = (next: Phase) => {
    busy.current = next !== "idle";
    setPhaseState(next);
  };
  const [clarification, setClarification] = useState<Clarification | null>(null);
  const [notPossible, setNotPossible] = useState<NotPossible | null>(null);
  const [failureText, setFailureText] = useState<string | null>(null);
  // «Another place…»: the person types the place, or more context, when no candidate is the one.
  const [other, setOther] = useState(false);
  const [otherText, setOtherText] = useState("");
  const [otherNote, setOtherNote] = useState<string | null>(null);

  // A question goes all the way: plan, then the run (or the report that already answers it). It stops only
  // when Delfos asks which place (here, in the page), cannot answer, or answers with an article.
  async function send(text: string, chosen?: PlanCandidate | string) {
    if (!text || busy.current) return;
    setPhase("planning");
    setFailureText(null);
    setNotPossible(null);
    if (!chosen) {
      setClarification(null);
      setOther(false);
      setOtherNote(null);
    }
    try {
      const reply = await create(text, language, chosen);
      // A question about the place is always asked in the page, whatever article the server also wrote.
      if (reply.kind === "clarification") {
        setClarification(reply);
        setPhase("idle");
        return;
      }
      if (reply.kind === "article") {
        if (!ARTICLE.test(reply.article_url)) throw new Error("Invalid article URL");
        navigate(reply.article_url);
        return;
      }
      setClarification(null);
      setOther(false);
      if (reply.existing_analysis) {
        navigate(reportPath(reply.existing_analysis));
        return;
      }
      const answer = reply.plan.answer;
      if (answer.level === "not_possible") {
        const local = (value?: Record<string, string>) => (value ? (value[language] ?? value.en ?? "") : "");
        setNotPossible({ limits: (answer.limits ?? []).map(local).filter(Boolean), alternative: local(answer.alternative) });
        setPhase("idle");
        return;
      }
      setPhase("starting");
      const { analysis_id } = await start(reply.plan);
      navigate(`/run/${encodeURIComponent(analysis_id)}`, { state: { plan: reply.plan } });
    } catch (error) {
      setFailureText(apiMessage(error, language, t("home.askFailed")));
      setPhase("idle");
    }
  }

  // The typed place goes to the gazetteer first, so it works with or without a language model: a place found
  // goes back with the question as a zone; several become the new candidates. Words that are not a place
  // are context: they travel with the question and as `place`, and the server decides what to do with them.
  async function lookForOther(question: string) {
    const typed = otherText.trim();
    if (!typed) return;
    setOtherNote(null);
    let answer: PlaceAnswer;
    try {
      answer = await resolve(typed, language);
    } catch (error) {
      setOtherNote(apiMessage(error, language, t("home.otherFailed")));
      return;
    }
    if (answer.status === "found") {
      await send(question, answer.zone);
      return;
    }
    if (answer.status === "ambiguous" && answer.candidates.length > 0) {
      setClarification((current) =>
        current
          ? { ...current, candidates: answer.candidates }
          : { kind: "clarification", missing: "place", question: { en: "", es: "" }, candidates: answer.candidates },
      );
      setOther(false);
      return;
    }
    setOtherNote(t("home.otherNotFound").replace("{text}", typed));
    await send(`${question} (${typed})`, typed);
  }

  return {
    phase,
    sending: phase !== "idle",
    clarification,
    setClarification,
    notPossible,
    failureText,
    other,
    setOther,
    otherText,
    setOtherText,
    otherNote,
    send,
    lookForOther,
  };
}

export type Ask = ReturnType<typeof useAsk>;

// Delfos is waiting for the person: the accent colour, never the warning amber. The candidates as buttons and
// «Another place…» at the end, also when there is no candidate. It takes the focus when it appears.
export function ClarifyBox({ ask, question }: { ask: Ask; question: string }) {
  const { t, language } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  const { clarification, other, otherText, otherNote } = ask;

  useEffect(() => {
    if (!clarification) return;
    box.current?.scrollIntoView?.({ block: "nearest" });
    // Whoever is still typing keeps the caret: taking it would swallow their keys and, on a phone, close the
    // keyboard. The question is read out anyway, by its role="status".
    if (document.activeElement?.matches("input, textarea, [contenteditable]")) return;
    box.current?.focus({ preventScroll: true });
  }, [clarification]);

  if (!clarification || ask.sending) return null;
  const candidates = clarification.candidates ?? [];
  const text = clarification.question[language] ?? clarification.question.en;

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask.lookForOther(question);
  }

  return (
    <div
      ref={box}
      tabIndex={-1}
      role="group"
      aria-labelledby="clarify-question"
      className="fade-in flex flex-col gap-10 rounded-xl border border-signal border-l-4 bg-signal-soft p-12 text-ink outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      <span className="label text-signal">{t("home.clarification")}</span>
      {text && (
        <p id="clarify-question" role="status" className="body font-semibold">
          {text}
        </p>
      )}
      {candidates.length > 0 && (
        <ul className="flex flex-wrap gap-6">
          {candidates.map((candidate) => (
            <li key={candidate.id} className="min-w-0">
              <button
                type="button"
                onClick={() => ask.send(question, candidate)}
                className="small min-h-11 rounded-md border border-line-strong bg-paper px-12 py-6 text-left text-ink transition-colors hover:border-signal hover:text-signal focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                {candidate.name}
              </button>
            </li>
          ))}
        </ul>
      )}
      {(clarification.missing === "place" || candidates.length > 0) &&
        (!other ? (
          <button
            type="button"
            aria-expanded={false}
            onClick={() => ask.setOther(true)}
            className="small min-h-11 w-fit rounded-md border border-dashed border-signal px-12 py-6 font-semibold text-signal transition-colors hover:bg-paper focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          >
            {t("home.otherPlace")}
          </button>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-6">
            <label htmlFor="clarify-other" className="small font-semibold">
              {t("home.otherLabel")}
            </label>
            <div className="flex flex-wrap gap-6">
              <input
                id="clarify-other"
                autoFocus
                value={otherText}
                onChange={(event) => ask.setOtherText(event.target.value)}
                aria-describedby="clarify-other-hint"
                className="body min-h-11 min-w-0 flex-1 rounded-md border border-line-strong bg-paper px-10 py-6 text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              />
              <Button type="submit" disabled={otherText.trim() === ""} className="shrink-0">
                {t("home.otherSend")}
              </Button>
            </div>
            <p id="clarify-other-hint" className="small text-muted">
              {t("home.otherHint")}
            </p>
            {otherNote && (
              <p role="status" className="small text-ink">
                {otherNote}
              </p>
            )}
          </form>
        ))}
    </div>
  );
}

// The bar while a question is on its way: the same as the run screen's, moving without a count.
export function AskProgress({ ask }: { ask: Ask }) {
  const { t } = useI18n();
  if (!ask.sending) return null;
  return (
    <div role="status" className="fade-in run-stage-progress rounded-xl border border-line bg-paper p-12 text-ink">
      <p className="run-stage-now">
        <span className="run-spinner" aria-hidden="true" />
        <span>{t(ask.phase === "planning" ? "home.thinking" : "home.starting")}</span>
      </p>
      <div className="run-stage-track" aria-hidden="true">
        <div className="run-stage-fill is-indeterminate" />
      </div>
    </div>
  );
}
