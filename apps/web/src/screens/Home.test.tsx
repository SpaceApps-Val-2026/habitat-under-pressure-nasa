import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { ApiError, type AnalysisSummary, type PlaceAnswer, type PlanCandidate, type PlanReply } from "../api/client";
import type { plan } from "../contracts";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";
import { Home } from "./Home";
import planExample from "../../../../docs/contratos/examples/plan.json";

type Create = (text: string, language: string, place?: PlanCandidate | string) => Promise<PlanReply>;
type Resolve = (text: string, language: string) => Promise<PlaceAnswer>;
type Load = () => Promise<{ items: AnalysisSummary[] }>;
type AnalysisPlan = plan.AnalysisPlan;

const items: AnalysisSummary[] = [
  {
    id: "sagunt-impact-example",
    title: { en: "Sagunto: Soil moisture, Water demand, Precipitation" },
    summary: { en: "Vegetation and surface heat." },
    design: "impact",
    zone: { name: "Parc Sagunt II, Spain", bbox: [-0.35, 39.6, -0.15, 39.75] },
    created: "2026-11-14T10:00:00Z",
    status: "finished",
    featured: true,
  },
];

const example = planExample as unknown as AnalysisPlan;

function RunProbe() {
  const { analysisId } = useParams();
  return <p>RUN {analysisId}</p>;
}

function ArticleProbe() {
  const { id } = useParams();
  return <p>ARTICLE {id}</p>;
}

function ReportProbe() {
  const { analysisId } = useParams();
  return <p>REPORT {analysisId}</p>;
}

function planReply(extra: Partial<AnalysisPlan["answer"]> = {}, existing: string | null = null): PlanReply {
  return {
    kind: "plan",
    plan: { ...example, answer: { level: "complete", ...extra } },
    plan_fingerprint: null,
    existing_analysis: existing,
  };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-units-choice", "imperial");
});

function renderHome({
  create = vi.fn(async () => planReply()),
  load = vi.fn(async () => ({ items })),
  start = vi.fn(async () => ({ analysis_id: "run-1" })),
  read = vi.fn(async () => ({ plan: { ...example, request: { text: "Has the land warmed in Alzira?", language: "en" } } })),
  names = vi.fn(async () => ({ variables: [] })),
  resolve = vi.fn(async (): Promise<PlaceAnswer> => ({ status: "not_found" })),
}: {
  create?: Create;
  load?: Load;
  start?: (candidate: AnalysisPlan) => Promise<{ analysis_id: string }>;
  read?: (id: string) => Promise<{ plan?: AnalysisPlan | null }>;
  names?: () => Promise<{ variables: { id: string; name: Record<string, string> }[] }>;
  resolve?: Resolve;
} = {}) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <I18nProvider>
        <UnitsProvider>
          <Routes>
            <Route path="/" element={
              <Home create={create} load={load} start={start} read={read}
                names={names} resolve={resolve} />
            } />
            <Route path="/run/:analysisId" element={<RunProbe />} />
            <Route path="/a/:analysisId/report/journalistic" element={<ReportProbe />} />
            <Route path="/q/:id" element={<ArticleProbe />} />
          </Routes>
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

