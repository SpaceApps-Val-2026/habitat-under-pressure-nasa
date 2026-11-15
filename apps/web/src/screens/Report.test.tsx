import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import analysisExample from "../../../../docs/contratos/examples/analysis.json";
import resultsExample from "../../../../docs/contratos/examples/results.json";
import type { analysis, result } from "../contracts";
import { I18nProvider } from "../i18n";
import { UnitsProvider } from "../units";
import { fetchPlaceImage } from "../blocks/PlaceImage";
import { ReportView } from "./Report";

const saved = analysisExample as unknown as analysis.SavedAnalysis;
const results = resultsExample as unknown as result.StepResult[];

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("hup-units-choice", "metric");
});

function renderReport(style = "journalistic", value = saved, stepResults = results) {
  return render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <ReportView analysis={value} results={stepResults} style={style} />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );
}

test("the journalistic report draws the blocks of its story and the mandatory footer", () => {
  renderReport();

  expect(screen.getByText("−0.32")).toBeInTheDocument();
  // In the piece, the evidence is a tag beside each figure, not a box.
  expect(screen.getAllByText("Coincidence in time and place").length).toBeGreaterThanOrEqual(1);
  expect(screen.queryByText("Evidence level")).toBeNull();
  expect(screen.getAllByText(/sagunt-impact-example/).length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText(new RegExp(saved.fingerprint.sha256))).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Journalistic" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Institutional" })).toHaveAttribute(
    "href",
    "/a/sagunt-impact-example/report/institutional",
  );
});

test("without an editorial piece, the headline names design, variables and place and the finding is the lead", () => {
  renderReport();

  // The question is in Spanish and the screen in English: the composed headline holds the page.
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
    "Before / after with control: Vegetation greenness (NDVI) and Land surface temperature in Parc Sagunt II",
  );
  expect(screen.getByText(/Since 2023 the site runs 1.1 °C hotter than its surroundings/)).toBeInTheDocument();
});

test("the person's question is the kicker, small, and never the headline twice", () => {
  localStorage.setItem("hup-lang", "es");
  renderReport();

  expect(screen.getByText(saved.plan.request.text)).toHaveClass("report-kicker");
  expect(screen.getByRole("heading", { level: 1 }).textContent).not.toBe(saved.plan.request.text);
});

test("the server's editorial piece opens the report, filled from the results it cites", () => {
  const finding = saved.findings.find((item) => item.text?.en.includes("{"));
  const placeholder = finding?.text?.en.match(/\{[^}]+\.value\}/)?.[0] ?? "";
  const editorial = {
    journalistic: {
      headline: { en: "The site is hotter than its surroundings", es: "La parcela está más caliente que su entorno" },
      lead: { en: `The contrast is ${placeholder}.`, es: `El contraste es ${placeholder}.` },
      meaning: { en: "It describes the site, not the town.", es: "Describe la parcela, no el municipio." },
      cannot_say: { en: "It cannot say who caused it {missing.value}.", es: "No dice quién lo causó {missing.value}." },
      results: finding?.results ?? [],
    },
  };
  renderReport("journalistic", { ...saved, story: { ...saved.story, editorial } as unknown as typeof saved.story });

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("The site is hotter than its surroundings");
  expect(screen.getByText(/^The contrast is /)).toBeInTheDocument();
  // Without a body, what it means is the nudo.
  // A lone short paragraph is a standout, without a drop cap, its notes attached to its last word.
  const standout = document.querySelector(".report-prose p")!;
  expect(standout).toHaveClass("standout");
  expect(standout).not.toHaveClass("lead");
  expect(standout.textContent).toMatch(/^It describes the site, not the town\./);
  expect(standout.querySelector(".report-nowrap")?.textContent).toMatch(/^town\./);
  // A field that cannot be filled is left out, never shown with its placeholder.
  expect(screen.queryByText("What we cannot say")).toBeNull();
  expect(document.body.textContent).not.toContain("{");
});

