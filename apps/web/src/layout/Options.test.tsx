import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test } from "vitest";

import { I18nProvider } from "../i18n";
import { ThemeProvider } from "../theme";
import { UnitsProvider } from "../units";
import { AppBar } from "./AppBar";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-lang", "en");
  document.documentElement.removeAttribute("data-theme");
});

function openOptions() {
  render(
    <MemoryRouter>
      <I18nProvider>
        <ThemeProvider>
          <UnitsProvider>
            <AppBar />
          </UnitsProvider>
        </ThemeProvider>
      </I18nProvider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Options" }));
  return screen.getByRole("dialog", { name: "Options" });
}

test("the drawer has a title, an icon close button and three described settings", () => {
  const dialog = openOptions();

  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(within(dialog).getByRole("button", { name: "Close options" })).toHaveFocus();
  for (const name of ["Language", "Theme", "Units"]) {
    expect(within(dialog).getByRole("group", { name })).toBeInTheDocument();
  }
  expect(within(dialog).getByText("System follows the light or dark setting of your device.")).toBeInTheDocument();
});

test("the theme can follow the system, and the choice is remembered", () => {
  const dialog = openOptions();

  fireEvent.click(within(dialog).getByRole("button", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  fireEvent.click(within(dialog).getByRole("button", { name: "System" }));
  expect(within(dialog).getByRole("button", { name: "System" })).toHaveAttribute("aria-pressed", "true");
  expect(localStorage.getItem("hup-theme")).toBe("system");
  // jsdom has no dark system preference.
  expect(document.documentElement.dataset.theme).toBe("light");
});

test("the language can be chosen here too", () => {
  const dialog = openOptions();

  fireEvent.click(within(dialog).getByRole("button", { name: "Español" }));
  expect(screen.getByRole("dialog", { name: "Opciones" })).toBeInTheDocument();
});

test("Escape closes it and focus goes back to the settings button", () => {
  openOptions();

  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Options" })).toHaveFocus();
});

test("Tab stays inside the drawer", () => {
  const dialog = openOptions();
  const close = within(dialog).getByRole("button", { name: "Close options" });
  const buttons = within(dialog).getAllByRole("button");
  const last = buttons[buttons.length - 1];

  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(last).toHaveFocus();
});
