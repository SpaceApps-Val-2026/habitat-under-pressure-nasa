"""The report for the population living in hot cells answers in its first sentence, from the series itself."""

from hup_api.reviewer.text import CAUSAL
from hup_api.writer.opening import state_answer

VALUES = [791, 19969, 13484, 18364, 21518, 27019, 6912, 37066, 484, 43251, 36951, 23777, 38049]
RESULT = {
    "id": "r-hot", "variable": "population_in_hot_cells", "type": "series", "unit": "people",
    "method": {"operation": "read_series"}, "sources": [{"source": "landsat-c2-l2"}],
    "diagnostics": {"threshold_c": 43.02},
    "series": [{"t": f"{2013 + i}-01-01", "v": v} for i, v in enumerate(VALUES)],
}
PLAN = {"design": "state", "zone": {"name": "Alzira"}}
PIECE = {"headline": {"en": "h", "es": "h"}, "lead": {"en": "old", "es": "viejo"},
         "meaning": {"en": "old", "es": "viejo"}, "cannot_say": {"en": "Kept.", "es": "Se mantiene."},
         "results": ["r-other"]}


def test_first_sentence_answers_with_the_latest_mean_and_peak_of_the_series():
    piece = state_answer(PLAN, [RESULT], PIECE)
    assert piece["lead"]["en"].startswith("About 38,000 people in Alzira lived in hot cells in 2025, the latest summer")
    assert "average was 22,100 and the peak 43,300 (2022)" in piece["lead"]["en"]
    assert piece["lead"]["es"].startswith("Unas 38.000 personas en Alzira vivían en celdas calientes en 2025")
    assert "la media fue de 22.100 y el máximo de 43.300 (2022)" in piece["lead"]["es"]
    assert "43.02 °C" in piece["meaning"]["en"] and "43,02 °C" in piece["meaning"]["es"]
    assert piece["results"] == ["r-hot", "r-other"]


def test_limit_gives_the_lowest_and_highest_summer_without_a_cause():
    piece = state_answer(PLAN, [RESULT], PIECE)
    assert piece["cannot_say"]["en"].startswith("Kept. ")
    assert "from 484 (2021) to 43,300 (2022); it is not a trend" in piece["cannot_say"]["en"]
    assert "de 484 (2021) a 43.300 (2022); no es una tendencia" in piece["cannot_say"]["es"]
    for field in ("lead", "meaning", "cannot_say"):
        for text in piece[field].values():
            assert not CAUSAL.search(text)
    assert PIECE["lead"]["en"] == "old"  # the original piece is not changed


def test_it_leaves_other_variables_designs_and_empty_series_alone():
    assert state_answer(PLAN, [{**RESULT, "variable": "lst"}], PIECE) is None
    assert state_answer({**PLAN, "design": "trend"}, [RESULT], PIECE) is None
    assert state_answer(PLAN, [{**RESULT, "absence": {"reason": "no_coverage"}}], PIECE) is None
    assert state_answer(PLAN, [{**RESULT, "series": RESULT["series"][:1]}], PIECE) is None