test("the institutional report is a briefing note: summary, indicators table, open law, grouped limits, use", () => {
  renderReport("institutional");

  expect(screen.getByText("Institutional report")).toHaveClass("report-doc-label");
  // A sober descriptive title, not the story's headline.
  expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/ in Parc Sagunt II, /);
  const summary = screen.getByRole("heading", { name: "Summary" }).closest("section")!;
  expect(summary.querySelectorAll("li")).toHaveLength(3);
  expect(summary).toHaveTextContent("What was analysed");
  expect(screen.getByRole("table")).toHaveTextContent("Interval");
  expect(screen.getByRole("heading", { name: /Legal framework/ })).toBeInTheDocument();
  expect(document.querySelector(".report-law details")).toHaveAttribute("open");
  expect(screen.getByRole("heading", { name: "About attribution" })).toBeInTheDocument();
  expect(screen.getAllByText("It shows what changed, not who caused it.").length).toBeGreaterThanOrEqual(1);
  expect(screen.getByRole("heading", { name: "What this figure is for, and what it is not for" })).toBeInTheDocument();
});

test("the scientific report is a technical report: abstract, numbered sections, every result, method, reproducibility", () => {
  renderReport("scientific");

  expect(screen.getByText("Technical report")).toHaveClass("report-doc-label");
  expect(screen.getByRole("heading", { name: "Abstract" })).toBeInTheDocument();
  for (const name of ["Data", "Method", "Results", "Diagnostics", "Limitations and absences", "Reproducibility", "References"]) {
    expect(screen.getByRole("heading", { name })).toBeInTheDocument();
  }
  // Every non-layer result is a row, absences included, with its id in mono.
  const table = screen.getByRole("heading", { name: "Results" }).closest("section")!.querySelector("table")!;
  expect(table.querySelectorAll("tbody tr")).toHaveLength(results.filter((item) => item.type !== "layer").length);
  expect(table).toHaveTextContent("r-s3-series");
  // The plan's steps with their operation id and version.
  expect(screen.getByRole("heading", { name: "Method" }).closest("section")!.querySelectorAll(".report-steps li").length).toBe(
    saved.plan.steps.length,
  );
  expect(screen.getByRole("heading", { name: "Reproducibility" }).closest("section")).toHaveTextContent(saved.fingerprint.sha256);
  expect(screen.getByRole("link", { name: "Run the analysis again" })).toHaveAttribute("href", "/run/sagunt-impact-example?replay=1");
});

test("an analysis without a story shows a message and the blocks its results allow", () => {
  renderReport("journalistic", { ...saved, story: undefined });

  expect(screen.getByText(/no story in this format/i)).toBeInTheDocument();
  expect(screen.getByText("−0.32")).toBeInTheDocument();
  expect(screen.getByText("Sources")).toBeInTheDocument();
});

test("no unresolved placeholder reaches the page", () => {
  renderReport();

  expect(document.body.textContent).not.toContain("{");
});

test("the footer names the sources and the analysis is in the byline", () => {
  renderReport();

  expect(screen.getByText(/Analysis: sagunt-impact-example/)).toBeInTheDocument();
  expect(document.querySelector("#sources")).not.toBeNull();
});

