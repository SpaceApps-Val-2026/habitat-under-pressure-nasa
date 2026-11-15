import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test } from "vitest";

import { I18nProvider } from "../i18n";
import { ThemeProvider } from "../theme";
import { UnitsProvider } from "../units";
import { AppBar } from "./AppBar";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "es");
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <I18nProvider>
        <ThemeProvider>
          <UnitsProvider>
            <AppBar />
          </UnitsProvider>
        </ThemeProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
}

test("the bar names its sections next to the brand and marks the one open", () => {
  renderAt("/library");

  const sections = screen.getByRole("navigation", { name: "Secciones" });
  expect(sections).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Inicio" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("link", { name: "Biblioteca" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Inicio" })).not.toHaveAttribute("aria-current");
});

test("on the cover, home is the open section", () => {
  renderAt("/");

  expect(screen.getByRole("link", { name: "Inicio" })).toHaveAttribute("aria-current", "page");
});

test("four sections: What is this? and Manifesto join Home and Library", () => {
  renderAt("/manifesto");

  expect(screen.getByRole("link", { name: "¿Qué es esto?" })).toHaveAttribute("href", "/about");
  expect(screen.getByRole("link", { name: "Manifiesto" })).toHaveAttribute("aria-current", "page");
});

test("on a narrow screen the sections open from a menu button, and Escape closes it", () => {
  renderAt("/");

  const button = screen.getByRole("button", { name: "Menú" });
  expect(button).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(button);
  expect(button).toHaveAttribute("aria-expanded", "true");
  const menu = document.getElementById("app-menu") as HTMLElement;
  expect(within(menu).getAllByRole("link")).toHaveLength(4);
  expect(within(menu).getByRole("link", { name: "Inicio" })).toHaveAttribute("aria-current", "page");

  fireEvent.keyDown(window, { key: "Escape" });
  expect(document.getElementById("app-menu")).toBeNull();
  expect(button).toHaveFocus();
});
