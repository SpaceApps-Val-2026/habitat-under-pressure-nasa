"""`land_only` through the common door `readers.read_series`: the derived key is today's when it is absent or false,
it differs when it is set, and a reader that cannot mask ignores it and says so. No network: fake readers."""
import pandas as pd
import pytest

from hup_data import fingerprint, readers, store

ZONE = {"id": "z", "name": "Z", "bbox": [0.0, 0.0, 0.1, 0.1]}
PERIOD = {"start": "2020-01-01", "end": "2020-12-31"}
APPLIED = {"applied": True, "detail": {"en": "Water out.", "es": "Sin agua."}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    readers._load_all()
    calls = []

    def answer(value):
        return {"data": pd.DataFrame({"t": ["2020-06-01"], "v": [value]}), "absence": None, "origin": "cache",
                "sources": [{"source": "fake", "url": "urn:fake"}], "support": {}, "coverage": {}, "unit": "1",
                "input_fingerprint": None}

    def masking(variable, zone, period, land_only=False):
        calls.append(("masking", land_only))
        return {**answer(2.0 if land_only else 1.0), **({"land_mask": APPLIED} if land_only else {})}

    def plain(variable, zone, period):
        calls.append(("plain", None))
        return answer(5.0)

    monkeypatch.setitem(readers.READERS, "fake_masking", ("1", masking))
    monkeypatch.setitem(readers.READERS, "fake_plain", ("1", plain))
    return calls


def today_key(variable):
    """The derived key as written before `land_only` existed."""
    return fingerprint.sha({"read_series": variable, "reader": "1", "zone": fingerprint.zone_key(ZONE),
                            "bbox": ZONE["bbox"], "period": PERIOD})


def test_default_read_keeps_todays_key_and_call(fake):
    got = readers.read_series("fake_masking", ZONE, PERIOD)
    assert fake == [("masking", False)] and got["input_fingerprint"] == today_key("fake_masking")
    assert "land_mask" not in got
    again = readers.read_series("fake_masking", ZONE, PERIOD, land_only=False)
    assert again["origin"] == "derived" and again["input_fingerprint"] == today_key("fake_masking")
    assert len(fake) == 1


def test_masked_and_unmasked_reads_never_share_a_saved_series(fake):
    unmasked = readers.read_series("fake_masking", ZONE, PERIOD)
    masked = readers.read_series("fake_masking", ZONE, PERIOD, land_only=True)
    assert masked["input_fingerprint"] != unmasked["input_fingerprint"]
    assert masked["data"]["v"].tolist() == [2.0] and masked["land_mask"] == APPLIED
    saved = readers.read_series("fake_masking", ZONE, PERIOD, land_only=True)
    assert saved["origin"] == "derived" and saved["data"]["v"].tolist() == [2.0] and saved["land_mask"] == APPLIED
    assert readers.read_series("fake_masking", ZONE, PERIOD)["data"]["v"].tolist() == [1.0]
    assert fake == [("masking", False), ("masking", True)]


def test_without_a_water_tile_the_masked_read_is_not_saved(fake, monkeypatch):
    def no_tile(variable, zone, period, land_only=False):
        fake.append(("no_tile", land_only))
        return {"data": pd.DataFrame({"t": ["2020-06-01"], "v": [1.0]}), "absence": None, "origin": "cache",
                "sources": [], "support": {}, "coverage": {}, "unit": "1", "input_fingerprint": None,
                "land_mask": {"applied": False, "detail": {"en": "No tile.", "es": "Sin tesela."}}}

    monkeypatch.setitem(readers.READERS, "fake_masking", ("1", no_tile))
    got = readers.read_series("fake_masking", ZONE, PERIOD, land_only=True)
    assert got["land_mask"]["applied"] is False and got["input_fingerprint"] is None
    assert store.get_derived(today_key("fake_masking")) is None


def test_a_reader_that_cannot_mask_ignores_it_and_says_so(fake):
    got = readers.read_series("fake_plain", ZONE, PERIOD, land_only=True)
    assert fake == [("plain", None)] and got["absence"] is None
    assert got["land_mask"] == readers.NO_LAND_MASK and got["input_fingerprint"] == today_key("fake_plain")
    saved = readers.read_series("fake_plain", ZONE, PERIOD, land_only=True)
    assert saved["origin"] == "derived" and saved["land_mask"] == readers.NO_LAND_MASK
    assert "land_mask" not in readers.read_series("fake_plain", ZONE, PERIOD)


def test_the_landsat_reader_takes_the_flag():
    readers._load_all()
    assert readers._masks(readers.READERS["lst"][1]) and readers._masks(readers.READERS["ndvi"][1])
    assert not readers._masks(readers.READERS["air_temperature"][1])