test("the report reads in Spanish too", () => {
  localStorage.setItem("hup-lang", "es");
  renderReport();

  expect(screen.getByRole("link", { name: "Periodístico" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { name: "Lo que esto no dice" })).toBeInTheDocument();
});

test("the story reads kicker, headline, lead, nudo with its featured figure, desenlace and caveat", () => {
  const finding = saved.findings.find((item) => item.text?.en.includes("{"));
  const cited = finding?.results[0] ?? "";
  const editorial = {
    journalistic: {
      headline: { en: "The site is hotter", es: "La parcela está más caliente" },
      lead: { en: "A lead.", es: "Una entradilla." },
      meaning: { en: "What it means.", es: "Qué significa." },
      cannot_say: { en: "It cannot say who caused it.", es: "No dice quién lo causó." },
      body: [{ en: "First paragraph.", es: "Primer párrafo." }, { en: "Second paragraph.", es: "Segundo párrafo." }],
      closing: { en: "The ending.", es: "El final." },
      featured: cited,
      results: finding?.results ?? [],
    },
  };
  renderReport("journalistic", { ...saved, story: { ...saved.story, editorial } as unknown as typeof saved.story });

  expect(screen.getByText(saved.plan.request.text)).toHaveClass("report-kicker");
  expect(screen.getByText("A lead.")).toHaveClass("report-standfirst");
  // A short first paragraph of several reads plain: a drop cap needs at least three lines.
  const first = document.querySelector(".report-prose p")!;
  expect(first.textContent).toMatch(/^First paragraph\./);
  expect(first).not.toHaveClass("lead");
  expect(first).not.toHaveClass("standout");
  expect(screen.getByText("Second paragraph.").closest(".report-prose")).not.toBeNull();
  // The key figures come before the text (this example has no drawable picture: its series is absent).
  const figures = document.querySelector(".report-figures");
  const prose = document.querySelector(".report-prose");
  expect(figures).not.toBeNull();
  expect(figures!.compareDocumentPosition(prose!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText("The ending.").closest(".report-prose")).not.toBeNull();
  expect(screen.getByText("What it means.").closest(".report-prose")).not.toBeNull();
  // What this does not say: the editor's caveat and at most three limits of the cited results.
  const limits = screen.getByRole("complementary", { name: "What this does not say" });
  expect(limits).toHaveTextContent("It cannot say who caused it.");
  expect(limits.querySelectorAll("li").length).toBeLessThanOrEqual(4);
  expect(screen.getByRole("complementary", { name: "How we know" })).toBeInTheDocument();
  expect(document.getElementById("sources")).not.toBeNull();
});

test("the place is always shown from the satellite, first, with its source numbered last", () => {
  const info = {
    available: true,
    source: { id: "sentinel-2-l2a", name: { en: "Sentinel-2 L2A", es: "Sentinel-2 L2A" } },
    period: "2025-07",
    resolution_m: 10,
    credit: { en: "Contains modified Copernicus Sentinel data", es: "Contiene datos modificados de Copernicus Sentinel" },
    license: { en: "Free and open", es: "Libre y abierto" },
  };
  render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <ReportView analysis={saved} results={results} style="journalistic" placeImage={info} />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );

  const image = screen.getByRole("img", { name: /Satellite image of Parc Sagunt II/ });
  expect(image).toHaveAttribute("loading", "lazy");
  expect(image.getAttribute("src")).toContain("/api/places/image?bbox=");
  const place = image.closest("figure")!;
  // Right after the key figures and before any other figure.
  expect(document.querySelector(".report-figures")!.compareDocumentPosition(place) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(document.querySelectorAll(".report-figure")[0]).toBe(place);
  expect(place).toHaveTextContent("Sentinel-2 L2A · 2025-07 · Resolution: 10 m · Contains modified Copernicus Sentinel data");
  const items = document.querySelectorAll("#sources ol > li");
  const last = items[items.length - 1];
  expect(last.id).toBe(`source-${items.length}`);
  expect(last).toHaveTextContent("Free and open");
  expect(place.querySelector(`a.fn[href="#source-${items.length}"]`)).not.toBeNull();
});

test("without the image route, the report draws no empty frame", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
  await expect(fetchPlaceImage([-0.3, 39.6, -0.2, 39.7])).resolves.toBeNull();
  vi.unstubAllGlobals();
  renderReport();
  expect(screen.queryByRole("img", { name: /Satellite image/ })).toBeNull();
});

test("key figures read with three significant figures, and a percentile as P", () => {
  const value = results.find((item) => item.type === "value" && !item.absence)!;
  const percentile = { ...value, id: "r-p", unit: "percentile", value: 84.62, interval: undefined, method: { operation: "rain_extreme", version: "1" } };
  const precise = { ...value, id: "r-q", value: -0.2125 };
  const analysis = { ...saved, story: { journalistic: [] } as unknown as typeof saved.story };
  render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <ReportView analysis={analysis} results={[percentile, precise] as typeof results} style="journalistic" />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );
  expect(screen.getByText("P85")).toBeInTheDocument();
  expect(screen.getByText(/percentile 84\.6/)).toBeInTheDocument();
  expect(document.body.textContent).toContain("−0.213");
  expect(document.body.textContent).not.toContain("0.2125");
});

