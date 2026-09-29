import json
import sqlite3
from hashlib import sha256

import pytest

from sampoagent.db.repository import Repository


def _seed_learning_history(repository, tmp_path):
    repository.initialize()
    repository.save_profile("Private Candidate Name", "en", "private-candidate@example.test")
    cv_path = tmp_path / "private-cv.pdf"
    cv_path.write_bytes(b"private cv bytes for a synthetic candidate")
    checksum = sha256(cv_path.read_bytes()).hexdigest()
    repository.archive_cv(
        path=str(cv_path), checksum=checksum, language="en", role_family="warehouse_logistics",
        source_job_id=None, fit_score=93, ats_score=100, strategy="generated",
    )
    repository.add_document(kind="application_cv", path=str(cv_path), checksum=checksum)
    outcomes = ("INTERVIEW", "ASSESSMENT", "OFFER", "REJECTED", "REJECTED")
    for index, outcome in enumerate(outcomes):
        cursor = repository.connection.execute(
            "INSERT INTO jobs(title, company, location, language, description, application_url, fingerprint, verification_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "Warehouse Worker — confidential posting", "Private Employer 901", "Private Location",
                "en", "Candidate-specific private requirement", f"https://example.test/private/{index}",
                f"private-learning-{index}", "VERIFIED",
            ),
        )
        repository.connection.commit()
        application_id = repository.queue_application(int(cursor.lastrowid), language="en", cv_path=str(cv_path))
        repository.update_application_status(application_id, outcome, "Private free-form recruiter note")
    return cv_path, checksum


def test_learning_digest_shares_only_aggregated_confirmed_role_and_cv_outcomes(tmp_path):
    repository = Repository(tmp_path / "learning.db")
    cv_path, checksum = _seed_learning_history(repository, tmp_path)

    digest = repository.learning_digest()

    assert digest["data_boundary"] == {
        "scope": "aggregated_confirmed_outcomes_only",
        "candidate_facts_included": False,
        "cv_content_included": False,
        "direct_identifiers_included": False,
    }
    role = next(item for item in digest["role_families"] if item["role_family"] == "warehouse_logistics")
    assert role == {
        "role_family": "warehouse_logistics",
        "confirmed_outcomes": 5,
        "positive_outcomes": 3,
        "ranking_adjustment": 5,
    }
    cv_group = next(item for item in digest["cv_groups"] if item["role_family"] == "warehouse_logistics")
    assert cv_group == {
        "role_family": "warehouse_logistics",
        "language": "en",
        "cv_variants": 1,
        "confirmed_outcomes": 5,
        "positive_outcomes": 3,
        "mature_variants": 1,
        "mean_selection_adjustment": 5,
    }

    serialized = json.dumps(digest, ensure_ascii=False)
    for private_value in (
        "Private Candidate Name", "private-candidate@example.test", "Private Employer 901",
        "confidential posting", "Private Location", "Candidate-specific private requirement",
        "Private free-form recruiter note", str(cv_path), checksum,
    ):
        assert private_value not in serialized
    repository.connection.close()


def test_learning_digest_suppresses_groups_below_five_confirmed_outcomes(tmp_path):
    repository = Repository(tmp_path / "small-learning.db")
    _seed_learning_history(repository, tmp_path)
    repository.connection.execute(
        "DELETE FROM application_learning WHERE application_id = (SELECT MAX(application_id) FROM application_learning)"
    )
    repository.connection.commit()

    digest = repository.learning_digest()

    assert not digest["role_families"]
    assert not digest["cv_groups"]
    repository.connection.close()


def test_application_receipt_is_not_a_hiring_outcome_or_learning_signal(tmp_path):
    repository = Repository(tmp_path / "receipt-learning.db")
    _seed_learning_history(repository, tmp_path)
    repository.connection.execute(
        "UPDATE application_learning SET outcome='APPLICATION_RECEIVED' "
        "WHERE application_id = (SELECT MAX(application_id) FROM application_learning)"
    )
    repository.connection.commit()

    assert repository.learning_adjustment("warehouse_logistics") == 0
    digest = repository.learning_digest()
    assert not digest["role_families"]
    assert not digest["cv_groups"]
    repository.connection.close()


def test_read_only_repository_cannot_write_or_create_a_missing_database(tmp_path):
    database_path = tmp_path / "existing.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.set_setting("codex_learning_summary_enabled", "true")
    repository.connection.close()
    before = database_path.read_bytes()

    readonly = Repository.open_read_only(database_path)
    assert readonly.setting("codex_learning_summary_enabled") == "true"
    with pytest.raises(sqlite3.OperationalError):
        readonly.set_setting("codex_learning_summary_enabled", "false")
    readonly.connection.close()

    assert database_path.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        Repository.open_read_only(missing)
    assert not missing.exists()
