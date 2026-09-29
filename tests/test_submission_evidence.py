from pathlib import Path


def test_submission_evidence_stores_safe_confirmation_not_credentials(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    evidence_id = repository.add_submission_evidence(application_id=1, final_url="https://careers.northstar-logistics.fi/thanks", confirmation_message="Application received", confirmation_id="ABC-1", agent_provider="manual")

    evidence = repository.submission_evidence(evidence_id)
    assert evidence["confirmation_id"] == "ABC-1"
    assert "password" not in evidence


def test_submission_evidence_rejects_unsafe_destinations():
    import pytest

    from sampoagent.db.repository import Repository

    repository = Repository(":memory:")
    repository.initialize()
    for url in ("http://careers.northstar-logistics.fi/thanks", "https://127.0.0.1/thanks", "https://careers.northstar-logistics.fi/thanks?session=private"):
        with pytest.raises(ValueError, match="safe public HTTPS"):
            repository.add_submission_evidence(application_id=1, final_url=url, confirmation_message="Application received", confirmation_id=None, agent_provider="manual")
