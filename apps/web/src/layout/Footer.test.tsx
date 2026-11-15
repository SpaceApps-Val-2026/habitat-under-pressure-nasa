import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test } from "vitest";

import type { CatalogSourceLike } from "../content/sources";
import { I18nProvider } from "../i18n";
import { Footer, type LogoEntry } from "./Footer";

const sources: CatalogSourceLike[] = [
  { id: "nasa-power", name: "NASA POWER", provider: "NASA LaRC", access: { url: "https://power.larc.nasa.gov/api/temporal/monthly/point" } },
  { id: "imerg-monthly", name: "GPM IMERG Final Precipitation L3, monthly", provider: "NASA GES DISC", access: { url: "https://doi.org/10.5067/GPM/IMERG/3B-MONTH/07" } },
  { id: "sentinel-1-rtc", name: "Sentinel-1 RTC", provider: "European Space Agency (Copernicus Sentinel-1)" },
  { id: "ine", name: "INE census section boundaries", provider: "Instituto Nacional de Estadística", access: { url: "https://www.ine.es/" } },
];

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "en");
});

function renderFooter(logos: LogoEntry[] = []) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <Footer catalog={async () => ({ sources })} logos={async () => logos} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

test("the footer lists the sources the app uses, grouped by agency, generated from the catalogue", async () => {
  renderFooter();

  const data = await screen.findByRole("region", { name: "Data from" });
  expect(within(data).getByText("NASA")).toBeInTheDocument();
  expect(within(data).getByText("ESA and Copernicus")).toBeInTheDocument();
  expect(within(data).getByText("Other sources")).toBeInTheDocument();
  // A page links; an API endpoint does not.
  expect(within(data).getByRole("link", { name: /GPM IMERG/ })).toHaveAttribute("href", "https://doi.org/10.5067/GPM/IMERG/3B-MONTH/07");
  expect(within(data).queryByRole("link", { name: "NASA POWER" })).not.toBeInTheDocument();
  expect(within(data).getByText("NASA POWER")).toBeInTheDocument();
});

test("the footer has the event, the pages, the code and the licence, and hides in print", async () => {
  const { container } = renderFooter();

  expect(screen.getByText("NASA Space Apps Challenge 2026 · València")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "What is this?" })).toHaveAttribute("href", "/about");
  expect(screen.getByRole("link", { name: "Manifesto" })).toHaveAttribute("href", "/manifesto");
  expect(screen.getByRole("link", { name: "Library" })).toHaveAttribute("href", "/library");
  expect(screen.getByRole("link", { name: "Source code" })).toHaveAttribute(
    "href",
    "https://github.com/SpaceApps-Val-2026/Habitat-Under-Pressure",
  );
  expect(screen.getByText(/Apache 2.0/)).toBeInTheDocument();
  expect(container.querySelector("footer")?.className).toContain("print:hidden");
  // The project's own mark, as in the header, decorative next to its name; the pages carry a visible label.
  expect(container.querySelector("footer img[alt='']")).toHaveAttribute("src", expect.stringContaining("logo"));
  expect(screen.getByRole("navigation", { name: "Pages" })).toContainElement(screen.getByRole("link", { name: "Manifesto" }));
  await screen.findByRole("region", { name: "Data from" });
});

test("no logo is drawn unless the team lists it; a listed logo that fails goes back to the name", async () => {
  const { container, unmount } = renderFooter();
  await screen.findByRole("region", { name: "Data from" });
  expect(container.querySelectorAll("img:not([alt=''])")).toHaveLength(0);
  unmount();

  const second = renderFooter([{ key: "nasa", file: "nasa.svg", alt: "NASA" }]);
  const image = await screen.findByRole("img", { name: "NASA" });
  expect(image).toHaveAttribute("src", "/logos/nasa.svg");
  fireEvent.error(image);
  expect(second.container.querySelectorAll("img:not([alt=''])")).toHaveLength(0);
  expect(screen.getAllByText("NASA").length).toBeGreaterThanOrEqual(1);
});

test("a logo with a version per theme draws each in its theme; one made for the other background goes on a plate", async () => {
  renderFooter([
    { key: "esa", alt: "ESA", dark: "esa.dark.svg", light: "esa.light.png" },
    { key: "ine-es", alt: "Instituto Nacional de Estadística", light: "ine.light.svg" },
    { key: "nasa", alt: "NASA", file: "nasa.svg" },
  ]);
  const data = await screen.findByRole("region", { name: "Data from" });

  const esa = within(data).getAllByRole("img", { name: "ESA" });
  expect(esa.map((image) => image.getAttribute("src"))).toEqual(["/logos/esa.light.png", "/logos/esa.dark.svg"]);
  expect(esa[0].className).toContain("dark:hidden");
  expect(esa[1].className).toContain("dark:block");
  expect(esa.every((image) => !image.parentElement?.className.includes("bg-orbit"))).toBe(true);
  // The group label stays as text next to its logos.
  expect(within(data).getByText("ESA and Copernicus")).toBeInTheDocument();

  // Only a light-background version: plain in the light theme, on the pale plate in the dark one.
  const ine = within(data).getAllByRole("img", { name: "Instituto Nacional de Estadística" });
  expect(ine.map((image) => image.getAttribute("src"))).toEqual(["/logos/ine.light.svg", "/logos/ine.light.svg"]);
  expect(ine[0].className).toContain("dark:hidden");
  expect(ine[1].parentElement?.className).toContain("bg-orbit-ink");
  expect(ine[1].parentElement?.className).toContain("dark:inline-flex");
  expect(ine[1]).toHaveAttribute("title", "Instituto Nacional de Estadística");

  // One file for both themes: a single image.
  expect(within(data).getAllByRole("img", { name: "NASA" })).toHaveLength(1);
});

test("only the logos under «show» in logos.json are drawn; «held» ones wait for permission", async () => {
  const manifest = {
    show: [{ key: "space-apps", alt: "NASA Space Apps Challenge", dark: "space-apps.dark.png" }],
    held: [{ key: "nasa", alt: "NASA", file: "nasa.svg" }],
  };
  const original = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify(manifest))) as typeof fetch;
  try {
    render(
      <MemoryRouter>
        <I18nProvider>
          <Footer catalog={async () => ({ sources })} />
        </I18nProvider>
      </MemoryRouter>,
    );
    // A white logo only: on the dark plate in the light theme, plain in the dark one.
    const event = await screen.findAllByRole("img", { name: "NASA Space Apps Challenge" });
    expect(event.map((image) => image.getAttribute("src"))).toEqual(["/logos/space-apps.dark.png", "/logos/space-apps.dark.png"]);
    expect(event[0].parentElement?.className).toContain("bg-orbit");
    expect(event[1].className).toContain("dark:block");
    // The event line stays as text: the global logo does not say where or when.
    expect(screen.getByText("NASA Space Apps Challenge 2026 · València")).toBeInTheDocument();
    await screen.findByRole("region", { name: "Data from" });
    expect(screen.queryByRole("img", { name: "NASA" })).not.toBeInTheDocument();
  } finally {
    globalThis.fetch = original;
  }
});