function ask(text: string) {
  fireEvent.change(screen.getByRole("searchbox", { name: "Your question" }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

const articleUrl = "/q/0123456789abcdef0123456789abcdef";

test("an article answer opens the article page inside the app", async () => {
  const create = vi.fn(async (): Promise<PlanReply> => ({ kind: "article", article_url: articleUrl }));
  const start = vi.fn(async () => ({ analysis_id: "run-1" }));
  renderHome({ create, start });
  ask("What is the impact in this place?");

  expect(await screen.findByText("ARTICLE 0123456789abcdef0123456789abcdef")).toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
});

test("a question about the place stays on the cover even when the server also wrote an article", async () => {
  const create = vi.fn(
    async (): Promise<PlanReply> => ({
      kind: "clarification",
      question: { en: "Which place?" },
      missing: "place",
      candidates: [{ id: "dubai", name: "Dubai, United Arab Emirates", bbox: [0, 0, 1, 1] }],
      article_url: articleUrl,
    }),
  );
  renderHome({ create });
  ask("How does the Gulf war with Iran affect Dubai?");

  expect(await screen.findByRole("button", { name: "Dubai, United Arab Emirates" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Another place…" })).toBeInTheDocument();
  expect(screen.queryByText(/^ARTICLE/)).not.toBeInTheDocument();
});

test("the cover names Delfos, shows the question box and the latest reports titled by their question", async () => {
  renderHome();

  expect(screen.getByRole("heading", { name: /One method\. Any place on Earth\./ })).toBeInTheDocument();
  expect(screen.getByText(/Ask Delfos/)).toBeInTheDocument();
  expect(screen.getByRole("searchbox", { name: "Your question" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: /Has the land warmed in Alzira\?/ })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example",
  );
  expect(screen.getByRole("link", { name: "All reports" })).toHaveAttribute("href", "/library");
});

test("the examples are the questions of real analyses and one click asks it", async () => {
  const create = vi.fn(async () => planReply());
  renderHome({ create });

  fireEvent.click(await screen.findByRole("button", { name: "Has the land warmed in Alzira?" }));

  await screen.findByText("RUN run-1");
  expect(create).toHaveBeenCalledWith("Has the land warmed in Alzira?", "en", undefined);
});

test("without asked questions the examples are built from catalogue names and real places", async () => {
  const read = vi.fn(async () => ({
    plan: {
      ...example,
      request: { text: "", language: "en" },
      variables: ["t2m"] as unknown as AnalysisPlan["variables"],
      zone: { ...example.zone, name: "Alzira, La Ribera Alta, Spain" },
    },
  }));
  const names = vi.fn(async () => ({ variables: [{ id: "t2m", name: { en: "Air temperature" } }] }));
  renderHome({ read, names });

  expect(await screen.findByRole("button", { name: "Air temperature in Alzira: what has changed?" })).toBeInTheDocument();
});

test("the resting place of the globe feeds the reading card, metric even with imperial units", async () => {
  renderHome();

  expect((await screen.findAllByText("Parc Sagunt II, Spain")).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText(/per decade/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("Illustrative").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  expect(screen.queryByText(/°F/)).not.toBeInTheDocument();
  expect(screen.getAllByText(/% of saturation/).length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText(/Hamed/)).not.toBeInTheDocument();
  expect(screen.queryByText(/\d[.,]\d{3,}/)).not.toBeInTheDocument();
});

test("the reading card reads in Spanish", async () => {
  localStorage.setItem("hup-lang", "es");
  renderHome();

  expect(await screen.findByText("Temperatura del aire")).toBeInTheDocument();
  expect(screen.getAllByText("Ilustrativo").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText(/por década/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText(/mm\/día/).length).toBeGreaterThanOrEqual(1);
});

test("Enter launches the question: plan, run, and the run screen at once", async () => {
  const create = vi.fn(async () => planReply());
  const start = vi.fn(async () => ({ analysis_id: "run-1" }));
  renderHome({ create, start });

  ask("What changed around Sagunt?");

  await screen.findByText("RUN run-1");
  expect(create).toHaveBeenCalledWith("What changed around Sagunt?", "en", undefined);
  expect(start).toHaveBeenCalledTimes(1);
});

test("while the plan is on its way the bar is already there", async () => {
  let resolve: (reply: PlanReply) => void = () => {};
  const create = vi.fn(() => new Promise<PlanReply>((done) => (resolve = done)));
  renderHome({ create });

  ask("What changed around Sagunt?");

  expect(await screen.findByText("Delfos is reading your question…")).toBeInTheDocument();
  // A request in flight: the bar moves without a count, never sits empty.
  expect(document.querySelector(".run-stage-fill.is-indeterminate")).not.toBeNull();
  expect(document.querySelector(".run-spinner")).not.toBeNull();
  resolve(planReply());
  await screen.findByText("RUN run-1");
});

test("a question that is already answered opens its report", async () => {
  const create = vi.fn(async () => planReply({}, "old-1"));
  const start = vi.fn(async () => ({ analysis_id: "run-1" }));
  renderHome({ create, start });

  ask("What changed around Sagunt?");

  await screen.findByText("REPORT old-1");
  expect(start).not.toHaveBeenCalled();
});

test("an ambiguous place shows one question and the candidates; a pick goes on with the same text", async () => {
  const candidate = { id: "valencia-es", name: "Valencia, Spain" };
  const create = vi.fn(
    async (_text: string, _language: string, place?: PlanCandidate | string): Promise<PlanReply> =>
      place
        ? planReply()
        : { kind: "clarification", question: { en: "Which place do you mean?" }, missing: "place", candidates: [candidate] },
  );
  renderHome({ create });

  ask("Is it warmer in Valencia?");

  expect(await screen.findAllByText("Which place do you mean?")).toHaveLength(1);
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Valencia, Spain" }));

  await screen.findByText("RUN run-1");
  expect(create).toHaveBeenLastCalledWith("Is it warmer in Valencia?", "en", candidate);
});

test("a clarification without candidates is a question, not an error", async () => {
  const create = vi.fn(
    async (): Promise<PlanReply> => ({
      kind: "clarification",
      question: { en: "No variable in the catalogue covers this question." },
    }),
  );
  renderHome({ create });

  ask("What changed?");

  expect(await screen.findByText("No variable in the catalogue covers this question.")).toBeInTheDocument();
  expect(screen.queryByText(/^RUN/)).not.toBeInTheDocument();
});

test("a plan that cannot be answered says why and offers the alternative, without running", async () => {
  const create = vi.fn(async () =>
    planReply({
      level: "not_possible",
      limits: [{ en: "No source covers the sea floor." }],
      alternative: { en: "Sea temperature near Gandia" },
    }),
  );
  const start = vi.fn(async () => ({ analysis_id: "run-1" }));
  renderHome({ create, start });

  ask("How deep is the sea floor?");

  expect(await screen.findByText("No source covers the sea floor.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sea temperature near Gandia" })).toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
});

test("a refusal shows the server's own sentence", async () => {
  const create = vi.fn(async (): Promise<PlanReply> => {
    throw new ApiError(429, "rate_limited", { en: "Too many questions; wait a minute.", es: "Demasiadas preguntas." });
  });
  renderHome({ create });

  ask("What changed?");

  expect(await screen.findByText("Too many questions; wait a minute.")).toBeInTheDocument();
});

test("without an answer from the server the cover says so", async () => {
  const create = vi.fn(async (): Promise<PlanReply> => {
    throw new Error("no assistant");
  });
  renderHome({ create });

  ask("What changed?");

  await waitFor(() => expect(screen.getByText(/assistant is not available/i)).toBeInTheDocument());
});

const gulfQuestion = "What happens with the burnt refineries in the gulf?";
const golfo = (id: string, name: string): PlanCandidate => ({ id, name, bbox: [0, 0, 1, 1] });
function gulfCreate() {
  return vi.fn(
    async (_text: string, _language: string, place?: PlanCandidate | string): Promise<PlanReply> =>
      place && typeof place !== "string" && place.id === "persian-gulf"
        ? planReply()
        : {
            kind: "clarification",
            question: { en: "Which place do you mean?" },
            missing: "place",
            candidates: [golfo("golfo-co", "El Golfo, Colombia"), golfo("golfo-ni", "El Golfo, Nicaragua")],
          },
  );
}

test("a question about the place stands out, takes the focus and offers another place", async () => {
  renderHome({ create: gulfCreate() });
  ask(gulfQuestion);

  const question = await screen.findByText("Which place do you mean?");
  const box = question.closest("[role=group]") as HTMLElement;
  expect(box.className).toContain("bg-signal-soft");
  expect(box.className).toContain("border-signal");
  await waitFor(() => expect(box).toHaveFocus());
  expect(screen.getByRole("button", { name: "El Golfo, Colombia" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Another place…" })).toBeInTheDocument();
});

test("a question about the place leaves the caret with whoever is still typing", async () => {
  renderHome({ create: gulfCreate() });
  const input = screen.getByRole("searchbox", { name: "Your question" });
  input.focus();
  ask(gulfQuestion);

  await screen.findByText("Which place do you mean?");
  expect(input).toHaveFocus();
});

test("another place found by the gazetteer goes back with the question as a zone", async () => {
  const create = gulfCreate();
  const persian = golfo("persian-gulf", "Persian Gulf");
  const resolve = vi.fn(async (): Promise<PlaceAnswer> => ({ status: "found", zone: persian }));
  renderHome({ create, resolve });
  ask(gulfQuestion);

  fireEvent.click(await screen.findByRole("button", { name: "Another place…" }));
  fireEvent.change(screen.getByLabelText("Type the place or give more context"), { target: { value: "Persian Gulf" } });
  fireEvent.click(screen.getByRole("button", { name: "Look for this place" }));

  await screen.findByText("RUN run-1");
  expect(resolve).toHaveBeenCalledWith("Persian Gulf", "en");
  expect(create).toHaveBeenLastCalledWith(gulfQuestion, "en", persian);
});

test("an ambiguous other place shows its own candidates", async () => {
  const resolve = vi.fn(
    async (): Promise<PlaceAnswer> => ({ status: "ambiguous", candidates: [golfo("kw", "Kuwait City"), golfo("kw2", "Kuwait")] }),
  );
  renderHome({ create: gulfCreate(), resolve });
  ask(gulfQuestion);

  fireEvent.click(await screen.findByRole("button", { name: "Another place…" }));
  fireEvent.change(screen.getByLabelText("Type the place or give more context"), { target: { value: "Kuwait" } });
  fireEvent.click(screen.getByRole("button", { name: "Look for this place" }));

  expect(await screen.findByRole("button", { name: "Kuwait City" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "El Golfo, Colombia" })).not.toBeInTheDocument();
});

test("context that is not a place travels with the question and the field stays", async () => {
  const create = gulfCreate();
  renderHome({ create });
  ask(gulfQuestion);

  fireEvent.click(await screen.findByRole("button", { name: "Another place…" }));
  const context = "I mean the Persian Gulf, because of the war with Iran";
  fireEvent.change(screen.getByLabelText("Type the place or give more context"), { target: { value: context } });
  fireEvent.click(screen.getByRole("button", { name: "Look for this place" }));

  await waitFor(() => expect(create).toHaveBeenLastCalledWith(`${gulfQuestion} (${context})`, "en", context));
  expect(await screen.findByText(/is not a place I can find/)).toBeInTheDocument();
  expect(screen.getByLabelText("Type the place or give more context")).toHaveValue(context);
});

test("another place is offered even when Delfos found no candidate", async () => {
  const create = vi.fn(
    async (): Promise<PlanReply> => ({ kind: "clarification", question: { en: "Which place do you mean?" }, missing: "place", candidates: [] }),
  );
  renderHome({ create });
  ask(gulfQuestion);

  expect(await screen.findByRole("button", { name: "Another place…" })).toBeInTheDocument();
});

test("back from a run the person stopped waiting for, the question is in the box and the note is honest", () => {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/", state: { question: "Is Alzira warmer?", leftRun: true } }]}>
      <I18nProvider>
        <UnitsProvider>
          <Routes>
            <Route
              path="/"
              element={<Home create={vi.fn()} load={vi.fn(async () => ({ items: [] }))} read={vi.fn()} names={vi.fn()} />}
            />
          </Routes>
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );

  expect(screen.getByRole("searchbox", { name: "Your question" })).toHaveValue("Is Alzira warmer?");
  expect(screen.getByText(/You stopped waiting; the analysis is still being computed/)).toBeInTheDocument();
});

test("long example questions sit on one line with their full text in a tooltip; short ones come first", async () => {
  const long = "prepara el radio de afectacion a nivel de consumo de energia de un centro de datos standard contruido en calig";
  const tooLong = "x".repeat(150);
  const texts: Record<string, string> = { a: long, b: "Is Alzira warmer?", c: tooLong };
  const read = vi.fn(async (id: string) => ({ plan: { ...example, request: { text: texts[id], language: "en" } } }));
  const load = vi.fn(async () => ({ items: ["a", "b", "c"].map((id) => ({ ...items[0], id })) }));
  renderHome({ read, load });

  const short = await screen.findByRole("button", { name: "Is Alzira warmer?" });
  const chip = await screen.findByRole("button", { name: long });
  const chips = screen.getAllByRole("button").filter((button) => button === short || button === chip);
  expect(chips[0]).toBe(short);
  expect(screen.queryByRole("button", { name: tooLong })).not.toBeInTheDocument();
  expect(chip.className).toContain("truncate");
  expect(chip).toHaveAttribute("title", long);

  fireEvent.focus(chip);
  expect(screen.getByRole("tooltip")).toHaveTextContent(long);
  fireEvent.blur(chip);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});
