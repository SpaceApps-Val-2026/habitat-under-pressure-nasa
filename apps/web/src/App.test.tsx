import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import analysisExample from "../../../docs/contratos/examples/analysis.json";
import resultsExample from "../../../docs/contratos/examples/results.json";
import { App } from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-units-choice", "metric");
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => vi.unstubAllGlobals());

const routes = [
  ["/", "One method. Any place on Earth."],
  ["/plan", "Check the plan"],
  ["/run/analysis-1", "Run"],
  ["/a/analysis-1", "Report"],
  ["/a/analysis-1/panel", "Dashboard"],
  ["/a/analysis-1/report/journalistic", "Report"],
  ["/library", "Library"],
  ["/about", "A question about a place, answered with Earth observation data"],
  ["/manifesto", "What we stand for"],
] as const;

function stubExamples() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return {
        ok: true,
        json: async () =>
          url.endsWith("/layers")
            ? { items: [] }
            : url.endsWith("/results")
              ? resultsExample
              : analysisExample,
      };
    }),
  );
}

test.each(routes)("route %s renders its title", async (path, title) => {
  renderAt(path);
  expect(await screen.findByRole("heading", { level: 1, name: title })).toBeInTheDocument();
});

test("the panel route renders an analysis when the API answers", async () => {
  stubExamples();

  renderAt("/a/analysis-1/panel");

  expect(
    await screen.findByRole("heading", { level: 1, name: /What changed around the Sagunt gigafactory/ }),
  ).toBeInTheDocument();
  expect(await screen.findByText("−0.32")).toBeInTheDocument();
});

test("the report route renders an analysis when the API answers", async () => {
  stubExamples();

  renderAt("/a/analysis-1/report/journalistic");

  expect(
    await screen.findByRole("heading", { level: 1, name: /Before \/ after with control: .* in Parc Sagunt II/ }),
  ).toBeInTheDocument();
  expect(screen.getByText(/Since 2023 the site runs 1\.1 °C hotter/)).toBeInTheDocument();
  expect(await screen.findByText("−0.32")).toBeInTheDocument();
  expect(document.querySelector(".report-byline")?.textContent).toMatch(/Analysis/);
  expect(document.querySelector("#sources")).not.toBeNull();
});

test("changing the language does not reload and is remembered", () => {
  const { unmount } = renderAt("/");
  expect(screen.getByRole("heading", { level: 1, name: "One method. Any place on Earth." })).toBeInTheDocument();

  fireEvent.click(document.querySelector("summary") as HTMLElement);
  fireEvent.click(screen.getByRole("button", { name: /Español/ }));

  expect(screen.getByRole("heading", { level: 1, name: "Un método. Cualquier lugar de la Tierra." })).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("es");
  expect(localStorage.getItem("hup-lang")).toBe("es");

  unmount();
  renderAt("/");
  expect(screen.getByRole("heading", { level: 1, name: "Un método. Cualquier lugar de la Tierra." })).toBeInTheDocument();
});

test("changing the theme does not reload and is remembered", () => {
  const { unmount } = renderAt("/");
  expect(document.documentElement.dataset.theme).toBe("light");

  fireEvent.click(screen.getByRole("button", { name: "Options" }));
  fireEvent.click(screen.getByRole("button", { name: "Dark" }));

  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("hup-theme")).toBe("dark");

  unmount();
  renderAt("/");
  expect(document.documentElement.dataset.theme).toBe("dark");
});

test("the options menu changes the units and remembers them", () => {
  renderAt("/");

  fireEvent.click(screen.getByRole("button", { name: "Options" }));
  fireEvent.click(screen.getByRole("button", { name: "Fahrenheit" }));

  expect(localStorage.getItem("hup-units-choice")).toBe("imperial");
});

test("renders when localStorage is blocked", () => {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  });

  renderAt("/");
  expect(screen.getByRole("heading", { level: 1, name: "One method. Any place on Earth." })).toBeInTheDocument();
});

test("the report of a cancelled analysis says so and offers to ask again", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      return {
        ok: true,
        json: async () =>
          url.endsWith("/results") ? [] : { ...analysisExample, status: "cancelled" },
      };
    }),
  );

  renderAt("/a/analysis-1");

  expect(await screen.findByText("The analysis was cancelled before it finished; it has no answer.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Ask again" })).toHaveAttribute("href", "/");
});