test("an illustrative figure carries a golden asterisk whose tooltip opens on focus and closes on Escape", () => {
  const value = results.find((item) => item.type === "value" && !item.absence)!;
  const illustrative = { ...value, id: "r-i", data_class: "illustrative" };
  const analysis = { ...saved, story: { journalistic: [] } as unknown as typeof saved.story };
  render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <ReportView analysis={analysis} results={[illustrative] as typeof results} style="journalistic" />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );
  const mark = screen.getByRole("button", { name: "Illustrative value" });
  expect(document.querySelector(".report-figures")).toHaveAttribute("data-count", "1");
  expect(screen.queryByText("Illustrative", { selector: ".tag" })).toBeNull();
  fireEvent.focus(mark);
  expect(mark).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Illustrative value");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(mark).toHaveAttribute("aria-expanded", "false");
});

test("the other formats do not borrow the story's headline; their own editorial headline wins when sent", () => {
  const editorial = {
    journalistic: { headline: { en: "Story headline", es: "Titular" }, results: [] },
    institutional: {
      headline: { en: "Land surface temperature rises in Parc Sagunt II", es: "Sube la temperatura de superficie en Parc Sagunt II" },
      results: [],
    },
  };
  const story = saved.story as Record<string, unknown>;
  renderReport("scientific", { ...saved, story: { ...story, editorial } as unknown as typeof saved.story });
  expect(screen.getByRole("heading", { level: 1 }).textContent).not.toBe("Story headline");
  cleanup();
  renderReport("institutional", { ...saved, story: { ...story, editorial } as unknown as typeof saved.story });
  // A specific headline (the place and a subject) wins; a generic one would not.
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Land surface temperature rises in Parc Sagunt II");
});

test("metric is the default for every language; an old automatic imperial setting is ignored", () => {
  localStorage.clear();
  localStorage.setItem("hup-units", "imperial");
  renderReport();
  expect(document.body.textContent).toContain("°C");
  expect(document.body.textContent).not.toContain("°F");
});

test.each(["en", "es"])("the journalistic report keeps uncited chain coverage and limits in %s", (language) => {
  localStorage.setItem("hup-lang", language);
  const input = results[0]!;
  const inherited = { en: "The input grid is 100 m.", es: "La rejilla de entrada es de 100 m." };
  const gap = {
    en: "Evaluated using 11 of 13 complete summers in the paired period. The regional control is missing complete summers for 2020, 2021.",
    es: "Evaluado con 11 de 13 veranos completos del periodo emparejado. Al control regional le faltan veranos completos de 2020, 2021.",
  };
  const unavailable = {
    en: "Insufficient paired years. 4 are available and the test requires 10.",
    es: "Años coincidentes insuficientes. Hay 4 y la prueba requiere 10.",
  };
  const chain: typeof input = {
    id: "r-chain",
    analysis: saved.id,
    step: "s-chain",
    type: "table",
    variable: "ndvi",
    data_class: "measured",
    sources: input.sources,
    method: { operation: "evaluate_chain", version: "1.4" },
    table: {
      columns: ["from", "to", "status", "reason", "reference", "reason_es"],
      rows: [["ndvi", "lst", "met", "", null, ""]],
    },
    limits: [inherited, gap],
  };
  const untestable: typeof input = {
    ...chain,
    id: "r-untestable",
    step: "s-untestable",
    table: { ...chain.table!, rows: [["ndvi", "lst", "not_testable", unavailable.en, null, unavailable.es]] },
    limits: [inherited, unavailable],
  };
  const plan: typeof saved.plan = {
    ...saved.plan,
    steps: [
      ...saved.plan.steps,
      ...[chain, untestable].map(({ step, method }) => ({
        id: step, type: "compute" as const, ...method, params: {}, depends_on: [input.step],
      })),
    ],
  };

  renderReport("journalistic", { ...saved, plan, findings: [] },
    [{ ...input, limits: [inherited] }, chain, untestable]);

  const limits = screen.getByRole("complementary", {
    name: language === "en" ? "What this does not say" : "Lo que esto no dice",
  });
  const lang = language === "en" ? "en" : "es";
  expect(limits).toHaveTextContent(gap[lang]);
  expect(limits).toHaveTextContent(unavailable[lang]);
  expect(limits).not.toHaveTextContent(inherited[lang]);
});

