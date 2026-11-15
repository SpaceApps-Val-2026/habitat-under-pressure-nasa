"""GHCN-Daily (NOAA NCEI): daily rain of one rain gauge, the station nearest to the zone centre.

Stations come from the predownloaded inventory (packages/ghcn-daily/ghcnd-inventory.txt, predownload
`ghcnd-inventory`): every station with PRCP, its position and its first and last year. The series is asked for once
per station, whole, to the NCEI data service (no account), and kept in <data>/cache/ghcn-daily/<station>.csv with its
manifest line; later reads take any period from that file. When a period ends after the last day saved, the series is
asked for again (at most once every REFRESH_HOURS) and kept as <station>_<last day>.csv, a new file with its own
line, so that the lines already in the manifest keep matching their files; offline, `limits` says where it ends.

Units: the service gives PRCP in tenths of mm unless asked for `units=metric`, which gives mm (checked on 2026-11-15:
"170" and "17.0" for Valencia airport on 28-10-2024). This reader asks for mm. A value with a quality flag (second
attribute) failed a GHCN check and is left out. The date is the GHCN day of the value, which depends on the source
of each station. Measured on 2026-11-15 against the IMERG cell of each gauge (daily Pearson, 1998-2024): Valencia
airport, all GSOD ("S", totals reported during the day), agrees best with the UTC day before (0.64 against 0.43 on
the same day); Valencia city, ECA&D ("E", the AEMET day from 07 to 07 UTC), with the same day (0.67 against 0.38 and
0.17). The source flags of the series read go in `limits`.

A gauge is a point. Candidates are the stations with rain in the period within MAX_KM of the zone centre and within
SIMILAR_KM of the nearest; the one read has the most valid years in the period, then the nearest, then the lowest
code (criterion fixed before coding it: .dev/notes/2026-11-15-plan-contraste-pluviometro.md). Its code goes in
`sources[].items`, and `sources[].product` and `limits` say which station it is, how far it is and which others were
compared. Wrong positions of the GHCN list are corrected from COORDINATE_FIXES, each with its source.
"""
import io
import os
import time
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from hup_data import manifest, paths, settings, store
from hup_data.readers import absent, register
from hup_data.readers.imerg_daily import calendar_days

SOURCE = "ghcn-daily"
DAILY, YEARLY_MAX = "precipitation_daily_gauge", "pr_max_1day_gauge"
PRODUCT, VERSION = "GHCN-Daily PRCP", "3"
URL = "https://doi.org/10.7289/V5D21VHZ"
SERVICE = "https://www.ncei.noaa.gov/access/services/data/v1"
LICENSE = ("No licence named by NOAA NCEI; data of non-U.S. stations (Spain included) only for research, education "
           "and other non-commercial use (WMO Resolution 40). Cite Menne et al. (2012), "
           "https://doi.org/10.1175/JTECH-D-11-00103.1, and GHCN-Daily v3, https://doi.org/10.7289/V5D21VHZ")
