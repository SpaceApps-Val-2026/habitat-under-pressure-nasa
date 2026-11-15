"""CHJ piezometric network (F71): validated static groundwater head at monitoring wells, in metres above sea level.

The package is the ZIP of the CHJ (predownload `chj-f71`): five XLSX books, read here with the standard library. The
inventory (F71_M10_Todas) gives each point its code, name, municipality, groundwater body and UTM ETRS89 zone 30
position; the field books (F71_D7_Medida en campo_*) give, per point and date, the column
"Niv. Valid. Estática (m.s.n.m.)": the validated static water level as a height above sea level. The dynamic,
affected and other levels, and the depth columns in metres, are other fields and are not read. The parsed tables are
kept in <data>/ready/chj-f71/, named by the sha256 of the ZIP, so a new download is parsed again.

A well is a point, not the groundwater body and not the zone. The series is that of one well: the one inside the zone
with the most dates in the period asked, or, when no well is inside, the nearest within NEAREST_KM of the zone
outline. Its code goes in `sources[].items`, and `limits` says which well it is, its groundwater body and how far it
is. Dates are those of the measurements, with their gaps; the yearly value, if asked for, is made by read_series.
"""
import io
import re
import zipfile
from functools import lru_cache
from xml.etree import ElementTree

import pandas as pd
import shapely
from shapely.geometry import box, shape

from hup_data import cancellation, paths, store
from hup_data.prepare.common import manifest_lines
from hup_data.readers import absent, register
from hup_data.readers.built_up import _project

SOURCE = "chj-f71"
PRODUCT = "F71 Puntos de control piezométrico"
URL = "https://aps.chj.es/down/CSV/F71_Puntos_de_control_piezometrico.zip"
LICENSE = ("Reuse under Ley 37/2007 and Real Decreto 1495/2011, art. 7 (CHJ legal notice); cite 'Origen de los "
           "datos: Confederación Hidrográfica del Júcar' and the update date")
CRS = "EPSG:25830"  # UTM ETRS89 zone 30, the inventory's own coordinates
NEAREST_KM = 5.0  # a well farther than this from the zone outline does not speak for it
INVENTORY = "F71_M10_Todas.xlsx"
FIELD_BOOKS = re.compile(r"^F71_D7_Medida en campo_\d+\.xlsx$")
LEVEL = "Niv. Valid. Estática (m.s.n.m.)"
COLUMNS = {"Cód. Punto": "code", "Nombre": "name", "Municipio": "municipality", "Cód. Masa PHJ22 Hidrog.": "body",
           "Masa Subterránea PHJ22 Hidrog.": "body_name", "UTM ETRS89 X": "x", "UTM ETRS89 Y": "y"}
EXCEL_EPOCH = pd.Timestamp("1899-12-30")  # serial day 0 of the 1900 date system
LAYOUT = "1"  # changes when the parsed tables change
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _source(line: dict | None = None, items: list[str] | None = None) -> dict:
    ref = {"source": SOURCE, "product": PRODUCT, "url": URL, "license": LICENSE}
    if line:
        ref.update({key: line[key] for key in ("version", "license") if line.get(key)})
        ref["url"] = line.get("ingest", {}).get("url") or URL
    return {**ref, "items": items} if items else ref


def _cell(cell, shared: list[str]) -> str | None:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(text.text or "" for text in cell.iter(_NS + "t"))
    value = cell.find(_NS + "v")
    if value is None or value.text is None:
        return None
    return shared[int(value.text)] if kind == "s" else value.text


