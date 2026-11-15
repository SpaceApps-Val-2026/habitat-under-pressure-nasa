import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { ApiError, type PlaceAnswer, type PlanCandidate, type PlanReply, type QuestionArticle as Article } from "../api/client";
import type { PlaceImageInfo } from "../blocks/PlaceImage";
import type { plan } from "../contracts";
import { I18nProvider } from "../i18n";
import planExample from "../../../../docs/contratos/examples/plan.json";
import { QuestionArticle } from "./QuestionArticle";

const article: Article = {
  id: "0123456789abcdef0123456789abcdef",
  question: "Cómo afecta la guerra del golfo con Irán a Dubai?",
  language: "es",
  headline: { en: "The war cannot be measured from orbit", es: "La guerra no se mide desde la órbita" },
  lead: { en: "Delfos has no source for military events.", es: "Delfos no tiene fuentes sobre hechos militares." },
  cannot_say: { en: "Whether the conflict changed the city.", es: "Si el conflicto cambió la ciudad." },
  zone: "Dubai",
  sources: [{ name: "NASA POWER", url: "https://power.larc.nasa.gov/" }],
  candidates: [],
  clarify: false,
};

function RunProbe() {
  const { analysisId } = useParams();
  return <p>RUN {analysisId}</p>;
}

function HomeProbe() {
  const state = useLocation().state as { question?: string } | null;
  return <p>HOME [{state?.question ?? ""}]</p>;
}

function renderArticle({
  load = vi.fn(async () => article),
  create = vi.fn(
    async (): Promise<PlanReply> => ({
      kind: "plan",
      plan: planExample as unknown as plan.AnalysisPlan,
      plan_fingerprint: null,
      existing_analysis: null,
    }),
  ),
  resolve = vi.fn(async (): Promise<PlaceAnswer> => ({ status: "not_found" })),
  imageInfo = vi.fn(async (): Promise<PlaceImageInfo | null> => ({ available: true, credit: { en: "Contains Copernicus data" } })),
}: {
  load?: (id: string) => Promise<Article>;
  create?: (text: string, language: string, place?: PlanCandidate | string) => Promise<PlanReply>;
  resolve?: (text: string, language: string) => Promise<PlaceAnswer>;
  imageInfo?: (bbox: number[]) => Promise<PlaceImageInfo | null>;
} = {}) {
  const start = vi.fn(async () => ({ analysis_id: "run-1" }));
  render(
    <MemoryRouter initialEntries={[`/q/${article.id}`]}>
      <I18nProvider>
        <Routes>
          <Route path="/" element={<HomeProbe />} />
          <Route path="/q/:id" element={<QuestionArticle load={load} create={create} start={start} resolve={resolve} imageInfo={imageInfo} />} />
          <Route path="/run/:analysisId" element={<RunProbe />} />
        </Routes>
      </I18nProvider>
    </MemoryRouter>,
  );
  return { load, create, start, resolve, imageInfo };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "en");
});

test("the article reads in the interface language, not the one the question was asked in", async () => {
  renderArticle();

  expect(await screen.findByRole("heading", { level: 1, name: "The war cannot be measured from orbit" })).toBeInTheDocument();
  expect(screen.getByText("Delfos has no source for military events.")).toBeInTheDocument();
  expect(screen.getByText("Whether the conflict changed the city.")).toBeInTheDocument();
  expect(screen.getByText(article.question)).toBeInTheDocument();
  expect(screen.getByText("Dubai")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "NASA POWER" })).toHaveAttribute("href", "https://power.larc.nasa.gov/");
  expect(screen.getByRole("heading", { name: "What remains unknown" })).toBeInTheDocument();
});

test("and in Spanish when the interface is Spanish", async () => {
  localStorage.setItem("hup-lang", "es");
  renderArticle();

  expect(await screen.findByRole("heading", { level: 1, name: "La guerra no se mide desde la órbita" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Qué queda por saber" })).toBeInTheDocument();
});

test("an example question with the article's place launches the normal flow", async () => {
  const { create, start } = renderArticle();

  fireEvent.click(await screen.findByRole("button", { name: "Is the temperature rising in Dubai?" }));

  expect(await screen.findByText("RUN run-1")).toBeInTheDocument();
  expect(create).toHaveBeenCalledWith("Is the temperature rising in Dubai?", "en", undefined);
  expect(start).toHaveBeenCalledTimes(1);
});

test("rephrase takes the question back to the cover; ask another leaves the box empty", async () => {
  renderArticle();

  fireEvent.click(await screen.findByRole("link", { name: "Rephrase this question" }));
  expect(await screen.findByText(`HOME [${article.question}]`)).toBeInTheDocument();
});

test("an article that asks for the place offers the candidates and goes on with the chosen one", async () => {
  const dubai = { id: "dubai", name: "Dubai, United Arab Emirates", bbox: [55, 25, 55.5, 25.4] };
  const load = vi.fn(async () => ({ ...article, clarify: true, candidates: [dubai] }));
  const { create } = renderArticle({ load });

  fireEvent.click(await screen.findByRole("button", { name: "Dubai, United Arab Emirates" }));

  expect(await screen.findByText("RUN run-1")).toBeInTheDocument();
  expect(create).toHaveBeenCalledWith(article.question, "en", dubai);
  expect(screen.queryByRole("button", { name: "Another place…" })).not.toBeInTheDocument();
});

test("a missing article says it is no longer available", async () => {
  renderArticle({ load: vi.fn(async () => Promise.reject(new ApiError(404, "not_found"))) });

  expect(await screen.findByRole("heading", { name: "This article is no longer available." })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to the cover" })).toHaveAttribute("href", "/");
});

const dubaiZone = { id: "dubai", name: "Dubai, United Arab Emirates", bbox: [55.0, 24.8, 55.6, 25.4] };

test("the place resolves: the satellite picture of that box appears after the lead, with its credit", async () => {
  const resolve = vi.fn(async (): Promise<PlaceAnswer> => ({ status: "found", zone: dubaiZone }));
  const { imageInfo } = renderArticle({ resolve });

  // The text does not wait for the picture.
  expect(await screen.findByRole("heading", { level: 1, name: "The war cannot be measured from orbit" })).toBeInTheDocument();
  const image = await screen.findByRole("img", { name: /Dubai/ });
  expect(image.getAttribute("src")).toBe(`/api/places/image?bbox=${encodeURIComponent(dubaiZone.bbox.join(","))}&width=1200`);
  expect(resolve).toHaveBeenCalledWith("Dubai", "en");
  expect(imageInfo).toHaveBeenCalledWith(dubaiZone.bbox);
  expect(screen.getByText(/Dubai, satellite view/)).toBeInTheDocument();
  expect(screen.getByText(/Contains Copernicus data/)).toBeInTheDocument();
});

test("a place that is not found, or is ambiguous, leaves no figure", async () => {
  const { resolve } = renderArticle();

  await screen.findByRole("heading", { level: 1, name: "The war cannot be measured from orbit" });
  await waitFor(() => expect(resolve).toHaveBeenCalled());
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(document.querySelector("figure")).toBeNull();
});

test("a zone the article already carries as an object is used without asking the gazetteer", async () => {
  const resolve = vi.fn(async (): Promise<PlaceAnswer> => ({ status: "not_found" }));
  const load = vi.fn(async () => ({ ...article, zone: dubaiZone }));
  renderArticle({ load, resolve });

  expect(await screen.findByRole("img", { name: /Dubai/ })).toBeInTheDocument();
  expect(resolve).not.toHaveBeenCalled();
});
