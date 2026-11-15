"""The bench itself: cases.yaml is complete and the dry run passes every case, spending nothing."""
import importlib.util
import json
from pathlib import Path

import yaml

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("assistant_bench_run", HERE / "run.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_cases_cover_p1_to_p8_and_e1_to_e4_with_two_wordings():
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    assert [c["id"] for c in cases] == [f"P{i}" for i in range(1, 9)] + [f"E{i}" for i in range(1, 5)]
    for case in cases:
        assert len(case["wordings"]) == 2 and case["expect"]["accept"] and case["double"]


def test_the_dry_run_passes_every_case_and_saves_a_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    assert bench.main(["--dry-run", "--reports", str(tmp_path / "reports")]) == 0
    out = capsys.readouterr().out
    assert "12/12 cases passed" in out
    (report,) = list((tmp_path / "reports").glob("*.json"))
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["dry_run"] and data["passed"] == data["total"] == 12
    assert data["cost_usd"] == 0.0 and data["input_tokens"] == 0


def test_a_wrong_answer_fails_with_its_reason():
    case = {"expect": {"accept": [{"kind": "plan", "design": "trend", "levels": ["complete"]}]}}
    answer = {"kind": "plan", "plan": {"design": "state", "variables": [], "steps": [],
                                       "answer": {"level": "complete"}}}
    assert "design state is not trend" in bench.check(answer, case)[0]
    causal = {"kind": "plan", "plan": {"design": "trend", "variables": [], "steps": [],
                                       "answer": {"level": "complete", "limits": [{"en": "It is due to the plant."}]}}}
    assert "causal" in bench.check(causal, case)[0]


def test_a_real_run_needs_provider_and_model():
    import pytest

    with pytest.raises(SystemExit):
        bench.main([])
