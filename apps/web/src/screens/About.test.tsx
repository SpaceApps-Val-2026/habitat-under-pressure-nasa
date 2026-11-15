import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test } from "vitest";

import { aboutEn } from "../content/about.en";
import { aboutEs } from "../content/about.es";
import { manifestoEn } from "../content/manifesto.en";
import { manifestoEs } from "../content/manifesto.es";
import { I18nProvider } from "../i18n";
import { About } from "./About";
import { Manifesto } from "./Manifesto";

beforeEach(() => localStorage.clear());

function renderIn(language: "en" | "es", node: React.ReactNode) {
  localStorage.setItem("hup-lang", language);
  return render(
    <MemoryRouter>
      <I18nProvider>{node}</I18nProvider>
    </MemoryRouter>,
  );
}

const load = async () => ({
  sources: [
    { id: "landsat-c2-l2", name: "Landsat 8/9 Collection 2 Level-2", provider: "USGS and NASA" },
    { id: "ghsl", name: "Global Human Settlement Layer population grid", provider: "European Commission Joint Research Centre" },
  ],
});

test("«What is this?» reads as an article, with the evidence levels and the catalogue's sources", async () => {
  renderIn("en", <About load={load} />);

  expect(screen.getByRole("heading", { level: 1, name: aboutEn.title })).toBeInTheDocument();
  for (const section of aboutEn.sections) expect(screen.getByRole("heading", { name: section.heading })).toBeInTheDocument();
  expect(screen.getByText("Association against a control")).toBeInTheDocument();
  expect(screen.getByText("Measured")).toBeInTheDocument();
  const sources = screen.getByRole("heading", { name: aboutEn.sourcesHeading }).closest("section") as HTMLElement;
  expect(await within(sources).findByText("Landsat 8/9 Collection 2 Level-2")).toBeInTheDocument();
  expect(within(sources).getByText("NASA")).toBeInTheDocument();
  expect(within(sources).getByText("Other sources")).toBeInTheDocument();
});

test("«¿Qué es esto?» en castellano", () => {
  renderIn("es", <About load={load} />);

  expect(screen.getByRole("heading", { level: 1, name: aboutEs.title })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: aboutEs.closing.heading })).toBeInTheDocument();
});

test("the manifesto lists its principles, numbered, in both languages", () => {
  const { unmount } = renderIn("en", <Manifesto />);
  expect(screen.getByRole("heading", { level: 1, name: manifestoEn.title })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(manifestoEn.principles.length);
  expect(screen.getByText("01")).toBeInTheDocument();
  unmount();

  renderIn("es", <Manifesto />);
  expect(screen.getByRole("heading", { level: 1, name: manifestoEs.title })).toBeInTheDocument();
  expect(screen.getByText(/borrador/)).toBeInTheDocument();
});
