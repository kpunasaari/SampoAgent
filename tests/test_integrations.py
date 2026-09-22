from pathlib import Path


def test_finland_pack_has_required_builtin_sources() -> None:
    from sampoagent.country_packs.finland import builtin_sources

    names = {source.name for source in builtin_sources()}
    assert {"Duunitori", "Työmarkkinatori", "Jobly", "Valtiolle", "Kuntarekry", "Barona", "StaffPoint", "Eezy", "Bolt.Works", "Adecco Finland", "Manpower Finland", "Academic Work Finland", "Seure"} <= names


def test_browser_agent_unavailable_result_requires_manual_action() -> None:
    from sampoagent.agents.browser import UnconfiguredBrowserAgent

    result = UnconfiguredBrowserAgent().submit("https://example.test/application")
    assert result.submitted is False
    assert result.manual_action_required is True
    assert "not configured" in result.message.lower()


def test_cli_initializes_database_without_network(tmp_path: Path) -> None:
    from sampoagent.cli import initialize

    path = tmp_path / "local.db"
    initialize(path, demo=True)
    assert path.exists()
