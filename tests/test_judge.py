"""python judge.py is what a judge runs first: each of its checks must pass on the committed data."""
import pytest

import judge


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "OUT", tmp_path)


@pytest.mark.parametrize("check", [judge.check_seal, judge.check_receipt, judge.check_flip,
                                   judge.check_real_plots, judge.check_nightshift])
def test_check_passes(check):
    ok, detail = check()
    assert ok, detail


def test_forged_receipt_is_written_and_caught(tmp_path):
    ok, detail = judge.check_receipt()
    assert ok and "TAMPERED" in detail and "PROVEN" in detail
    assert (tmp_path / "receipt.json").exists() and (tmp_path / "receipt_forged.json").exists()


def test_quick_run_reports_every_check(tmp_path, capsys):
    assert judge.main(["--quick"]) == 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert report.count("| PASS |") == len(judge.CHECKS) - 2 and "FAIL" not in report
