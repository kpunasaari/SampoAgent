import pytest

from sampoagent.db.repository import Repository


def _application(repository: Repository, job_id: int = 1) -> int:
    return repository.queue_application(job_id, language="en", cv_path=None)


def _question(field_id: str, *, label: str | None = None, kind: str = "text", options=(), required: bool = True, risk: str = "LOW") -> dict[str, object]:
    return {
        "field_id": field_id,
        "label": label or field_id.replace("_", " ").title(),
        "description": "Answer only if this is accurate for you.",
        "kind": kind,
        "options": list(options),
        "required": required,
        "risk": risk,
    }


def _repository(tmp_path) -> Repository:
    repository = Repository(tmp_path / "application-questions.db")
    repository.initialize()
    repository.load_demo()
    return repository


def test_application_answers_are_confirmed_once_and_never_reused_for_another_application(tmp_path):
    repository = _repository(tmp_path)
    first_application = _application(repository, 1)
    other_application = _application(repository, 2)
    repository.update_application_status(first_application, "NEEDS_USER", "Missing required answer", queue_state="WAITING_USER")
    before_global_answers = repository.answers()
    before_facts = repository.rows("facts")

    assert repository.register_application_questions(
        first_application,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[_question("preferred_shift", label="Which shift can you work?", kind="select", options=("Day", "Night"))],
    ) == 1
    question = repository.application_questions(first_application)[0]
    assert question["label"] == "Which shift can you work?"
    assert repository.application_form_answers(first_application, form_signature="a" * 64, listing_hash="b" * 64) == {}

    with pytest.raises(ValueError):
        repository.answer_application_question(first_application, int(question["id"]), "Night", confirmed=False)

    repository.answer_application_question(first_application, int(question["id"]), "Night", confirmed=True)

    assert repository.application_form_answers(first_application, form_signature="a" * 64, listing_hash="b" * 64) == {"preferred_shift": "Night"}
    assert repository.application_form_answers(other_application, form_signature="a" * 64, listing_hash="b" * 64) == {}
    assert repository.answers() == before_global_answers
    assert repository.rows("facts") == before_facts


def test_application_answer_requires_exact_saved_choice_and_ready_transition_waits_for_all_questions(tmp_path):
    repository = _repository(tmp_path)
    application_id = _application(repository)
    repository.update_application_status(application_id, "NEEDS_USER", "Missing required answers", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[
            _question("shift", kind="radio", options=("Day", "Night")),
            _question("start_date", kind="date"),
        ],
    )
    questions = repository.application_questions(application_id)
    shift = next(item for item in questions if item["field_id"] == "shift")
    start_date = next(item for item in questions if item["field_id"] == "start_date")

    with pytest.raises(ValueError):
        repository.answer_application_question(application_id, int(shift["id"]), "Any shift", confirmed=True)

    repository.answer_application_question(application_id, int(shift["id"]), "Day", confirmed=True)
    assert repository.application(application_id)["queue_state"] == "WAITING_USER"
    assert repository.application(application_id)["status"] == "NEEDS_USER"

    repository.answer_application_question(application_id, int(start_date["id"]), "2026-10-01", confirmed=True)
    assert repository.application(application_id)["queue_state"] == "READY"
    assert repository.application(application_id)["status"] == "QUEUED"


def test_optional_high_risk_and_unsupported_fields_are_not_collected(tmp_path):
    repository = _repository(tmp_path)
    application_id = _application(repository)

    count = repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[
            _question("optional_note", required=False),
            _question("work_authorization", label="Are you authorized to work in Finland?", risk="HIGH"),
            _question("attachments", kind="file"),
            _question("unknown_multi", kind="checkbox", options=("A", "B")),
        ],
    )

    assert count == 0
    assert repository.application_questions(application_id) == []


