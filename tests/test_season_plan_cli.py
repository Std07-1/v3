"""CLI плану S7 (ADR-0095 S7.1, `python -m tools.repair.season_plan_report`): лише читання, звіт у stdout, JSON
плану поза data_root; будь-яка відмова — rc=2 з SEASON_PLAN_REFUSED, а не план частини символів.
"""
from __future__ import annotations

import json
import logging

from season_plan_synthetic import CFG, dataset, ms
from tools.repair.season_plan_report import main


def _config(tmp_path, data_root) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(dict(CFG, data_root=str(data_root), symbols=["XAU/USD"])), encoding="utf-8")
    return str(path)


def test_cli_prints_report_and_writes_json_outside_data_root_without_touching_data(tmp_path, capsys):
    root = tmp_path / "data_v3"
    dataset(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    out = tmp_path / "plan.json"
    rc = main(["--scope", "derived_from_m1,d1_rekey", "--config", _config(tmp_path, root), "--symbols", "XAU_USD",
               "--from", "2026-03-04", "--report-json", str(out)])
    assert rc == 0
    report = capsys.readouterr().out
    assert report.startswith("S7_PLAN scopes=derived_from_m1,d1_rekey ") and "window=2026-03-04T00:00..* " in report
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["scopes"] == ["derived_from_m1", "d1_rekey"] and doc["window"][0] == ms(2026, 3, 4)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before, "план нічого не пише в data_root"


def test_cli_refusals_exit_2(tmp_path, capsys, caplog):
    root = tmp_path / "data_v3"
    dataset(root)
    config = _config(tmp_path, root)
    bad_changed = tmp_path / "changed.json"
    bad_changed.write_text(json.dumps({"XAU_USD": ["x"]}), encoding="utf-8")
    cases = [
        ["--scope", "derived_from_m1", "--report-json", str(root / "plan.json")],
        ["--scope", "derived_from_m1", "--symbols", "NOPE"],
        ["--scope", "derived_from_m1", "--changed-m1", str(bad_changed)],
        ["--scope", "h4"],
    ]
    with caplog.at_level(logging.ERROR):
        for args in cases:
            assert main(args + ["--config", config]) == 2, args
    assert caplog.text.count("SEASON_PLAN_REFUSED") == len(cases)
    assert not (root / "plan.json").exists()
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(dict(CFG, day_anchor_offset_s=79200)), encoding="utf-8")
    assert main(["--scope", "derived_from_m1", "--config", str(legacy)]) == 2


def test_cli_changed_m1_limits_the_plan(tmp_path, capsys):
    root = tmp_path / "data_v3"
    dataset(root)
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps({"XAU_USD": [ms(2026, 3, 5, 14, 37)]}), encoding="utf-8")
    out = tmp_path / "plan.json"
    assert main(["--scope", "derived_from_m1", "--changed-m1", str(changed), "--config", _config(tmp_path, root),
                 "--report-json", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["changed_m1"] is True
    assert {tf: v["rebuild"] for tf, v in doc["symbols"][0]["tf"].items()} == {
        tf: 1 for tf in ("180", "300", "900", "1800", "3600", "14400", "86400")}
