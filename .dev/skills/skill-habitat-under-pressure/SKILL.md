---
name: skill-habitat-under-pressure
description: Operating context of Habitat Under Pressure, a tool that answers questions about a place with Earth observation data. Use it for any task in this repository; it does not replace AGENTS.md.
---

# Project context

Read [AGENTS.md](../../../AGENTS.md) first. The product is an analysis tool: a person asks about a place, an
assistant proposes a plan over a closed catalogue, an executor computes it with open data, and the result is
saved as a repeatable analysis with its report.

This is the one-page version of the skill the team used during the event. The full skill, in Spanish, with its
roadmap, data model, testing policy and design system, lives in the team's working repository.

| What | Where |
| --- | --- |
| What the product does and how to run it | [README.md](../../../README.md) |
| How the pieces fit | [docs/architecture.md](../../../docs/architecture.md) |
| The boundary between data, engine, assistant and web | [docs/contratos/](../../../docs/contratos/README.md): schemas, examples and validator |
| What the engine may use | [catalog/](../../../catalog/README.md) |
| Sources, licences and citations | [docs/data-sources.md](../../../docs/data-sources.md) |

## The rules that cut across everything

1. **The LLM writes no figures.** Every number comes from a result and enters the text through a placeholder
   that code fills in.
2. **Data or absence, never both.** The absence carries its reason; it is never a zero or an invented value.
3. **Every figure declares its class** (`measured`, `reanalysis`, `extrapolated`, `approximated`,
   `illustrative`), its unit, its period and its source with a URL.
4. **Every sentence states how far its evidence goes,** from `E0` to `E5`. No level allows “causes”, “leads
   to” or “due to”.
5. **The tool is universal.** The demo cases enter through the application's own cache, with no code or data
   prepared for a specific place.
6. **What a person must provide stays empty until they provide it:** quotes of norms, references of chain
   links, thresholds and coefficients.

## The path of a question

question → **assistant** (rules, and one LLM call when a provider is configured) → **plan** validated against
the catalogue and the coverage of each source → **executor** (graph of steps, events, cache) → **operations**
(pure functions over the data layer) → **results** → **writer** (blocks and placeholders) → **reviewer**
(rules and a small model) → **analysis** saved in the library, with a report in three formats.

## Working here

- Change a contract before the code on either side of it, and keep its examples valid.
- A new source needs its entry in `catalog/sources.yaml` with licence and citation in English and Spanish,
  and a reader in `packages/hup_data`.
- A new operation declares its parameters, its version and the highest evidence level it can support.
- Visible strings go to `locales/`, in both languages.
- Run `python tools/gate.py` before reporting a change as done, and say what was not checked.
