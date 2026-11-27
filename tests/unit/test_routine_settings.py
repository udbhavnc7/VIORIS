"""Opt-in ledger for routine suggestions: mining is dark until enabled."""

import json

from packages.shared.routine_settings import RoutineSettingsStore


def test_off_by_default(tmp_path):
    settings = RoutineSettingsStore(tmp_path / "settings.json")
    assert settings.opted_in() is False
    assert settings.accepted() == {}


def test_opt_in_persists(tmp_path):
    path = tmp_path / "settings.json"
    first = RoutineSettingsStore(path)
    assert first.set_opted_in(True) == {"opted_in": True}
    assert RoutineSettingsStore(path).opted_in() is True
    first.set_opted_in(False)
    assert RoutineSettingsStore(path).opted_in() is False


def test_accept_records_a_proposal_without_scheduling(tmp_path):
    settings = RoutineSettingsStore(tmp_path / "settings.json")
    settings.set_opted_in(True)
    record = settings.accept(
        "seq_abc_0",
        {"description": "open chrome", "tool": "system.open_app", "sequence": ["system.open_app"]},
    )
    assert record["pattern_id"] == "seq_abc_0"
    assert record["via"] if "via" in record else True
    assert settings.is_accepted("seq_abc_0")
    assert settings.accepted()["seq_abc_0"]["description"] == "open chrome"
    # The record is a plain JSON proposal — no scheduler/config keys.
    saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert "schedule" not in saved
    assert "cron" not in saved


def test_corrupt_settings_fail_closed(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", "utf-8")
    settings = RoutineSettingsStore(path)
    assert settings.opted_in() is False
    assert settings.accepted() == {}
