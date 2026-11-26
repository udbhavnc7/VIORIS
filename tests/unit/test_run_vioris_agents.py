"""The one-command launcher must expose the local agents the executor routes to."""

from run_vioris import local_agent_services


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
