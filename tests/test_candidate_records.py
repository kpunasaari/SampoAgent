from pathlib import Path


def test_candidate_can_manage_experience_education_certificates_and_licences(tmp_path: Path) -> None:
    from sampoagent.db.repository import Repository

    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    repository.add_candidate_record("experience", {"title": "Cleaner", "employer": "Example Oy", "start": "2023-01"})
    repository.add_candidate_record("education", {"name": "Vocational qualification"})
    repository.add_candidate_record("certificate", {"name": "Hygiene passport"})
    repository.add_candidate_record("licence", {"name": "B driving licence"})

    assert repository.candidate_records("experience")[0]["title"] == "Cleaner"
    assert repository.candidate_records("licence")[0]["name"] == "B driving licence"
