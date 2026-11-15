"""INE population of Spain by census section, spread inside each section by residential built-up surface at 100 m.

Figures. The section figures are those of the annual census (INE, Censo anual de población: residents on 1 January,
the operation that replaced the section figures of the Estadística del Padrón Continuo after 2022). In each
municipality they are scaled so that they add up to the official register figure of the same year (INE, Revisión del
Padrón Municipal): the municipal total is the official one, and each section keeps its census share.

Split inside a section (dasymetric). The people of a section are spread over the 100 m cells of GHS-BUILT-S R2023A by
their residential built-up surface (total minus non-residential, 2025 epoch). It is a modelled split, not a count. A
section with no residential surface on the grid, or outside the prepared tiles, is spread by area, and the result
says how many. Without any built-up tile everything is spread by area.

Boundaries. The sections are the INE 2026 boundaries prepared here; the census uses the boundaries of its own year. A
2026 section without a figure (split since) is pooled with the sections of its municipality and district that touch
it, and the pool is spread as one section: the people are kept, their place inside the pool is modelled.

`choose(geometry)` says whether a zone lies within sections with a figure (up to SLIVER of its area, for edges that
do not match); the callers use GHSL otherwise. The part of the zone outside every section (sea, port, beach), up to
NO_SECTION of it, counts as no one and not against SLIVER, unless a section near the zone is in a province with a land
border abroad (BORDER_PROVINCES), where that part may be another country.
`count(geometry, chosen)` gives the people inside any part of the zone.
"""
import re
from functools import lru_cache

import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import CRS
from shapely.geometry import box

from hup_data import cancellation, paths
from hup_data.prepare.common import manifest_lines, read_index
from hup_data.readers import vectors
from hup_data.readers.exposure import _project

SOURCE = "ine-population"
SECTIONS = re.compile(r"^ine/seccionado_(\d{4})__SECC")
BUILT = re.compile(r"^ghsl-built-s/.*GHS_BUILT_S_(NRES_)?E(\d{4})_GLOBE_R2023A_54009_100_V1_0_(R\d+_C\d+)\.tif$")
BUILT_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/"
MOLLWEIDE = CRS.from_user_input("ESRI:54009")
SLIVER = 0.005  # share of the zone that may fall outside the sections (edges drawn from other maps)
NO_SECTION = 0.05  # share of the zone outside every section (sea, port, beach) that may count as no one
# Provinces (INE codes) with a land border abroad: outside every section there may be people of another country, so
# near them the part without a section counts against SLIVER as before. Portugal: Pontevedra, Ourense, Zamora,
# Salamanca, Cáceres, Badajoz, Huelva. France: Girona, Lleida, Huesca, Navarra, Gipuzkoa. Andorra: Lleida.
# Gibraltar: Cádiz. Morocco: Ceuta, Melilla.
BORDER_PROVINCES = frozenset({"36", "32", "49", "37", "10", "06", "21", "17", "25", "22", "31", "20", "11", "51", "52"})
MIN_BUILT_M2 = 1.0  # less residential surface than this in a section is an edge sliver of a neighbouring cell