def sheet(book: bytes) -> pd.DataFrame:
    """The first sheet of an XLSX book as text, with its first row as header. Empty cells are None."""
    with zipfile.ZipFile(io.BytesIO(book)) as parts:
        names = parts.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ElementTree.fromstring(parts.read("xl/sharedStrings.xml")).iter(_NS + "si"):
                shared.append("".join(text.text or "" for text in item.iter(_NS + "t")))
        rows = []
        with parts.open(sorted(n for n in names if n.startswith("xl/worksheets/sheet"))[0]) as stream:
            for _, element in ElementTree.iterparse(stream):
                if element.tag == _NS + "row":
                    rows.append({re.match(r"[A-Z]+", cell.get("r")).group(): _cell(cell, shared)
                                 for cell in element.iter(_NS + "c")})
                    element.clear()
    if not rows:
        return pd.DataFrame()
    header = rows[0]
    return pd.DataFrame([[row.get(column) for column in header] for row in rows[1:]],
                        columns=list(header.values()))


def _parse(package) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(wells, levels) from the ZIP: wells with code, names, body and x, y; levels with code, t (ISO date), v."""
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        inventory = sheet(archive.read(INVENTORY)).rename(columns=COLUMNS)
        books = [sheet(archive.read(name)) for name in sorted(names) if FIELD_BOOKS.match(name)]
    wells = inventory[list(COLUMNS.values())].copy()
    wells["x"], wells["y"] = pd.to_numeric(wells["x"], errors="coerce"), pd.to_numeric(wells["y"], errors="coerce")
    wells = wells.dropna(subset=["code", "x", "y"]).drop_duplicates("code").reset_index(drop=True)
    parts = []
    for book in books:
        cancellation.checkpoint()
        if LEVEL not in book or book.empty:
            continue
        day = EXCEL_EPOCH + pd.to_timedelta(pd.to_numeric(book["Fecha"], errors="coerce").floordiv(1), unit="D")
        parts.append(pd.DataFrame({"code": book["Cód. Punto"], "t": day.dt.strftime("%Y-%m-%d"),
                                   "v": pd.to_numeric(book[LEVEL], errors="coerce")}))
    levels = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["code", "t", "v"])
    levels = levels.dropna()
    # Two static readings of one well on one day (none in the 2026-11-13 download) become their mean.
    levels = levels.groupby(["code", "t"], as_index=False, sort=True)["v"].mean()
    return wells, levels


def _package() -> dict | None:
    """The manifest line of the newest F71 ZIP on this computer, or None."""
    lines = manifest_lines(formats={"zip"}, sources={SOURCE})
    return max(lines, key=lambda line: line["ingest"]["date"]) if lines else None


@lru_cache(maxsize=2)
def tables(path: str, sha256: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(wells, levels) of one ZIP, parsed once and kept in the ready tier under its sha256."""
    folder = paths.ready() / SOURCE
    files = {name: folder / f"{sha256[:16]}-{LAYOUT}-{name}.parquet" for name in ("wells", "levels")}
    if all(file.is_file() for file in files.values()):
        return pd.read_parquet(files["wells"]), pd.read_parquet(files["levels"])
    wells, levels = _parse(paths.data_dir() / path)
    for name, table in (("wells", wells), ("levels", levels)):
        buffer = io.BytesIO()
        table.to_parquet(buffer, index=False)
        store.write_atomic(files[name], buffer.getvalue())
    return wells, levels


def _in_period(levels: pd.DataFrame, period: dict) -> pd.DataFrame:
    start, end = period["start"], period["end"]
    kept = levels[(levels.t.str[:len(start)] >= start) & (levels.t.str[:len(end)] <= end)]
    if period.get("months"):  # a season may cross the new year: the month number is all that counts
        kept = kept[kept.t.str[5:7].astype(int).isin(period["months"])]
    return kept