INVENTORY = "packages/ghcn-daily/ghcnd-inventory.txt"
STATIONS = "packages/ghcn-daily/ghcnd-stations.txt"
# A gauge farther than this from the zone centre does not speak for it. 20 km is about two IMERG cells: beyond it a
# gauge is outside the neighbourhood of a comarca-sized zone. It is already generous for one storm (29-10-2024:
# 771.8 mm in Turís, AEMET, against 1.0 mm at Valencia airport, about 25 km away); it is a bound, not a guarantee.
MAX_KM = 20.0
SIMILAR_KM = 5.0  # half an IMERG cell: within this margin of the nearest, distance gives no station a better claim
# Positions of the GHCN list known to be wrong, with the official source of the right one.
COORDINATE_FIXES = {
    "SP000008416": {"lat": 39.4806, "lon": -0.3664,
                    "source": "ECA&D station list ECA_blend_station_rr.txt, STAID 237 VALENCIA, +39:28:50 -000:21:59 "
                              "(read 2026-11-15)",
                    "reason": "GHCN lists longitude +0.3664 (east), in the sea 64 km off Valencia",
                    "motivo": "GHCN da la longitud +0,3664 (Este), en el mar a 64 km de Valencia"},
}
MIN_YEAR_SHARE = 0.95  # a yearly maximum needs this share of the days of that year (the rule of imerg_daily)
TIMEOUT = (10, 120)
ATTEMPTS = 3
REFRESH_HOURS = 24  # a series that still ends before the period is not asked for again sooner than this
HEADERS = {"User-Agent": "python-requests"}  # generic: nothing about the project or the person
SOURCE_FLAGS = {  # GHCN-Daily readme, table of source flags: (en, es)
    "S": ("GSOD, summed from synoptic reports: NOAA says to use these daily totals with caution",
          "GSOD, sumado de partes sinópticos: NOAA pide usar estos totales diarios con cautela"),
    "E": ("ECA&D (national network; for Spain, AEMET)", "ECA&D (red nacional; en España, AEMET)"),
    "I": ("international collection (non-U.S.)", "colección internacional (fuera de EE. UU.)"),
    "2": ("GHCN-Daily near real time", "GHCN-Daily casi en tiempo real"),
    "K": ("Global Summary of the Day (archive)", "Global Summary of the Day (archivo)")}


class Unreachable(Exception):
    """The NCEI service did not answer with data."""


def _source(station: dict | None = None, n_items: int | None = None) -> dict:
    ref = {"source": SOURCE, "product": PRODUCT, "version": VERSION, "url": URL, "license": LICENSE}
    if station:
        ref["product"] = (f"{PRODUCT}, rain gauge {station['id']} {station['name']}".strip()
                          + f", point measurement {station['km']:.1f} km from the zone centre")
        ref["items"] = [station["id"]]
    return ref if n_items is None else {**ref, "n_items": n_items}


@lru_cache(maxsize=2)
def _inventory(path: str, stamp: int) -> pd.DataFrame:
    """Stations with PRCP: id, lat, lon, first, last year, and the name when the station list is there."""
    rows = []
    with open(path, encoding="utf-8") as lines:
        for line in lines:
            if line[31:35] == "PRCP":
                rows.append((line[0:11], float(line[12:20]), float(line[21:30]), int(line[36:40]), int(line[41:45])))
    table = pd.DataFrame(rows, columns=["id", "lat", "lon", "first", "last"])
    table["fix"] = table["fix_es"] = ""
    for code, fix in COORDINATE_FIXES.items():
        listed = table.id == code
        table.loc[listed, ["lat", "lon"]] = [fix["lat"], fix["lon"]]
        table.loc[listed, "fix"] = f"{fix['reason']}; {fix['source']}"
        table.loc[listed, "fix_es"] = f"{fix['motivo']}; {fix['source']}"
    names = paths.data_dir() / STATIONS
    if names.is_file():
        found = {}
        with names.open(encoding="utf-8") as lines:
            for line in lines:
                found[line[0:11]] = line[41:71].strip()
        table["name"] = table.id.map(found).fillna("")
    else:
        table["name"] = ""
    return table


def stations() -> pd.DataFrame | None:
    file = paths.data_dir() / INVENTORY
    return _inventory(str(file), file.stat().st_mtime_ns) if file.is_file() else None


