def test_applications_can_record_safe_submission_evidence() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    client.post("/queue/prepare/1")
    response = client.post(
        "/applications/1/evidence",
        data={
            "final_url": "https://example.test/thanks",
            "confirmation_message": "Application received",
            "confirmation_id": "ABC-1",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Application received" in response.text
    assert "ABC-1" in response.text


def test_submission_evidence_rejects_password_like_content() -> None:
    from pathlib import Path
    import pytest

    from sampoagent.db.repository import Repository

    repository = Repository(Path(":memory:"))
    repository.initialize()
    with pytest.raises(ValueError, match="credential"):
        repository.add_submission_evidence(
            application_id=1,
            final_url="https://example.test/thanks",
            confirmation_message="password: never-save-this",
            confirmation_id=None,
            agent_provider="manual",
        )
