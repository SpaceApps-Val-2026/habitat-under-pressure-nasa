# Footer logos

The footer of the web app shows here the logos of the event and of the agencies and missions whose data the
application uses. **The footer draws only the logos listed under `show` in `logos.json`.** Those under `held`
wait for permission: the footer writes their name as text instead. The origin, date, size and usage rule of
each file are in [SOURCES.md](SOURCES.md).

These logos belong to their owners and are not covered by the project's Apache 2.0 licence.

**Team decision, 15 November 2026: all are shown in the footer; any is withdrawn if its owner asks.** That is
why `held` is empty. Withdrawing one means moving its entry from `show` to `held`.

## `logos.json`

```json
{
  "show": [{ "key": "space-apps", "alt": "NASA Space Apps Challenge", "dark": "space-apps.dark.png" }],
  "held": [{ "key": "esa", "alt": "ESA", "dark": "esa.dark.svg", "light": "esa.light.png" }]
}
```

- `key`: which mark it replaces (table below). `alt`: the name of the owner or mission.
- `file` serves both themes; `dark` and `light` are the owner's versions for dark and light backgrounds. When
  only the version for the other background exists, it is drawn on a small plate of constant colour, without
  touching its colours. If a file does not load, the name stays as text.

## Keys

| Key | Where it appears | When |
| --- | --- | --- |
| `space-apps` | Event column, above “NASA Space Apps Challenge 2026 · València”, which stays as text | Always |
| `space-apps-valencia` | Replaces that line of text | If there is ever an official logo of the local event |
| `nasa`, `usgs`, `gpm`, `smap`, `esa`, `copernicus`, `copernicus-ems`, `eox`, `ec`, `eea`, `ine-es`, `chj`, `miteco`, `boe`, `gva`, `icv` | Row of logos under the title of each “Data from” group | When a source of the group names them in its `provider` or `name` (table `MARKS` in `Footer.tsx`) |
| `<source id>` | Replaces the name of that source in the list, with the `id` of `catalog/sources.yaml` | If it is listed |

Owner keys cannot match a catalogue `id`: that is why INE is `ine-es` (the source `ine` already exists).

## Size

Agencies and missions at 22 px high and at most 110 px wide; Space Apps, the event, at 40 px and 180 px;
`object-fit: contain`. SVG files have their `viewBox` fitted to the content and PNG files are trimmed and
112 px high (4×), or their original size when smaller. Colours, proportions and elements are never changed:
the brand rules forbid it.
