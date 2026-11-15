# Exchange contracts, version 1.0

The JSON Schemas in this folder are the boundary between the parts of the system. Each part is written
against these files and their [examples](examples/), not against the code of another part. That is what let
three work fronts advance at once during the event.

The folder keeps its original name, `contratos` (Spanish for “contracts”), because the build and the code
refer to it by path.

## The six contracts

| Contract | File | Written by | Read by |
| --- | --- | --- | --- |
| Catalogue | [catalog.schema.json](catalog.schema.json) | Data and analysis | Assistant, engine and web |
| Plan | [plan.schema.json](plan.schema.json) | Assistant or form | Engine and web |
| Event | [event.schema.json](event.schema.json) | Engine | Web |
| Result | [result.schema.json](result.schema.json) | Operations | Engine, report and web |
| Analysis | [analysis.schema.json](analysis.schema.json) | Engine | Library, report and web |
| Cache manifest | [manifest.schema.json](manifest.schema.json) | Ingest and pre-download | Engine |

The shared pieces (data class, text by language, source, absence, evidence level, chain link, support and
report format) are in [common.schema.json](common.schema.json). The **report** has no contract of its own: it
is generated only from the analysis and its results, so it cannot say anything that is not in them.

## The ten rules the schemas and the validator enforce

1. **A result carries data or an absence, never both.** The absence has a reason: `no_coverage`,
   `insufficient_qa`, `source_unreachable`, `needs_package` or `not_computed`.
2. **Every figure declares its class** (`data_class`): `measured`, `reanalysis`, `extrapolated`,
   `approximated` or `illustrative`. The interface translates them into its labels.
3. **Every result cites at least one source with its URL.** That is what opens the source in one click.
4. **The LLM writes no figures.** The text of a finding has placeholders such as `{r-lst-change.value}` that
   code fills in. The validator rejects any number written by hand, except years.
5. **The plan answers before running,** at three levels: `complete`, `with_limits` or `not_possible`, with the
   alternative question that does have an answer.
6. **Ingest is a visible step.** Each ingest step says whether the data will come from the cache, the network
   or a package, and how long it is expected to take; the events report it while it happens.
7. **Texts go by language,** with English mandatory.
8. **Every sentence states how far its evidence goes.** The operation declares its level (`evidence_level`,
   from `E0` to `E5`), the result cannot exceed it and the finding cannot exceed its results. The reviewer can
   only lower it. No level allows causal language.
9. **A report block only enters if its data exist.** Each block of the catalogue declares what it needs and
   in which formats it is used; a result with an absence feeds neither a figure nor a chart.
10. **What a person must provide stays empty until they provide it.** The literal quote of a norm, the
    reference of a chain link and the range of a consumption are `null` until someone has filled them in and
    checked them; the schema does not accept filler text in place of the datum.

## How to use them

```bash
python docs/contratos/validate.py
```

It validates the examples against the schemas and checks the cross-references: that each step uses a
catalogue operation with its type, version and valid parameters; that each finding, alert, event and block
points at results that exist; that evidence levels do not rise from one step to the next; that each chain,
norm and block cites variables, sources and operations of the catalogue; and that the events are in order. It
needs the `jsonschema` package. It is the first step of the quality gate.

The [examples](examples/) are one coherent analysis (“what has changed around the gigafactory in Sagunt?”)
with figures from the team's real-data probe. They include the three ingest cases: data downloaded on the
spot, data already in the cache and a package source that is missing. `generate_examples.py` writes them; they
are not edited by hand.

| Example | What it is for |
| --- | --- |
| `events.jsonl` | The web app replays a complete run without the engine |
| `analysis.json` and `results.json` | The data panel and the report are built without waiting for the operations |
| `plan.json` and `catalog.json` | The engine and the operations are tested without the assistant |
| `manifest.jsonl` | Pre-download and ingest write the same format |
