"""`percentile` and `base_period` through the common door `readers.read_series`: they reach only the readers that take
them, enter the derived key only when given (today's key is unchanged without them), and the reader's `threshold`
comes back on a derived read. No network: fake readers, test values only."""
import pandas as pd
import pytest

from hup_data import fingerprint, readers

ZONE = {"id": "z", "name": "Z", "bbox": [0.0, 0.0, 0.1, 0.1]}
PERIOD = {"start": "2020-01-01", "end": "2020-12-31"}
BASE = {"start": "2010-01-01", "end": "2011-12-31"}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    readers._load_all()
    calls = []

    def answer(value, **extra):
        return {"data": pd.DataFrame({"t": ["2020-06-01"], "v": [value]}), "absence": None, "origin": "cache",
                "sources": [{"source": "fake", "url": "urn:fake"}], "support": {}, "coverage": {}, "unit": "1",
                "input_fingerprint": None, **extra}

    def thresholded(variable, zone, period, *, percentile=None, base_period=None):
        calls.append(("thresholded", percentile, base_period))
        if percentile is None:
            return answer(0.0)
        return answer(1.0, threshold={"value_c": 1.5, "kind": "base_percentile", "percentile": percentile,
                                      "base_period": base_period, "n_values": 4})

    def plain(variable, zone, period):
        calls.append(("plain",))
        return answer(5.0)

    monkeypatch.setitem(readers.READERS, "fake_thresholded", ("1", thresholded))
    monkeypatch.setitem(readers.READERS, "fake_plain", ("1", plain))
    return calls


def today_key(variable):
    """The derived key as written before `percentile` and `base_period` existed."""
    return fingerprint.sha({"read_series": variable, "reader": "1", "zone": fingerprint.zone_key(ZONE),
                            "bbox": ZONE["bbox"], "period": PERIOD})


def test_without_them_the_call_and_the_key_are_todays(fake):
    got = readers.read_series("fake_thresholded", ZONE, PERIOD)
    assert fake == [("thresholded", None, None)] and got["input_fingerprint"] == today_key("fake_thresholded")
    assert "threshold" not in got


def test_they_reach_the_reader_and_change_the_key(fake):
    without = readers.read_series("fake_thresholded", ZONE, PERIOD)
    given = readers.read_series("fake_thresholded", ZONE, PERIOD, percentile=50, base_period=BASE)
    assert fake[-1] == ("thresholded", 50, BASE)
    assert given["input_fingerprint"] not in (None, without["input_fingerprint"])
    other = readers.read_series("fake_thresholded", ZONE, PERIOD, percentile=60, base_period=BASE)
    assert other["input_fingerprint"] != given["input_fingerprint"]


def test_the_threshold_is_saved_and_comes_back_on_a_derived_read(fake):
    first = readers.read_series("fake_thresholded", ZONE, PERIOD, percentile=50, base_period=BASE)
    saved = readers.read_series("fake_thresholded", ZONE, PERIOD, percentile=50, base_period=BASE)
    assert saved["origin"] == "derived" and saved["threshold"] == first["threshold"]
    assert saved["threshold"]["percentile"] == 50 and saved["threshold"]["base_period"] == BASE
    assert len(fake) == 1
    assert "threshold" not in readers.read_series("fake_thresholded", ZONE, PERIOD)


def test_a_reader_that_does_not_take_them_is_called_as_today(fake):
    got = readers.read_series("fake_plain", ZONE, PERIOD, percentile=50, base_period=BASE)
    assert fake == [("plain",)] and got["input_fingerprint"] == today_key("fake_plain")
