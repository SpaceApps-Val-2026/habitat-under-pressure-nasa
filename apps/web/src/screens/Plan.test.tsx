import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";

import catalogExample from "../../../../docs/contratos/examples/catalog.json";
import planExample from "../../../../docs/contratos/examples/plan.json";
import type { PlanValidation } from "../api/client";
import type { plan } from "../contracts";
import { I18nProvider } from "../i18n";
import { PlanReview, type CatalogLike } from "./Plan";

const base = planExample as unknown as plan.AnalysisPlan;
const catalog = catalogExample as unknown as CatalogLike;

function valid(candidate: plan.AnalysisPlan): PlanValidation {
  return { valid: true, errors: [], plan: candidate, plan_fingerprint: null, existing_analysis: null };
}

function review(overrides: Partial<plan.AnalysisPlan> = {}, validate = vi.fn(async (candidate: plan.AnalysisPlan) => valid(candidate))) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <PlanReview initialPlan={{ ...base, ...overrides }} catalog={catalog} validate={validate} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

test("the example plan shows its seven steps, its limit and the package step", async () => {
  review();
  expect(await screen.findByText("Ready to run.")).toBeInTheDocument();

  // Seven steps, each in the catalogue's words: no step ids and no operation ids.
  expect(screen.getAllByText(/^Reading scenes from (Landsat|SMAP|NASA)/)).toHaveLength(3);
  expect(screen.getAllByText("Comparing the site with its surroundings, before and after")).toHaveLength(2);
  expect(screen.getByText("Writing the findings")).toBeInTheDocument();
  expect(screen.getByText("Checking every figure against its result")).toBeInTheDocument();
  expect(screen.queryByText("s1")).not.toBeInTheDocument();
  expect(screen.queryByText("read_series")).not.toBeInTheDocument();
  expect(screen.getByText(/Soil moisture is not available/)).toBeInTheDocument();
  expect(screen.getByText("Needs package")).toBeInTheDocument();
  expect(screen.getByText(/This source needs a downloaded package/)).toBeInTheDocument();
});

test("complete, with limits and not possible read differently without their colour", async () => {
  const complete = review({ answer: { level: "complete" } });
  expect(await screen.findByText("Complete")).toBeInTheDocument();
  expect(screen.queryByText("Limits")).not.toBeInTheDocument();
  complete.unmount();

  const withLimits = review({ answer: { level: "with_limits", limits: [{ en: "A limit to read." }] } });
  expect(await screen.findByText("With limits")).toBeInTheDocument();
  expect(await screen.findByText("A limit to read.")).toBeInTheDocument();
  withLimits.unmount();

  review({
    answer: { level: "not_possible", alternative: { en: "A ten-year trend instead." } },
  });
  expect(await screen.findByText("Not possible")).toBeInTheDocument();
  expect(screen.getByText(/A ten-year trend instead/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Ask this instead" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run this plan" })).toBeDisabled();
});

test("the answer level comes from the plan the server returns", async () => {
  const validate = vi.fn(async (candidate: plan.AnalysisPlan) => ({
    ...valid(candidate),
    plan: { ...candidate, answer: { level: "complete" as const } },
  }));
  review({ answer: { level: "with_limits", limits: [{ en: "A limit the server removed." }] } }, validate);

  expect(await screen.findByText("Complete")).toBeInTheDocument();
  expect(screen.queryByText("With limits")).not.toBeInTheDocument();
  expect(screen.queryByText("A limit the server removed.")).not.toBeInTheDocument();
});

test("a plan that already has an analysis offers to open it", async () => {
  const validate = vi.fn(async (candidate: plan.AnalysisPlan) => ({
    ...valid(candidate),
    existing_analysis: "sagunt-impact-example",
  }));
  review({}, validate);

  expect(await screen.findByText("This plan has already been run.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open the existing analysis" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example",
  );
});

test("server errors land next to the plan or next to their step", async () => {
  const validate = vi.fn(async (candidate: plan.AnalysisPlan) => ({
    ...valid(candidate),
    valid: false,
    errors: ["plan: variable outside the catalog: nope", "step s1: operation outside the catalog: nope"],
  }));
  review({}, validate);

  expect(await screen.findByText("! variable outside the catalog: nope")).toBeInTheDocument();
  const step = screen.getAllByText(/^Reading scenes from/)[0].closest("li");
  expect(step).not.toBeNull();
  expect(within(step as HTMLElement).getByText("! operation outside the catalog: nope")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Run this plan" })).toBeDisabled();
});

test("changing a variable sends the edited plan to validate", async () => {
  const validate = vi.fn(async (candidate: plan.AnalysisPlan) => valid(candidate));
  review({}, validate);
  await screen.findByText("Ready to run.");

  fireEvent.click(screen.getByRole("checkbox", { name: /Vegetation greenness/ }));

  await waitFor(() =>
    expect(validate).toHaveBeenLastCalledWith(expect.objectContaining({ variables: ["lst", "soil_moisture"] })),
  );
});

test("the plan of a running analysis explains it and links back, without running it again", async () => {
  const decided = {
    ...base,
    answer: { ...base.answer, decided: [{ field: "period", text: { en: "Period: the whole record." } }] },
  } as unknown as plan.AnalysisPlan;
  render(
    <MemoryRouter>
      <I18nProvider>
        <PlanReview initialPlan={decided} catalog={catalog} validate={vi.fn(async (candidate: plan.AnalysisPlan) => valid(candidate))} analysisId="run-1" />
      </I18nProvider>
    </MemoryRouter>,
  );

  expect(await screen.findByText("Period: the whole record.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Back to the run" })).toHaveAttribute("href", "/run/run-1");
  expect(screen.getByRole("link", { name: "Open the report" })).toHaveAttribute("href", "/a/run-1/report/journalistic");
  expect(screen.queryByRole("button", { name: "Run this plan" })).not.toBeInTheDocument();
});