def _well_text(well, km: float) -> tuple[str, str]:
    where = "inside the zone" if km == 0 else f"{km:.1f} km from the zone outline"
    donde = "dentro de la zona" if km == 0 else f"a {km:.1f} km del contorno de la zona".replace(".", ",", 1)
    def text(value) -> str:  # an empty cell of the inventory comes back as None or NaN: it is left out (U16)
        return value.strip() if isinstance(value, str) else ""

    named = text(well.body)
    body = [f"groundwater body {named} {text(well.body_name)}".rstrip()] if named else []
    masa = [f"masa de agua subterránea {named} {text(well.body_name)}".rstrip()] if named else []
    known = [part for part in (text(well["name"]), text(well.municipality)) if part]
    en, es = ", ".join(known + body), ", ".join(known + masa)
    return (f"CHJ monitoring well {well.code}{f' ({en})' if en else ''}, {where}",
            f"piezómetro de la CHJ {well.code}{f' ({es})' if es else ''}, {donde}")


@register("1", "groundwater_level")
def read(variable: str, zone: dict, period: dict) -> dict:
    line = _package()
    if line is None:
        return absent("needs_package", "The CHJ piezometric network (F71) has not been downloaded on this computer.",
                      "La red piezométrica de la CHJ (F71) no se ha descargado en este ordenador.", _source(),
                      origin="package")
    wells, levels = tables(line["path"], line["sha256"])
    geometry = shapely.make_valid(shape(zone["geometry"])) if zone.get("geometry") else box(*zone["bbox"])
    area = _project(geometry, CRS)
    points = shapely.points(wells["x"].to_numpy(), wells["y"].to_numpy())
    wells = wells.assign(km=shapely.distance(area, points) / 1000)
    inside = wells[wells.km == 0]
    near = inside if not inside.empty else wells[wells.km <= NEAREST_KM].nsmallest(1, "km")
    if near.empty:
        return absent("no_coverage", f"No well of the CHJ piezometric network lies within {NEAREST_KM:g} km of "
                      "the zone.",
                      f"Ningún piezómetro de la red de la CHJ está a menos de {NEAREST_KM:g} km de la zona.",
                      _source(line), origin="package")
    kept = _in_period(levels[levels.code.isin(near.code)], period)
    counts = kept.groupby("code").size()
    if counts.empty:
        codes = ", ".join(near.code)
        return absent("no_coverage", f"The CHJ wells that speak for this zone ({codes}) have no validated static "
                      "level in this period.", f"Los piezómetros de la CHJ que corresponden a esta zona ({codes}) no "
                      "tienen nivel estático validado en este periodo.", _source(line, list(near.code)),
                      origin="package")
    ranked = near[near.code.isin(counts.index)].assign(n=lambda frame: frame.code.map(counts))
    well = ranked.sort_values(["n", "km", "code"], ascending=[False, True, True]).iloc[0]
    data = kept[kept.code == well.code][["t", "v"]].reset_index(drop=True)
    en, es = _well_text(well, float(well.km))
    limits = [{"en": f"Point measurement at {en}: not the level of the whole groundwater body, nor of the zone.",
               "es": f"Medida puntual en el {es}: no es el nivel de toda la masa de agua ni el de la zona."}]
    others = sorted(set(near.code) - {well.code})
    if others:
        limits.append({"en": f"Other wells of the network in the zone, not read: {', '.join(others)}. The one read "
                             "has the most dates in the period.",
                       "es": f"Otros piezómetros de la red en la zona, no leídos: {', '.join(others)}. El leído es "
                             "el que tiene más fechas en el periodo."})
    first_month = (period["start"] + "-01")[:7]  # a bare year runs from January to December
    months = pd.period_range(first_month, (period["end"] + "-12")[:7], freq="M")
    if period.get("months"):
        months = months[months.month.isin(period["months"])]
    measured = data.t.str[:7].nunique()  # coverage: months of the period with at least one measurement
    first = int(levels[levels.code == well.code].t.min()[:4])
    return {"data": data, "absence": None, "origin": "package", "sources": [_source(line, [str(well.code)])],
            "support": {"since": first},
            "coverage": {"valid_pct": round(100 * measured / max(len(months), 1), 1), "n_dates": int(len(data))},
            "unit": "m a.s.l.", "limits": limits, "input_fingerprint": None}
