"""The one-command launcher must expose the local agents the executor routes to."""

import json

from run_vioris import gmail_credentials, local_agent_services


def test_local_agents_have_reachable_health_probes():
    services = local_agent_services(include_computer=True)
    assert [(s.key, s.port, s.health, s.optional) for s in services] == [
        ("computer", 8430, "/windows", True),
        ("browser", 8431, "/health", True),
        ("documents", 8432, "/health", True),
        ("knowledge", 8442, "/knowledge/health", True),
    ]
    assert services[1].module == "agents.browser.app.daemon:app"
    assert services[2].module == "agents.documents.app.daemon:app"
    assert services[3].module == "services.knowledge_service.app.api:app"


def test_helper_can_exclude_computer_agent():
    services = local_agent_services(include_computer=False)
    assert [s.key for s in services] == ["browser", "documents", "knowledge"]


def test_gmail_credentials_read_gitignored_file(monkeypatch, tmp_path):
    for name in (
        "VIORIS_GMAIL_CLIENT_ID",
        "VIORIS_GMAIL_CLIENT_SECRET",
        "VIORUS_GMAIL_CLIENT_ID",
        "VIORUS_GMAIL_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("run_vioris.DATA", tmp_path)
    (tmp_path / "gmail_credentials.json").write_text(
        json.dumps({"client_id": "launcher-id", "client_secret": "launcher-secret"}),
        encoding="utf-8",
    )
    assert gmail_credentials() == ("launcher-id", "launcher-secret")


def test_gmail_credentials_env_wins_and_corrupt_file_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("VIORIS_GMAIL_CLIENT_ID", "env-id")
    monkeypatch.setenv("VIORIS_GMAIL_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr("run_vioris.DATA", tmp_path)
    (tmp_path / "gmail_credentials.json").write_text("{bad", encoding="utf-8")
    assert gmail_credentials() == ("env-id", "env-secret")

    monkeypatch.delenv("VIORIS_GMAIL_CLIENT_ID")
    monkeypatch.delenv("VIORIS_GMAIL_CLIENT_SECRET")
    assert gmail_credentials() == ("", "")