def km_between(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (haversine, Earth radius 6371 km); works on arrays."""
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(np.asarray(lon2) - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def nearest(zone: dict, period: dict, max_km: float = MAX_KM, every: bool = False) -> tuple[list[dict], dict | None]:
    """(candidates, None): the gauges with rain in the period within max_km of the zone centre and within SIMILAR_KM
    of the nearest (all those within max_km with `every`), nearest first; or ([], the nearest beyond max_km, or None)
    when there is none."""
    table = stations()
    west, south, east, north = zone["bbox"]
    lat, lon = (south + north) / 2, (west + east) / 2
    first, last = int(period["start"][:4]), int(period["end"][:4])
    table = table[(table["first"] <= last) & (table["last"] >= first)]
    if table.empty:
        return [], None
    table = table.assign(km=km_between(lat, lon, table.lat.to_numpy(), table.lon.to_numpy())).sort_values(["km", "id"])
    best = float(table.km.iloc[0])
    if best > max_km:
        return [], table.iloc[0].to_dict()
    near = table[(table.km <= max_km) & (every | (table.km <= best + SIMILAR_KM))]
    return [row.to_dict() for _, row in near.iterrows()], None


def _file(station_id: str, last_day: str | None = None):
    return paths.cache() / SOURCE / (f"{station_id}_{last_day}.csv" if last_day else f"{station_id}.csv")


def _saved(station_id: str):
    """The most recent saved series of a station: the refreshed files sort after the first one, by their last day."""
    folder = paths.cache() / SOURCE
    files = sorted([*folder.glob(f"{station_id}.csv"), *folder.glob(f"{station_id}_*.csv")])
    return files[-1] if files else None


def _url(station_id: str, first: int, last: int) -> str:
    return (f"{SERVICE}?dataset=daily-summaries&stations={station_id}&startDate={first}-01-01&endDate={last}-12-31"
            "&dataTypes=PRCP&includeAttributes=true&units=metric&format=csv")


def parse(text: str) -> pd.DataFrame:
    """t (ISO day), v (mm), sflag and qflag of an NCEI daily-summaries CSV; empty values dropped."""
    table = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    if not {"DATE", "PRCP"} <= set(table.columns):
        raise ValueError("the answer has no DATE and PRCP columns")
    flags = (table["PRCP_ATTRIBUTES"] if "PRCP_ATTRIBUTES" in table else pd.Series("", index=table.index))
    parts = flags.str.split(",", expand=True).reindex(columns=range(3)).fillna("")
    out = pd.DataFrame({"t": table["DATE"].str[:10], "v": pd.to_numeric(table["PRCP"].str.strip(), errors="coerce"),
                        "qflag": parts[1].str.strip(), "sflag": parts[2].str.strip()})
    return out.dropna(subset=["v"]).reset_index(drop=True)


def fetch(station: dict, last_year: int | None = None, after: str | None = None) -> bool:
    """The whole series of one station, from the NCEI service, cached with its manifest line. A refresh asks up to
    `last_year` (the inventory can be older than the series) and saves a new file named by its last day, only when
    that day is later than `after`, the last one saved; it says whether it saved one."""
    url = _url(station["id"], int(station["first"]), max(int(station["last"]), last_year or 0))
    failure = "no answer"
    for attempt in range(ATTEMPTS):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as error:
            failure = type(error).__name__
        else:
            if response.ok:
                break
            failure = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                raise Unreachable(f"NCEI refused the request ({failure}).")
        if attempt + 1 < ATTEMPTS:
            time.sleep(2 ** attempt)
    else:
        raise Unreachable(f"NCEI did not answer ({failure}).")
    series = parse(response.text)  # refuse to cache an answer that cannot be read
    if series.empty:
        raise ValueError("the answer holds no rain value")
    if after and series.t.iloc[-1] <= after:
        return False
    file = _file(station["id"], series.t.iloc[-1] if after else None)
    store.write_atomic(file, response.content)
    manifest.record(file, source=SOURCE, item=station["id"], scope="global", fmt="csv", url=url, license=LICENSE,
                    product=PRODUCT, version=VERSION, tier="raw", variables=[DAILY, YEARLY_MAX],
                    bbox=[station["lon"], station["lat"], station["lon"], station["lat"]],
                    period={"start": series.t.iloc[0], "end": series.t.iloc[-1]})
    return True


def days_of(period: dict) -> list[date]:
    first = date.fromisoformat((period["start"] + "-01-01")[:10])
    last = date.fromisoformat(period["end"][:10]) if len(period["end"]) >= 10 else date(int(period["end"][:4]), 12, 31)
    months = set(period.get("months") or range(1, 13))
    return [first + timedelta(n) for n in range((last - first).days + 1) if (first + timedelta(n)).month in months]


def yearly_max(daily: pd.DataFrame, asked: list[date], months=None) -> pd.DataFrame:
    """The wettest day of each year asked for (of `months` when given), only for years with MIN_YEAR_SHARE of the
    days of their calendar year with a value: a period that starts or ends within a year does not make a piece of
    it a year."""
    grouped = daily.groupby(daily.t.str[:4].astype(int))["v"]
    table = pd.DataFrame({"v": grouped.max(), "n": grouped.count()})
    table = table[table.index.isin({day.year for day in asked})]
    whole = np.array([calendar_days(year, months) for year in table.index])
    table = table[table.n.to_numpy() >= MIN_YEAR_SHARE * whole]
    return pd.DataFrame({"t": [str(year) for year in table.index], "v": table.v.to_numpy()})


def _limits(station: dict, series: pd.DataFrame, dropped: int) -> list[dict]:
    km = f"{station['km']:.1f}"
    where = f"{station['id']} {station['name']}".strip()
    limits = [{"en": f"Point measurement at the rain gauge {where} (latitude {station['lat']:.4f}, longitude "
                     f"{station['lon']:.4f}), {km} km from the zone centre: not the rain of the whole zone, and a "
                     "storm can miss it.",
               "es": f"Medida puntual en el pluviómetro {where} (latitud {station['lat']:.4f}, longitud "
                     f"{station['lon']:.4f}), a {km.replace('.', ',')} km del centro de la zona: no es la lluvia de "
                     "toda la zona, y una tormenta puede no pasar por él."}]
    if station.get("fix"):
        limits.append({"en": f"Position corrected: {station['fix']}.",
                       "es": f"Posición corregida: {station['fix_es']}."})
    flags = sorted(set(series.sflag) - {""})
    unknown = ("see the GHCN-Daily readme", "véase el readme de GHCN-Daily")
    known = "; ".join(f"{flag}: {SOURCE_FLAGS.get(flag, unknown)[0]}" for flag in flags)
    conocido = "; ".join(f"{flag}: {SOURCE_FLAGS.get(flag, unknown)[1]}" for flag in flags)
    if flags:
        limits.append({"en": f"Source of the daily values (GHCN source flag) {known}. The day is that of the source "
                             "(GSOD: totals reported during the day, which at Valencia airport match the UTC day "
                             "before best; AEMET: 07 to 07 UTC, labelled with the first day). Not homogenised.",
                       "es": f"Origen de los valores diarios (indicador de fuente de GHCN) {conocido}. El día es el de "
                             "la fuente (GSOD: totales comunicados durante el día, que en el aeropuerto de Valencia "
                             "casan mejor con el día UTC anterior; AEMET: de 07 a 07 UTC, con la fecha del primer "
                             "día). Sin homogeneizar."})
    if dropped:
        limits.append({"en": f"{dropped} daily values with a GHCN quality flag were left out.",
                       "es": f"Se han dejado fuera {dropped} valores diarios con indicador de calidad de GHCN."})
    return limits


def _failure(error: Exception) -> str:
    return str(error) if isinstance(error, Unreachable) else "NCEI gave an answer that cannot be read."


def read_station(variable: str, station: dict, period: dict) -> dict:
    """The series of one known station (a row of `stations()` with its `km`), through the cache."""
    file = _saved(station["id"])
    origin, stale = "cache", []
    offline = settings.load().offline
    if file is None:
        if offline:
            return absent("source_unreachable", f"Offline, and the rain gauge {station['id']} has not been read here "
                          "before.", f"Sin red, y el pluviómetro {station['id']} no se había leído antes aquí.",
                          _source(station))
        try:
            fetch(station)
        except (Unreachable, ValueError) as error:
            return absent("source_unreachable", f"GHCN-Daily could not be read: {_failure(error)}",
                          "No se ha podido leer GHCN-Daily del servicio de NCEI.", _source(station), origin="remote")
        file, origin = _saved(station["id"]), "remote"
    everything = parse(file.read_text(encoding="utf-8"))
    asked = days_of(period)
    last_saved = everything.t.max()
    if asked and asked[-1].isoformat() > last_saved:
        why = None
        if offline:
            why = ("offline, and it was not asked for again", "sin red, y no se ha vuelto a pedir")
        elif max(time.time() - file.stat().st_mtime, 0) / 3600 >= REFRESH_HOURS:
            try:
                newer = fetch(station, min(asked[-1].year, date.today().year), after=last_saved)
            except (Unreachable, ValueError) as error:
                why = (f"it could not be asked for again ({_failure(error)})", "no se ha podido volver a pedir")
            else:
                if newer:
                    file, origin = _saved(station["id"]), "remote"
                    everything = parse(file.read_text(encoding="utf-8"))
                else:
                    os.utime(file)  # asked again, nothing later: not asked for again before REFRESH_HOURS
        last_saved = everything.t.max()
        if asked[-1].isoformat() > last_saved:
            en, es = why or ("the service has no later day", "el servicio no tiene días posteriores")
            saved = f"{last_saved[8:10]}-{last_saved[5:7]}-{last_saved[:4]}"
            stale.append({"en": f"The saved series of the rain gauge {station['id']} ends on {last_saved}: {en}; "
                                "the later days of the period have no value.",
                          "es": f"La serie guardada del pluviómetro {station['id']} acaba el {saved}: {es}; los "
                                "días posteriores del periodo no tienen valor."})
    days = asked
    wanted = {day.isoformat() for day in days}
    inside = everything[everything.t.isin(wanted)]
    good = inside[inside.qflag == ""]
    dropped = len(inside) - len(good)
    daily = good[["t", "v"]].reset_index(drop=True)
    if daily.empty:
        return absent("no_coverage", f"The rain gauge {station['id']} has no daily value in this period.",
                      f"El pluviómetro {station['id']} no tiene ningún valor diario en este periodo.",
                      _source(station), origin=origin)
    since = int(everything.t.iloc[0][:4])
    common = {"absence": None, "origin": origin, "sources": [_source(station, len(daily))],
              "support": {"since": since}, "limits": _limits(station, inside, dropped) + stale,
              "input_fingerprint": None}
    if variable == YEARLY_MAX:
        table = yearly_max(daily, days, period.get("months"))
        if table.empty:
            return absent("insufficient_qa", f"No year has {MIN_YEAR_SHARE:.0%} of its days with a value at the rain "
                          f"gauge {station['id']}.", f"Ningún año tiene el {MIN_YEAR_SHARE:.0%} de sus días con valor "
                          f"en el pluviómetro {station['id']}.", _source(station, len(daily)), origin=origin)
        years = len({day.year for day in days})
        return {**common, "data": table, "unit": "mm",
                "coverage": {"valid_pct": round(100 * len(table) / years, 1), "n_dates": int(len(table))}}
    return {**common, "data": daily, "unit": "mm/day",
            "coverage": {"valid_pct": round(100 * len(daily) / len(days), 1), "n_dates": int(len(daily))}}


@register("2", DAILY, YEARLY_MAX)
def read(variable: str, zone: dict, period: dict, max_km: float = MAX_KM) -> dict:
    if stations() is None:
        return absent("needs_package", "The GHCN-Daily station inventory has not been downloaded on this computer "
                      "(predownload ghcnd-inventory).", "El inventario de estaciones de GHCN-Daily no se ha "
                      "descargado en este ordenador (predescarga ghcnd-inventory).", _source(), origin="package")
    if not days_of(period):
        return absent("no_coverage", "The period asked for has no day.", "El periodo pedido no tiene ningún día.",
                      _source())
    pool, beyond = nearest(zone, period, max_km, every=True)
    if not pool:
        if beyond is None:
            return absent("no_coverage", "No GHCN-Daily rain gauge measures in this period.",
                          "Ningún pluviómetro de GHCN-Daily mide en este periodo.", _source())
        km = f"{beyond['km']:.1f}"
        return absent("no_coverage", f"No GHCN-Daily rain gauge with data in this period lies within {max_km:g} km "
                      f"of the zone centre; the nearest, {beyond['id']} {beyond['name']}, is {km} km away and does "
                      "not speak for this zone.",
                      f"Ningún pluviómetro de GHCN-Daily con datos en este periodo está a menos de {max_km:g} km del "
                      f"centro de la zona; el más cercano, {beyond['id']} {beyond['name']}, está a "
                      f"{km.replace('.', ',')} km y no representa esta zona.", _source())
    empty = []
    while pool:
        # The nearest that are left and those within SIMILAR_KM of it; when none has a value in the period, the next.
        candidates = [c for c in pool if c["km"] <= pool[0]["km"] + SIMILAR_KM]
        pool = pool[len(candidates):]
        ranked, skipped = [], []
        for candidate in candidates:  # count the valid years of each: the series is read (and cached) for that
            got = read_station(YEARLY_MAX if len(candidates) > 1 else variable, candidate, period)
            reason = (got["absence"] or {}).get("reason")
            if reason == "source_unreachable":
                skipped.append((candidate["id"], got["origin"] == "remote", got))
            elif reason == "no_coverage":
                empty.append(candidate)
            else:
                ranked.append((0 if reason else len(got["data"]), candidate, got))
        if skipped and not ranked:
            # Not read, not known to be empty: the answer is that absence, without asking the service again.
            return skipped[0][2]
        if not ranked:
            continue
        ranked.sort(key=lambda item: (-item[0], item[1]["km"], item[1]["id"]))
        chosen = ranked[0]
        result = chosen[2] if len(candidates) == 1 else read_station(variable, chosen[1], period)
        if result["absence"] is None:
            result["limits"] = [*result["limits"], *_choice_limits([r[:2] for r in ranked], skipped, empty)]
        return result
    codes = ", ".join(c["id"] for c in empty)
    return absent("no_coverage", f"No GHCN-Daily rain gauge within {max_km:g} km of the zone centre has a daily value "
                  f"in this period ({codes}).", f"Ningún pluviómetro de GHCN-Daily a menos de {max_km:g} km del centro "
                  f"de la zona tiene valores diarios en este periodo ({codes}).", _source())


def _choice_limits(ranked: list[tuple[int, dict]], skipped: list[tuple], empty: list[dict]) -> list[dict]:
    limits = []
    if len(ranked) > 1:
        en = ", ".join(f"{c['id']} ({c['km']:.1f} km, {n} valid years)" for n, c in ranked[1:])
        es = ", ".join(f"{c['id']} ({c['km']:.1f} km, {n} años válidos)".replace(".", ",", 1) for n, c in ranked[1:])
        limits.append({"en": f"Other rain gauges at a similar distance (within {SIMILAR_KM:g} km of the nearest): "
                             f"{en}. The one read has the most years with 95% of their days ({ranked[0][0]}).",
                       "es": f"Otros pluviómetros a distancia parecida (a menos de {SIMILAR_KM:g} km del más "
                             f"cercano): {es}. El leído es el que tiene más años con el 95 % de sus días "
                             f"({ranked[0][0]})."})
    offline = [code for code, tried, _ in skipped if not tried]
    failed = [code for code, tried, _ in skipped if tried]
    if offline:
        limits.append({"en": f"Not compared (offline and not read here before): {', '.join(offline)}.",
                       "es": f"No comparados (sin red y sin lectura previa aquí): {', '.join(offline)}."})
    if failed:
        limits.append({"en": f"Not compared (the NCEI service did not give their series): {', '.join(failed)}.",
                       "es": f"No comparados (el servicio de NCEI no ha dado su serie): {', '.join(failed)}."})
    if empty:
        en = ", ".join(f"{c['id']} ({c['km']:.1f} km)" for c in empty)
        es = ", ".join(f"{c['id']} ({c['km']:.1f} km)".replace(".", ",", 1) for c in empty)
        limits.append({"en": f"Also looked at, without any daily value in this period: {en}.",
                       "es": f"También mirados, sin ningún valor diario en este periodo: {es}."})
    return limits


def station(station_id: str) -> dict:
    """A row of the inventory by its code (position corrected when it is in COORDINATE_FIXES), for analyses that
    name the gauge; `km` is 0."""
    table = stations()
    return {**table[table.id == station_id].iloc[0].to_dict(), "km": 0.0}
