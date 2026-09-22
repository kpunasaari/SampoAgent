from pathlib import Path


def test_submission_evidence_stores_safe_confirmation_not_credentials(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    evidence_id = repository.add_submission_evidence(application_id=1, final_url="https://example.test/thanks", confirmation_message="Application received", confirmation_id="ABC-1", agent_provider="manual")

    evidence = repository.submission_evidence(evidence_id)
    assert evidence["confirmation_id"] == "ABC-1"
    assert "password" not in evidence
