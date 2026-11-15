"""The one definition of the seasons used by the product, and how a dated value maps to the year of its season.

Decided by Guillem on 14 November 2026 (A5 preparation note, section 13): summer is June to September in the northern
hemisphere, as the PRD sets for the Landsat heat and green layers, and December to March in the southern one.
"""
import calendar

SEASONS = {"summer": {"north": (6, 7, 8, 9), "south": (12, 1, 2, 3)}}


def season_months(season: str, zone: dict) -> list[int]:
    """The months of a season at a zone; the hemisphere is the one of the middle of its bounding box."""
    _, south, _, north = zone["bbox"]
    return list(SEASONS[season]["south" if (south + north) / 2 < 0 else "north"])


def bounds(months) -> tuple[int, int]:
    """First and last month of a set of months that may cross the new year: the season starts after its longest gap."""
    ordered = sorted(set(months or range(1, 13)))
    if len(ordered) == 12:
        return 1, 12
    _, i = max(((ordered[(k + 1) % len(ordered)] - month) % 12, k) for k, month in enumerate(ordered))
    return ordered[(i + 1) % len(ordered)], ordered[i]


def season_year(date, months) -> int:
    """The year a dated value counts in: the year its season ends, so December 2020 belongs to the summer of 2021."""
    text = str(date)
    year, month = int(text[:4]), (int(text[5:7]) if len(text) >= 7 else 1)
    first, last = bounds(months)
    return year + 1 if first > last and month >= first else year


def season_end(year: int, months) -> str:
    """Last day of the season that counts in a year."""
    _, last = bounds(months)
    return f"{year}-{last:02d}-{calendar.monthrange(year, last)[1]:02d}"