test("modelled figures carry an asterisk each, measured ones keep their tag, and one line explains the asterisk", () => {
  const value = results.find((item) => item.type === "value" && !item.absence)!;
  const modelled = [
    { ...value, id: "r-a", data_class: "reanalysis" },
    { ...value, id: "r-b", data_class: "reanalysis" },
    { ...value, id: "r-c", data_class: "measured" },
  ];
  const analysis = { ...saved, story: { journalistic: [] } as unknown as typeof saved.story };
  render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <ReportView analysis={analysis} results={modelled as typeof results} style="journalistic" />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );
  const marks = screen.getAllByRole("button", { name: "Modelled · reanalysis" });
  expect(marks).toHaveLength(2);
  fireEvent.focus(marks[0]);
  expect(screen.getByRole("tooltip")).toHaveTextContent("A reanalysis blends observations with a physical model");
  expect(screen.getByText("Measured", { selector: ".tag" })).toBeInTheDocument();
  expect(screen.getByText(/tap the asterisk to see what it means/)).toBeInTheDocument();
  // On paper the class is written after the value.
  expect(document.querySelectorAll(".data-mark-print")).toHaveLength(2);
});

test("the drop cap opens only a long first paragraph", async () => {
  const { openingClass } = await import("./Report");
  expect(openingClass(["Short."])).toBe("standout");
  expect(openingClass(["Short.", "Second."])).toBeUndefined();
  expect(openingClass(["x".repeat(300), "Second."])).toBe("lead");
  expect(openingClass(["x".repeat(240)])).toBeUndefined();
});

test("the asterisk is the footnote mark of the value: right after the unit, and the legend sits in the figure block", () => {
  const value = results.find((item) => item.type === "value" && !item.absence)!;
  const items = [
    { ...value, id: "r-a", data_class: "reanalysis", unit: "°C" },
    { ...value, id: "r-b", data_class: "illustrative", unit: "°C" },
  ];
  const analysis = { ...saved, story: { journalistic: [] } as unknown as typeof saved.story };
  render(
    <MemoryRouter>
      <I18nProvider><UnitsProvider>
        <ReportView analysis={analysis} results={items as typeof results} style="journalistic" />
      </UnitsProvider></I18nProvider>
    </MemoryRouter>,
  );
  expect(document.querySelectorAll(".report-stat .data-mark")).toHaveLength(2);
  for (const mark of document.querySelectorAll(".report-stat .data-mark")) {
    expect(mark.previousElementSibling).toHaveClass("unit");
    expect(mark.parentElement).toHaveClass("report-nowrap");
  }
  const legend = document.querySelector(".data-mark-legend")!;
  expect(legend.closest(".report-figures")).not.toBeNull();
  // One glyph per colour present, and only the classes present, named.
  expect([...legend.querySelectorAll(".data-mark-glyph")].map((glyph) => glyph.getAttribute("data-class"))).toEqual([
    "reanalysis",
    "illustrative",
  ]);
  expect(legend.textContent).toContain("modelled data or illustrative value");
  expect(legend.textContent).not.toContain("approximated");
  expect(legend.querySelector(".data-mark-hint")).not.toBeNull();
});
