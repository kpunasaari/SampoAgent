from pathlib import Path


def test_ai_usage_records_and_summarizes_provider_metadata(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    repository.record_ai_usage(
        provider="example-provider",
        model="example-model",
        feature="requirement_interpretation",
        input_tokens=120,
        output_tokens=40,
        cached_tokens=20,
    )

    summary = repository.ai_usage_summary()
    assert summary == {"requests": 1, "input_tokens": 120, "output_tokens": 40, "cached_tokens": 20}


def test_agent_page_exposes_zero_usage_without_provider() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    response = TestClient(create_app(database_path=":memory:")).get("/agent")

    assert "AI usage: 0 requests" in response.text
