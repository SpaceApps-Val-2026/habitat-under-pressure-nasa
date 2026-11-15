import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import type { AnalysisSummary, PlaceImageCredit } from "../api/client";
import type { plan } from "../contracts";
import { I18nProvider } from "../i18n";
import { Library } from "./Library";

const items: AnalysisSummary[] = [
  {
    id: "sagunt-impact-example",
    title: { en: "What changed around the Sagunt gigafactory", es: "Qué cambió alrededor de la gigafactoría" },
    summary: { en: "Vegetation and surface heat around the site.", es: "Vegetación y calor de superficie." },
    design: "impact",
    zone: { id: "parc-sagunt-ii", name: "Parc Sagunt II", bbox: [-0.35, 39.6, -0.15, 39.75] },
    created: "2026-11-13T22:09:20Z",
    status: "finished",
    featured: true,
  },
  {
    id: "peniscola-scan",
    title: { en: "Peñíscola: Vegetation greenness (NDVI), Land surface temperature, Air temperature" },
    summary: null,
    design: "trend",
    zone: {
      id: "peniscola",
      name: "Peñíscola, Baix Maestrat, Castelló, Comunitat Valenciana, 12598, España",
      bbox: [0.36, 40.33, 0.42, 40.4],
    },
    created: "2026-11-13T22:25:54Z",
    status: "finished",
    featured: false,
  },
];

function fakeLoad(params: { q?: string; design?: string; featured?: boolean } = {}) {
  const q = (params.q ?? "").toLowerCase();
  return Promise.resolve({
    items: items.filter(
      (item) =>
        (!q ||
          [item.title, item.summary]
            .filter((text): text is NonNullable<AnalysisSummary["title"]> => Boolean(text))
            .some((text) => Object.values(text).some((value) => value.toLowerCase().includes(q)))) &&
        (!params.design || item.design === params.design) &&
        (!params.featured || item.featured),
    ),
  });
}

// No saved plan by default: the titles come from the list.
const noPlan = async () => ({ plan: null });
const noCredit = async (): Promise<PlaceImageCredit | null> => null;

function renderLibrary(
  load = fakeLoad,
  read: (id: string) => Promise<{ plan?: plan.AnalysisPlan | null }> = noPlan,
  credit = noCredit,
) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <Library load={load} read={read} credit={credit} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => localStorage.clear());

test("lists the analyses with a readable title, a short place, the design and the date", async () => {
  renderLibrary();

  expect(await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" })).toBeInTheDocument();
  // A title that is a list of variables is cut to its place and the design.
  expect(screen.getByRole("heading", { name: "Peñíscola · Trend" })).toBeInTheDocument();
  expect(screen.getByText(/^Peñíscola, Comunitat Valenciana · /)).toBeInTheDocument();
  expect(screen.getByText("Before / after with control")).toBeInTheDocument();
  expect(screen.getByText("Featured")).toBeInTheDocument();
  expect(screen.queryByText("Finished")).not.toBeInTheDocument();
  expect(screen.getByText(/^Parc Sagunt II · /)).toHaveTextContent(
    new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(items[0].created)),
  );
});

test("the person's question replaces the saved title when it arrives", async () => {
  const read = vi.fn(async (id: string) => ({
    plan: (id === "peniscola-scan"
      ? { request: { text: "How is Peñíscola doing?", language: "en" }, variables: ["ndvi"], zone: items[1].zone }
      : null) as unknown as plan.AnalysisPlan,
  }));
  renderLibrary(fakeLoad, read);

  expect(await screen.findByRole("heading", { name: "How is Peñíscola doing?" })).toBeInTheDocument();
  expect(read).toHaveBeenCalledTimes(2);
});

test("each card shows a small picture of its place, lazy, and one credit line", async () => {
  const credit = vi.fn(async () => ({ credit: { en: "Contains modified Copernicus Sentinel data 2025" } }));
  const { container } = renderLibrary(fakeLoad, noPlan, credit);

  await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" });
  const pictures = container.querySelectorAll("img");
  expect(pictures).toHaveLength(2);
  expect(pictures[0].getAttribute("src")).toBe("/api/places/image?bbox=-0.35%2C39.6%2C-0.15%2C39.75&width=160");
  expect(pictures[0].getAttribute("loading")).toBe("lazy");
  expect(await screen.findByText("Place images: Contains modified Copernicus Sentinel data 2025")).toBeInTheDocument();
  expect(credit).toHaveBeenCalledTimes(1);

  fireEvent.error(pictures[0]);
  expect(container.querySelectorAll("img")).toHaveLength(1);
});