def test_stale_form_or_listing_answer_cannot_resolve_and_old_pending_question_is_hidden(tmp_path):
    repository = _repository(tmp_path)
    application_id = _application(repository)
    repository.update_application_status(application_id, "NEEDS_USER", "Missing required answer", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[_question("start_date", kind="date")],
    )
    first = repository.application_questions(application_id)[0]
    repository.answer_application_question(application_id, int(first["id"]), "2026-10-01", confirmed=True)

    assert repository.application_form_answers(application_id, form_signature="c" * 64, listing_hash="b" * 64) == {}
    assert repository.application_form_answers(application_id, form_signature="a" * 64, listing_hash="d" * 64) == {}

    repository.update_application_status(application_id, "NEEDS_USER", "Listing changed", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="c" * 64,
        listing_hash="d" * 64,
        questions=[_question("availability", label="When can you start?", kind="date")],
    )

    visible_questions = repository.application_questions(application_id)
    assert [item["field_id"] for item in visible_questions] == ["availability"]
    assert repository.application_form_answers(application_id, form_signature="c" * 64, listing_hash="d" * 64) == {}


def test_obsolete_question_is_staled_when_it_is_no_longer_missing_on_same_form(tmp_path):
    repository = _repository(tmp_path)
    application_id = _application(repository)
    repository.update_application_status(application_id, "NEEDS_USER", "Missing required answer", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[_question("shift")],
    )

    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[],
    )

    assert repository.application_questions(application_id) == []


def test_application_question_server_validates_email_and_number_and_preserves_textarea_paragraphs(tmp_path):
    repository = _repository(tmp_path)
    application_id = _application(repository)
    repository.update_application_status(application_id, "NEEDS_USER", "Missing required answers", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[
            _question("email", kind="email"),
            _question("salary", kind="number"),
            _question("note", kind="textarea"),
        ],
    )
    questions = {str(item["field_id"]): item for item in repository.application_questions(application_id)}

    with pytest.raises(ValueError):
        repository.answer_application_question(application_id, int(questions["email"]["id"]), "not an email", confirmed=True)
    with pytest.raises(ValueError):
        repository.answer_application_question(application_id, int(questions["salary"]["id"]), "NaN", confirmed=True)
    repository.answer_application_question(application_id, int(questions["note"]["id"]), "First paragraph\n\nSecond paragraph", confirmed=True)

    saved = repository.application_form_answers(application_id, form_signature="a" * 64, listing_hash="b" * 64)
    assert saved["note"] == "First paragraph\n\nSecond paragraph"


def test_application_answer_changes_only_application_context_not_autopilot_grant_or_learning_digest(tmp_path):
    import json

    repository = _repository(tmp_path)
    application_id = _application(repository)
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "5")
    repository.set_setting("dry_run", "false")
    repository.set_setting("automation_paused", "false")
    repository.grant_autopilot()
    assert repository.autopilot_authorized()
    repository.update_application_status(application_id, "NEEDS_USER", "Missing required answer", queue_state="WAITING_USER")
    repository.register_application_questions(
        application_id,
        form_signature="a" * 64,
        listing_hash="b" * 64,
        questions=[_question("availability", label="When can you start?", kind="date")],
    )
    question = repository.application_questions(application_id)[0]
    before_context = repository.application_context_fingerprint(application_id)
    before_digest = json.dumps(repository.learning_digest(), sort_keys=True)

    repository.answer_application_question(application_id, int(question["id"]), "2026-10-01", confirmed=True)

    assert repository.application_context_fingerprint(application_id) != before_context
    assert repository.autopilot_authorized()
    assert json.dumps(repository.learning_digest(), sort_keys=True) == before_digest
    assert "2026-10-01" not in json.dumps(repository.learning_digest(), sort_keys=True)


def test_existing_database_gets_application_answer_table_additively(tmp_path):
    database_path = tmp_path / "existing-local-profile.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.load_demo()
    application_id = repository.queue_application(1, language="en", cv_path=None)
    repository.connection.execute("DROP TABLE application_form_answers")
    repository.connection.commit()
    repository.connection.close()

    upgraded = Repository(database_path)
    upgraded.initialize()

    assert upgraded.application(application_id) is not None
    assert upgraded.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_form_answers'").fetchone()
    assert upgraded.application_questions(application_id) == []
    upgraded.connection.close()
