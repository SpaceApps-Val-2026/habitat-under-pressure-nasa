from hup_ops.registry import REGISTRY, Context


def test_historical_window_and_missing_years():
    entries = {
        "read": [
            {
                "unit": "mm/month",
                "series": [
                    {"t": f"{year}-{month:02d}", "v": year - 2000}
                    for year in range(2000, 2005)
                    for month in (10, 11)
                    if (year, month) != (2003, 11)
                ],
            }
        ]
    }
    ctx = Context({}, {}, 0, None, lambda *_: None)
    params = {"year": 2004, "months": [10, 11], "min_years": 3}
    rain = REGISTRY["rain_extreme"].fn(params, entries, ctx)["extreme"]
    assert rain["value"] == 100
    assert rain["diagnostics"] == {"rank": 1, "n_years": 4}
    anomaly = REGISTRY["same_date_anomaly"].fn(params, entries, ctx)["anomaly"]
    assert anomaly["value"] == 6
    missing = REGISTRY["rain_extreme"].fn({**params, "min_years": 4}, entries, ctx)["extreme"]
    assert missing["absence"]["reason"] == "insufficient_qa"


def test_ties_and_future_windows():
    ctx = Context({}, {}, 0, None, lambda *_: None)
    entry = {
        "unit": "mm/month",
        "series": [{"t": f"{year}-10", "v": value} for year, value in enumerate([1, 2, 3, 2, 4], start=2000)],
    }
    op = REGISTRY["rain_extreme"].fn
    params = {"year": 2003, "months": [10], "min_years": 3}
    result = op(params, {"read": [entry]}, ctx)["extreme"]
    assert result["value"] == 100 * 2 / 3
    assert result["diagnostics"]["rank"] == 2
    entry["unit"] = "mm/day"
    assert "absence" in op(params, {"read": [entry]}, ctx)["extreme"]