test("the search reduces the list and has its magnifier", async () => {
  const { container } = renderLibrary();
  await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" });

  expect(container.querySelector("#library-q")?.parentElement?.querySelector("svg")).not.toBeNull();
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "gigafactory" } });

  await waitFor(() => expect(screen.queryByRole("heading", { name: "Peñíscola · Trend" })).not.toBeInTheDocument());
  expect(screen.getByRole("heading", { name: "What changed around the Sagunt gigafactory" })).toBeInTheDocument();
});

test("the design drop-down is a listbox that works with the mouse and the keyboard", async () => {
  renderLibrary();
  await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" });

  const button = screen.getByRole("button", { name: /Design/ });
  expect(button).toHaveAttribute("aria-haspopup", "listbox");
  fireEvent.click(button);
  const list = screen.getByRole("listbox", { name: "Design" });
  expect(within(list).getByRole("option", { name: "All designs" })).toHaveAttribute("aria-selected", "true");

  fireEvent.click(within(list).getByRole("option", { name: "Trend" }));
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(button).toHaveFocus();
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "What changed around the Sagunt gigafactory" })).not.toBeInTheDocument(),
  );

  // Keyboard: open with the arrow, Home, Enter; Escape closes without choosing.
  fireEvent.keyDown(button, { key: "ArrowDown" });
  const again = screen.getByRole("listbox");
  fireEvent.keyDown(again, { key: "Home" });
  expect(again.getAttribute("aria-activedescendant")).toMatch(/-0$/);
  fireEvent.keyDown(again, { key: "Enter" });
  expect(button).toHaveTextContent("All designs");
  expect(await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" })).toBeInTheDocument();

  fireEvent.keyDown(button, { key: "ArrowDown" });
  fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

  fireEvent.click(button);
  act(() => {
    document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

test("featured only is a switch that filters", async () => {
  renderLibrary();
  await screen.findByRole("heading", { name: "Peñíscola · Trend" });

  const toggle = screen.getByRole("switch", { name: "Featured only" });
  fireEvent.click(toggle);

  await waitFor(() => expect(screen.queryByRole("heading", { name: "Peñíscola · Trend" })).not.toBeInTheDocument());
});

test("an empty library and a search with no matches say different things", async () => {
  const { unmount } = renderLibrary(async () => ({ items: [] }));
  expect(await screen.findByText("No analyses yet.")).toBeInTheDocument();
  unmount();

  renderLibrary();
  await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" });
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "nothing matches this" } });
  expect(await screen.findByText("No analyses match these filters.")).toBeInTheDocument();
});

test("the whole card opens the report and offers the run as a small link", async () => {
  renderLibrary();
  const row = (await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" })).closest("li");
  expect(row).not.toBeNull();

  expect(within(row as HTMLElement).getByRole("link", { name: "What changed around the Sagunt gigafactory" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example",
  );
  expect(within(row as HTMLElement).getByRole("link", { name: "See the run" })).toHaveAttribute(
    "href",
    "/run/sagunt-impact-example?replay=1",
  );
});

test("a planned analysis says so in words", async () => {
  renderLibrary(async () => ({ items: [{ ...items[0], status: "planned" }] }));

  expect(await screen.findByText("Planned")).toBeInTheDocument();
});

test("a missing zone is declared instead of hidden", async () => {
  renderLibrary(async () => ({ items: [{ ...items[0], zone: null }] }));

  expect(await screen.findByText(/^No zone · /)).toBeInTheDocument();
});

test("a failed load offers a retry that works", async () => {
  let calls = 0;
  const load = async () => {
    calls += 1;
    if (calls === 1) throw new Error("down");
    return { items };
  };
  renderLibrary(load);

  expect(await screen.findByText("The list could not be loaded.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByRole("heading", { name: "What changed around the Sagunt gigafactory" })).toBeInTheDocument();
});

test("the filters and rows read in Spanish", async () => {
  localStorage.setItem("hup-lang", "es");
  renderLibrary();

  expect(await screen.findByRole("heading", { name: "Biblioteca" })).toBeInTheDocument();
  expect(screen.getByLabelText("Buscar")).toBeInTheDocument();
  expect(screen.getAllByText("Ver la ejecución")).toHaveLength(2);
  expect(screen.getByRole("switch", { name: "Solo destacados" })).toBeInTheDocument();
});

test("a cancelled analysis, if the list ever carries one, says so", async () => {
  renderLibrary(async () => ({ items: [{ ...items[0], status: "cancelled" as AnalysisSummary["status"] }] }));

  expect(await screen.findByText("Cancelled")).toBeInTheDocument();
});