def _number(text: pd.Series) -> pd.Series:
    """INE CSV figures: dots for thousands, empty when there is no figure."""
    return pd.to_numeric(text.str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")


@lru_cache(maxsize=4)
def _tables(files: tuple[tuple[str, str], ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(sections, municipalities) from the INE CSV files: codes, year and people, totals of sex and nationality."""
    sections, municipal = [], []
    for path, _ in files:
        cancellation.checkpoint()
        table = pd.read_csv(paths.data_dir() / path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=False)
        if "Sexo" in table:
            table = table[table.Sexo == "Total"]
        if "Nacionalidad" in table:
            table = table[table.Nacionalidad == "Total"]
        if "Secciones" in table:
            rows = table[table.Secciones != ""]
            sections.append(pd.DataFrame({"cusec": rows.Secciones.str[:10], "cumun": rows.Secciones.str[:5],
                                          "year": rows.Periodo.astype(int), "people": _number(rows.Total)}))
        elif list(table.columns[:1]) == ["Municipios"]:
            municipal.append(pd.DataFrame({"cumun": table.Municipios.str[:5], "year": table.Periodo.astype(int),
                                           "people": _number(table.Total)}))
    empty = pd.DataFrame(columns=["cusec", "cumun", "year", "people"])
    return (pd.concat(sections, ignore_index=True) if sections else empty,
            pd.concat(municipal, ignore_index=True) if municipal else empty[["cumun", "year", "people"]])


def _lines() -> list[dict]:
    return manifest_lines(formats={"csv"}, sources={SOURCE})


@lru_cache(maxsize=4)
def _figures(files: tuple[tuple[str, str], ...]) -> tuple[int | None, dict[str, float], dict[str, float]]:
    """Latest year with both tables; section people scaled to the register figure of their municipality; those."""
    sections, municipal = _tables(files)
    years = set(sections.year) & set(municipal.dropna().year)
    if not years:
        return None, {}, {}
    year = max(years)
    sections = sections[(sections.year == year) & sections.people.notna()]
    official = municipal[(municipal.year == year) & municipal.people.notna()].set_index("cumun").people
    census = sections.groupby("cumun").people.sum()
    scale = (official / census).dropna()
    sections = sections[sections.cumun.isin(scale.index) & (census.reindex(sections.cumun).to_numpy() > 0)]
    people = sections.people.to_numpy() * scale.reindex(sections.cumun).to_numpy()
    return year, dict(zip(sections.cusec, people, strict=True)), official[official.index.isin(scale.index)].to_dict()


def figures() -> tuple[int | None, dict[str, float], dict[str, float]]:
    """(year, people by section code, official people by municipality code) from the INE tables on this computer."""
    return _figures(tuple((line["path"], line["sha256"]) for line in _lines()))


def _layer() -> str | None:
    found = sorted((match.group(1), key) for key in vectors.layers("ine") if (match := SECTIONS.match(key)))
    return found[-1][1] if found else None


def _sections(key: str, bounds, people: dict[str, float], municipal: dict[str, float]):
    """Sections touching `bounds`, and those that touch a section without a figure in a municipality with figures."""
    found = vectors.read_layer(key, list(bounds))["data"]
    if found is None:
        return None
    if not found.empty:  # their neighbours too: a section without a figure may touch one of them from outside
        found = vectors.read_layer(key, list(found.total_bounds))["data"]
    seen = set(found.CUSEC)
    while True:
        lacking = found[~found.CUSEC.isin(set(people)) & found.CUMUN.isin(set(municipal))]
        if lacking.empty:
            return found
        more = vectors.read_layer(key, list(lacking.total_bounds))["data"]
        more = more[~more.CUSEC.isin(seen)]
        if more.empty:
            return found
        seen |= set(more.CUSEC)
        found = pd.concat([found, more], ignore_index=True)


def _units(sections, people: dict[str, float], municipal: dict[str, float]) -> list[dict]:
    """Sections with a figure, and pools of a section without one plus its neighbours (same municipality, district)."""
    sections = sections.reset_index(drop=True)
    parent = list(range(len(sections)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, row in sections.iterrows():
        if row.CUSEC in people or row.CUMUN not in municipal:
            continue
        near = sections[(sections.CUMUN == row.CUMUN) & (sections.CDIS == row.CDIS) & (sections.index != i)]
        for j in near.index[near.geometry.intersects(row.geometry)]:
            parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(sections)):
        groups.setdefault(root(i), []).append(i)
    units = []
    for members in groups.values():
        rows = sections.iloc[members]
        known = [people[code] for code in rows.CUSEC if code in people]
        geometry = rows.geometry.iloc[0] if len(rows) == 1 else shapely.make_valid(shapely.union_all(rows.geometry))
        units.append({"geometry": geometry, "people": float(sum(known)) if known else None,
                      "sections": sorted(rows.CUSEC), "pooled": len(rows) > 1})
    return units


def _built(year: int) -> list[tuple[dict, dict | None]]:
    """(total, non-residential) index entries of the 100 m built-up tiles of the epoch closest to `year`."""
    tiles: dict[int, dict[str, dict]] = {}
    for key, entry in read_index("grids").items():
        match = BUILT.match(key)
        if match and (paths.data_dir() / entry["path"]).is_file():
            tiles.setdefault(int(match.group(2)), {})[("nres:" if match.group(1) else "") + match.group(3)] = entry
    if not tiles:
        return []
    epoch = min(tiles, key=lambda e: (abs(e - year), -e))
    found = tiles[epoch]
    return [(entry, found.get(f"nres:{tile}")) for tile, entry in sorted(found.items()) if not tile.startswith("nres:")]


class _Weights:
    """Residential built-up m² inside a geometry in Mollweide, summed over the open tiles."""

    def __init__(self, entries: list[tuple[dict, dict | None]]):
        self.tiles = [(rasterio.open(paths.data_dir() / total["path"]),
                       rasterio.open(paths.data_dir() / nres["path"]) if nres else None) for total, nres in entries]
        self.footprint = shapely.union_all([box(*total.bounds) for total, _ in self.tiles]) if self.tiles else None

    def close(self):
        for total, nres in self.tiles:
            total.close()
            if nres is not None:
                nres.close()

    def __call__(self, area) -> float | None:
        """None when the tiles do not cover `area`."""
        from hup_data.readers.population import cells

        if self.footprint is None or area.difference(self.footprint).area > 1e-6 * max(area.area, 1.0):
            return None
        weight = 0.0
        for total, nres in self.tiles:
            part = area.intersection(box(*total.bounds))
            found = cells(total, part) if part.area > 0 else None
            if found is None or "outside" in found:
                continue
            built = total.read(1, window=found["window"]).astype("float64")
            valid = built != total.nodata
            if nres is not None:
                other = nres.read(1, window=found["window"]).astype("float64")
                valid &= other != nres.nodata
                built = np.clip(built - other, 0, None)
            weight += float((np.where(valid, built, 0.0) * found["share"]).sum())
        return weight


def choose(geometry) -> dict:
    """Whether the INE answers for a zone (EPSG:4326). Keys: ok, why (en, es) when not, year, units, sources, outside.

    `ok` is True only when the tables, the sections and the figures cover the whole zone but for SLIVER of it.
    """
    year, people, municipal = figures()
    key = _layer()
    lines = _lines()
    chosen = {"ok": False, "why": None, "year": year, "units": [], "sources": _sources(lines, key, year, []),
              "outside": None}
    if year is None or key is None:
        return chosen
    everywhere = _sections(key, geometry.bounds, people, municipal)
    sections = None if everywhere is None else everywhere[everywhere.CUMUN.isin(set(municipal))]
    if sections is None or sections.empty:
        return chosen
    units = [unit for unit in _units(sections, people, municipal) if unit["geometry"].intersects(geometry)]
    zone = _project(geometry, MOLLWEIDE)
    held = shapely.union_all([unit["geometry"] for unit in units if unit["people"] is not None])
    outside = zone.difference(_project(held, MOLLWEIDE)).area / zone.area if zone.area > 0 else 1.0
    # The register puts every resident in a section: the part of the zone outside all of them holds no one registered.
    unsectioned = 0.0
    if zone.area > 0 and not set(everywhere.CUSEC.str[:2]) & BORDER_PROVINCES:
        every = _project(shapely.union_all(everywhere.geometry.values), MOLLWEIDE)
        unsectioned = zone.difference(every).area / zone.area
        unsectioned = unsectioned if unsectioned <= NO_SECTION else 0.0
    outside = max(outside - unsectioned, 0.0)
    chosen.update(units=units, outside=outside, unsectioned=unsectioned)
    if outside > SLIVER:
        chosen["why"] = {
            "en": f"{100 * outside:.1f}% of the zone lies outside the census sections with an INE figure on this "
                  "computer; GHSL is used instead.",
            "es": f"El {100 * outside:.1f}% de la zona queda fuera de las secciones censales con cifra del INE en "
                  "este ordenador; se usa GHSL."}
        return chosen
    chosen["ok"] = True
    chosen["built"] = _built(year)
    chosen["sources"] = _sources(lines, key, year, chosen["built"])
    return chosen


def _sources(lines: list[dict], key: str | None, year: int | None, built: list) -> list[dict]:
    from hup_data.readers.population import _url

    tables = sorted(lines, key=lambda line: line["path"])
    ref = {"source": SOURCE, "product": "INE: Censo anual de población por sección censal y Revisión del Padrón "
                                        "Municipal", "url": tables[0]["ingest"]["url"] if tables else "https://www.ine.es/",
           "items": [line["product"] for line in tables if line.get("product")], "n_items": len(tables)}
    if year is not None:
        ref["version"] = str(year)
    if tables:
        ref["license"] = tables[0]["license"]
    refs = [ref]
    if key is not None:
        entry = vectors.layers("ine")[key]
        refs.append({"source": "ine", "product": key.split("/", 1)[1], "url": _url(entry, "https://www.ine.es/"),
                     **{name: entry[name] for name in ("license",) if entry.get(name)}})
    if built:
        items = sorted(entry["path"].rsplit("/", 1)[-1].removesuffix(".tif")
                       for pair in built for entry in pair if entry)
        refs.append({"source": "ghsl-built-s", "product": "GHS-BUILT-S 100 m, residential (total minus NRES)",
                     "version": "R2023A V1-0", "url": BUILT_URL, "license": "CC BY 4.0", "items": items,
                     "n_items": len(items)})
    return refs


def count(geometry, chosen: dict) -> dict:
    """People inside `geometry` (EPSG:4326, within the chosen zone). Keys: people, sections, by_area, pooled."""
    counted = {"people": 0.0, "sections": 0, "by_area": 0, "pooled": 0}
    if geometry.is_empty:
        return counted
    weights = _Weights(chosen.get("built", []))
    try:
        shapely.prepare(geometry)
        for unit in chosen["units"]:
            if unit["people"] is None or not geometry.intersects(unit["geometry"]):
                continue
            whole = _project(unit["geometry"], MOLLWEIDE)
            inside = _project(shapely.make_valid(unit["geometry"].intersection(geometry)), MOLLWEIDE)
            if inside.area <= 0:
                continue
            counted["sections"] += 1
            counted["pooled"] += unit["pooled"]
            if inside.area >= whole.area * (1 - 1e-9):
                share = 1.0
            else:
                total = weights(whole)
                if total is not None and total >= MIN_BUILT_M2:
                    share = weights(inside) / total
                else:
                    share = inside.area / whole.area
                    counted["by_area"] += 1
            counted["people"] += unit["people"] * min(share, 1.0)
    finally:
        weights.close()
    return counted


def limits(chosen: dict, counted: dict) -> list[dict]:
    year = chosen["year"]
    found = [{
        "en": f"INE figures for 1 January {year}: the total of each municipality is the official register figure "
              "(Revisión del Padrón Municipal) and each census section keeps its share in the annual census. Inside a "
              "section, people are spread over 100 m cells by residential built-up surface (GHSL): a modelled split, "
              "valid for a neighbourhood, not for a building. Support: census section and 100 m cell.",
        "es": f"Cifras del INE a 1 de enero de {year}: el total de cada municipio es la cifra oficial del padrón "
              "(Revisión del Padrón Municipal) y cada sección censal conserva su parte en el censo anual. Dentro de "
              "la sección, la población se reparte en celdas de 100 m según la superficie construida residencial "
              "(GHSL): un reparto modelado, válido para un barrio, no para un edificio. Soporte: sección censal y "
              "celda de 100 m."}]
    if not chosen.get("built"):
        found.append({"en": "The built-up grid is not prepared on this computer: people are spread by area inside "
                            "each section.",
                      "es": "La rejilla de superficie construida no está preparada en este ordenador: la población se "
                            "reparte por área dentro de cada sección."})
    elif counted["by_area"]:
        found.append({"en": f"{counted['by_area']} of {counted['sections']} sections had no residential built-up "
                            "surface on the grid (or fell outside its tiles) and were spread by area.",
                      "es": f"{counted['by_area']} de {counted['sections']} secciones no tenían superficie construida "
                            "residencial en la rejilla (o quedaban fuera de sus teselas) y se repartieron por área."})
    if counted["pooled"]:
        found.append({"en": f"Sections of 2026 with no census figure for {year} (created since) are pooled with "
                            f"their neighbours and each pool is spread as one section (pools: {counted['pooled']}).",
                      "es": f"Las secciones de 2026 sin cifra del censo de {year} (creadas después) se agrupan con sus "
                            f"vecinas y cada grupo se reparte como una sola sección (grupos: {counted['pooled']})."})
    if chosen["outside"] >= 1e-4:
        found.append({"en": f"{100 * chosen['outside']:.2f}% of the zone lies outside the census sections and counts "
                            "as no one.",
                      "es": f"El {100 * chosen['outside']:.2f}% de la zona queda fuera de las secciones censales y "
                            "cuenta como nadie."})
    if chosen.get("unsectioned", 0.0) >= 1e-4:
        share = 100 * chosen["unsectioned"]
        found.append({"en": f"{share:.2f}% of the zone belongs to no census section (coast or port) and counts as "
                            "no population: the register places every resident in a section.",
                      "es": f"El {f'{share:.2f}'.replace('.', ',')} % de la zona no pertenece a ninguna sección censal "
                            "(costa o puerto) y se cuenta sin población: el padrón asigna a cada residente una "
                            "sección."})
    return found
