from pathlib import Path


def test_activity_log_records_important_local_actions(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    repository.add_skill("cleaning")

    actions = repository.recent_activity()
    assert actions[0]["action"] == "skill_added"
