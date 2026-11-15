import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import analysisExample from "../../../../docs/contratos/examples/analysis.json";
import resultsExample from "../../../../docs/contratos/examples/results.json";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";
import { Report } from "./Report";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "en");
});

afterEach(() => vi.unstubAllGlobals());

const bilingual = (en: string) => ({ en, es: en });

// The server: the analysis without the format's opening, its results, an empty catalogue, no place image,
// and, when asked with `editorial_style`, the analysis with the piece (or a failure).
function stubServer(id: string, piece: Record<string, unknown> | "fail") {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const analysis = { ...analysisExample, id };
      if (url.includes("editorial_style=")) {
        if (piece === "fail") return { ok: false, status: 500, json: async () => ({}) };
        const style = new URL(url, "http://local").searchParams.get("editorial_style") as string;
        return { ok: true, json: async () => ({ ...analysis, story: { ...analysis.story, editorial: { [style]: piece } } }) };
      }
      if (url.endsWith("/results")) return { ok: true, json: async () => resultsExample };
      if (url.startsWith("/api/catalog")) return { ok: true, json: async () => ({ sources: [], variables: [] }) };
      if (url.startsWith("/api/places")) return { ok: false, status: 404, json: async () => ({}) };
      if (url.startsWith("/api/legislation")) return { ok: true, json: async () => ({ items: [] }) };
      return { ok: true, json: async () => analysis };
    }),
  );
  return calls;
}

function renderAt(id: string, style: string) {
  return render(
    <MemoryRouter initialEntries={[`/a/${id}/report/${style}`]}>
      <I18nProvider>
        <UnitsProvider>
          <Routes>
            <Route path="/a/:analysisId/report/:style" element={<Report />} />
          </Routes>
        </UnitsProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

test("the institutional tab renders at once and asks once for its opening, which then feeds the summary", async () => {
  const calls = stubServer("editorial-a", {
    headline: bilingual("Environmental results for Parc Sagunt II"),
    lead: bilingual("The server's institutional lead."),
    meaning: bilingual("The server's meaning."),
    cannot_say: bilingual("The server's caveat."),
    results: [],
  });
  renderAt("editorial-a", "institutional");

  // At once: the descriptive title and the summary built from the data.
  const title = await screen.findByRole("heading", { level: 1, name: /Parc Sagunt II/ });
  const descriptive = title.textContent;
  // Then the piece: its lead, meaning and caveat in the summary; its generic headline is not adopted.
  await waitFor(() => expect(screen.getByText(/The server's institutional lead\./)).toBeInTheDocument());
  expect(screen.getByText(/The server's meaning\./)).toBeInTheDocument();
  expect(screen.getAllByText(/The server's caveat\./).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(descriptive);
  expect(calls.filter((url) => url.includes("editorial_style=institutional"))).toHaveLength(1);
});

test("a specific headline (the place and a subject) is adopted", async () => {
  stubServer("editorial-b", {
    headline: bilingual("Land surface temperature rises in Parc Sagunt II"),
    lead: bilingual("Lead."),
    meaning: bilingual("Meaning."),
    cannot_say: bilingual("Caveat."),
    results: [],
  });
  renderAt("editorial-b", "institutional");
  await waitFor(() =>
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Land surface temperature rises in Parc Sagunt II"),
  );
});

test("the scientific tab keeps its abstract and title and takes the server's caveat as main limitation", async () => {
  stubServer("editorial-c", {
    headline: bilingual("Land surface temperature rises in Parc Sagunt II"),
    lead: bilingual("Lead."),
    meaning: bilingual("Meaning."),
    cannot_say: bilingual("The scientific caveat."),
    results: [],
  });
  renderAt("editorial-c", "scientific");
  await waitFor(() => expect(screen.getByText(/Main limitation: The scientific caveat\./)).toBeInTheDocument());
  expect(screen.getByRole("heading", { level: 1 }).textContent).not.toBe("Land surface temperature rises in Parc Sagunt II");
});

test("a failed request leaves the generated document unchanged, and the story never asks", async () => {
  const calls = stubServer("editorial-d", "fail");
  renderAt("editorial-d", "institutional");
  const summary = (await screen.findByRole("heading", { name: "Summary" })).closest("section")!;
  await waitFor(() => expect(calls.some((url) => url.includes("editorial_style=institutional"))).toBe(true));
  expect(summary.querySelectorAll("li")).toHaveLength(3);

  const story = stubServer("editorial-e", "fail");
  renderAt("editorial-e", "journalistic");
  await screen.findAllByRole("heading", { level: 1 });
  expect(story.some((url) => url.includes("editorial_style="))).toBe(false);
});
