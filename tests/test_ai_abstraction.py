from pathlib import Path


def test_deterministic_ai_provider_never_requires_credentials() -> None:
    from sampoagent.ai.provider import DeterministicProvider

    provider = DeterministicProvider()
    result = provider.interpret_requirement({"title": "Cleaner", "mandatory_requirements": ["Finnish"]})

    assert result.provider == "none"
    assert result.used_tokens == 0
    assert "Finnish" in result.value


def test_semantic_cache_can_be_saved_read_and_cleared(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    repository.cache_put("requirement:v1", "confirmed")
    assert repository.cache_get("requirement:v1") == "confirmed"
    repository.clear_cache()
    assert repository.cache_get("requirement:v1") is None
