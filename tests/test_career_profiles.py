from pathlib import Path


def test_multiple_career_profiles_can_be_created_disabled_and_deleted(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    first = repository.add_career_profile("Cleaning", "Facilities work")
    second = repository.add_career_profile("Logistics", "Warehouse roles")
    repository.set_career_profile_enabled(first, False)

    assert len(repository.rows("career_profiles")) == 2
    assert repository.career_profile(first)["enabled"] == 0
    repository.delete_career_profile(second)
    assert len(repository.rows("career_profiles")) == 1
