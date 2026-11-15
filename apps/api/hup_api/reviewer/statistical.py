"""Provisional checks for diagnostics emitted by scientific operations."""


def multiple_tests(result):
    d = result.get("diagnostics", {})
    count = max(d.get("n_tests", 0), d.get("lags_tested", 0))
    table = result.get("table", {})
    columns = table.get("columns", [])
    has_adjusted = False
    if "p_adjusted" in columns:
        index = columns.index("p_adjusted")
        has_adjusted = any(
            isinstance(row, list) and len(row) > index and type(row[index]) in (int, float) and 0 <= row[index] <= 1
            for row in table["rows"]
        )
    if count > 1 and not (
        d.get("correction") == "Benjamini-Hochberg" or "p_adjusted" in result.get("statistic", {}) or has_adjusted
    ):
        return "E0"


def short_record(result):
    d = result.get("diagnostics", {})
    params = result.get("method", {}).get("params", {})
    for observed, minimum in (("n_years", "min_years"), ("n_dates", "min_dates")):
        if observed in d and minimum in params and d[observed] < params[minimum]:
            return "E0"


# Corrections of the trend test for serial correlation that the methods front has adopted. The current one is
# Yue-Wang with the first lag and a variance never below the classical one; Hamed-Rao is what archived results carry.
AUTOCORRELATION_CORRECTIONS = {"yue_wang_lag1_floor", "Hamed-Rao"}


def autocorrelation(result):
    d = result.get("diagnostics", {})
    has_memory = isinstance(d.get("autocorrelation_lag1"), (int, float))
    if has_memory and d.get("correction") not in AUTOCORRELATION_CORRECTIONS:
        return "E0"


def coverage(result):
    valid_pct = result.get("coverage", {}).get("valid_pct")
    minimum = result.get("method", {}).get("params", {}).get("min_valid_pct")
    if valid_pct is not None and minimum is not None and valid_pct < minimum:
        return "E0"


def coarse_grid(result):
    if result.get("support", {}).get("cells", float("inf")) <= 1:
        return result.get("evidence_level", "E0")


def pre_trend_control(result):
    if result.get("diagnostics", {}).get("pre_trend_control") is True:
        return "E1"


def interval_crosses_zero(result):
    interval = result.get("interval")
    if result.get("diagnostics", {}).get("interval_crosses_zero") is True or (
        interval
        and interval["low"] <= 0 <= interval["high"]
        and result["type"] == "value"
        and result.get("method", {}).get("operation")
        in {
            "trend",
            "scan_trends",
            "baci_contrast",
            "distance_decay",
            "lagged_association",
        }
    ):
        result["direction_assertable"] = False
        return "E0"


RULES = (
    (multiple_tests, "Multiple tests require adjusted probabilities.", "Las pruebas múltiples requieren ajuste."),
    (short_record, "The record is shorter than required.", "El registro es más corto de lo exigido."),
    (autocorrelation, "Autocorrelation correction is missing.", "Falta corregir la autocorrelación."),
    (coverage, "Coverage is below the declared minimum.", "La cobertura está por debajo del mínimo declarado."),
    (coarse_grid, "Grid support is at least as large as the area.", "La celda es al menos tan grande como la zona."),
    (pre_trend_control, "The site and reference were already diverging.", "La zona y su referencia ya divergían."),
    (interval_crosses_zero, "The interval includes zero.", "El intervalo incluye el cero."),
)
