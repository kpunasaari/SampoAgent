import sqlite3

from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.applications.field_resolver import FormField, resolve_application_fields
from sampoagent.candidate.questions import question_metadata
from sampoagent.db.repository import Repository


def test_onboarding_confirmation_persists_stable_id_and_validity(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post("/onboarding/questions/experience", data={"certificates": "First aid, valid through 2030-12-31"})
    assert "valid_until:experience:certificates" in client.get("/onboarding?section=review").text

    response = client.post(
        "/onboarding/confirm",
        data={
            "confirmed": ["experience:certificates"],
            "valid_until:experience:certificates": "2030-12-31",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    answer = client.app.state.repository.answers()[0]
    assert answer["question_id"] == "experience:certificates"
    assert answer["answer_state"] == "CONFIRMED"
    assert answer["scope_type"] == "GLOBAL"
    assert answer["valid_until"] == "2030-12-31"
    assert answer["confirmed_at"]


def test_question_metadata_defines_stable_type_sensitivity_and_scope():
    salary = question_metadata("availability:salary")
    email = question_metadata("contact:email")
    education = question_metadata("experience:education")
    permission = question_metadata("eligibility:work_permission")

    assert salary and (salary.category, salary.value_type) == ("PREFERENCE", "amount")
    assert email and (email.value_type, email.sensitivity) == ("contact_link", "PERSONAL")
    assert education and education.value_type == "repeatable"
    assert permission and (permission.scope_type, permission.sensitivity, permission.supports_expiry) == ("COUNTRY", "HIGH", True)


def test_employer_scoped_salary_answer_is_reviewed_and_resolved_only_for_that_employer(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    response = client.post(
        "/answers",
        data={
            "category": "PREFERENCE",
            "question": "What is your expected salary?",
            "value": "3 200 EUR/month",
            "source": "USER_CONFIRMED",
            "scope_type": "EMPLOYER",
            "scope_employer": "Northstar Oy",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    row = client.app.state.repository.answers()[0]
    assert row["question_id"] == "availability:salary"
    assert (row["scope_type"], row["scope_employer"], row["answer_state"]) == ("EMPLOYER", "Northstar Oy", "CONFIRMED")

    review = client.get("/answers").text
    assert "Employer: Northstar Oy" in review
    assert "What is your expected salary?" in review

    field = FormField("salary", "What is your expected salary?", True)
    same_employer = resolve_application_fields(
        [field], profile={}, facts=[], answers=client.app.state.repository.answers(), employer="Northstar Oy",
    )
    another_employer = resolve_application_fields(
        [field], profile={}, facts=[], answers=client.app.state.repository.answers(), employer="Other Employer",
    )
    assert same_employer.ready is True
    assert same_employer.values == {"salary": "3 200 EUR/month"}
    assert another_employer.ready is False
    assert another_employer.needs_input == ("salary",)


def test_custom_application_specific_answer_cannot_be_saved_as_reusable_employer_answer(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    response = client.post(
        "/answers",
        data={
            "category": "MOTIVATION",
            "question": "Why do you want to work for this employer and role?",
            "value": "Because I like the role",
            "source": "USER_CONFIRMED",
            "scope_type": "EMPLOYER",
            "scope_employer": "Northstar Oy",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.app.state.repository.answers() == []


def test_cv_confirmed_licence_and_questionnaire_answer_conflict_stops_only_that_field():
    results = resolve_application_fields(
        [
            FormField("licence", "What driving licence categories do you hold?", True),
            FormField("salary", "What is your expected salary?", True),
        ],
        profile={},
        facts=[{"type": "licence", "value": "B", "confirmed": 1, "rejected": 0, "provenance": "CV_CONFIRMED"}],
        answers=[
            {
                "question_id": "experience:licences", "category": "FACT", "question": "Driving licence categories",
                "value": "C", "source": "USER_CONFIRMED", "answer_state": "CONFIRMED", "scope_type": "GLOBAL",
            },
            {
                "question_id": "availability:salary", "category": "PREFERENCE", "question": "What is your expected salary?",
                "value": "3 000 EUR/month", "source": "USER_CONFIRMED", "answer_state": "CONFIRMED", "scope_type": "GLOBAL",
            },
        ],
    )
    assert results.ready is False
    assert results.conflicts == ("licence",)
    assert results.values == {"salary": "3 000 EUR/month"}


def test_work_permission_confirmation_is_country_scoped(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post(
        "/onboarding/questions/eligibility",
        data={"work_country": "Finland", "work_permission": "Yes"},
    )

    response = client.post(
        "/onboarding/confirm",
        data={
            "confirmed": ["eligibility:work_permission"],
            "scope_country:eligibility:work_permission": "Finland",
            "valid_until:eligibility:work_permission": "2030-12-31",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    answer = client.app.state.repository.answers()[0]
    assert answer["scope_type"] == "COUNTRY"
    assert answer["scope_country"] == "Finland"
    assert answer["valid_until"] == "2030-12-31"


def test_blank_country_and_application_specific_answers_cannot_be_promoted(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post(
        "/onboarding/questions/eligibility",
        data={"work_country": "", "work_permission": "Yes"},
    )

    missing_country = client.post(
        "/onboarding/confirm",
        data={"confirmed": ["eligibility:work_permission"]},
        follow_redirects=False,
    )
    assert missing_country.status_code == 422
    assert client.app.state.repository.answers() == []

    client.post(
        "/onboarding/questions/materials",
        data={"reference_contact": "Ask me for each application"},
    )
    application_specific = client.post(
        "/onboarding/confirm",
        data={"confirmed": ["materials:reference_contact"]},
        follow_redirects=False,
    )
    assert application_specific.status_code == 422
    assert client.app.state.repository.answers() == []


def test_unknown_and_declined_responses_are_retained_but_not_reused(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post(
        "/onboarding/questions/eligibility",
        data={"work_country": "Finland", "work_permission": "Unsure"},
    )
    client.post(
        "/onboarding/confirm",
        data={"confirmed": ["eligibility:work_permission"]},
        follow_redirects=False,
    )
    unknown = client.app.state.repository.answers()[0]
    assert unknown["answer_state"] == "UNKNOWN"

    client.post(
        "/onboarding/questions/eligibility",
        data={"work_country": "Finland", "work_permission": "Prefer not to answer"},
    )
    client.post(
        "/onboarding/confirm",
        data={"confirmed": ["eligibility:work_permission"]},
        follow_redirects=False,
    )
    current = client.app.state.repository.answers()[0]
    assert current["answer_state"] == "DECLINED"
    assert current["question_id"] == "eligibility:work_permission"
    answer_page = client.get("/answers").text
    assert "Declined" in answer_page
    assert "Finland" in answer_page


def test_country_scoped_answer_is_not_used_for_another_or_unknown_country():
    field = FormField("eligibility", "Do you currently have permission to work in that country?", True, "select", ("Yes", "No"))
    answer = {
        "category": "FACT",
        "question": "Do you currently have permission to work in that country?",
        "question_id": "eligibility:work_permission",
        "value": "Yes",
        "source": "USER_CONFIRMED",
        "answer_state": "CONFIRMED",
        "scope_type": "COUNTRY",
        "scope_country": "Finland",
        "valid_until": "2030-12-31",
    }

    finland = resolve_application_fields([field], profile={}, facts=[], answers=[answer], country="Finland")
    sweden = resolve_application_fields([field], profile={}, facts=[], answers=[answer], country="Sweden")
    unknown_country = resolve_application_fields([field], profile={}, facts=[], answers=[answer])

    assert finland.ready is True
    assert finland.values == {"eligibility": "Yes"}
    assert sweden.ready is False
    assert sweden.needs_input == ("eligibility",)
    assert unknown_country.ready is False


def test_expired_declined_and_uncertain_answers_never_fill_a_required_field():
    field = FormField("eligibility", "Do you currently have permission to work in that country?", True, "select", ("Yes", "No"))
    base = {
        "category": "FACT",
        "question": "Do you currently have permission to work in that country?",
        "question_id": "eligibility:work_permission",
        "value": "Yes",
        "source": "USER_CONFIRMED",
        "scope_type": "COUNTRY",
        "scope_country": "Finland",
    }

    for answer in (
        {**base, "answer_state": "CONFIRMED", "valid_until": "2000-01-01"},
        {**base, "answer_state": "DECLINED", "valid_until": "2030-12-31"},
        {**base, "answer_state": "UNKNOWN", "valid_until": "2030-12-31"},
        {**base, "answer_state": "NEEDS_RECONFIRMATION", "valid_until": "2030-12-31"},
    ):
        result = resolve_application_fields([field], profile={}, facts=[], answers=[answer], country="Finland")
        assert result.ready is False
        assert result.values == {}


def test_legacy_answer_rows_are_preserved_and_unscoped_eligibility_is_held(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE answer_bank (id INTEGER PRIMARY KEY, category TEXT NOT NULL, question TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO answer_bank VALUES (1, 'PREFERENCE', 'Preferred work locations', 'Vantaa', 'USER_CONFIRMED', '2026-09-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO answer_bank VALUES (2, 'FACT', 'Do you currently have permission to work in that country?', 'Yes', 'USER_CONFIRMED', '2026-09-01T00:00:00+00:00')"
        )

    repository = Repository(path)
    repository.initialize()
    migrated = {str(answer["question"]): answer for answer in repository.answers()}

    assert len(migrated) == 2
    assert migrated["Preferred work locations"]["question_id"] == "preferences:locations"
    assert migrated["Preferred work locations"]["answer_state"] == "CONFIRMED"
    assert migrated["Do you currently have permission to work in that country?"]["question_id"] == "eligibility:work_permission"
    assert migrated["Do you currently have permission to work in that country?"]["answer_state"] == "NEEDS_RECONFIRMATION"
    assert migrated["Do you currently have permission to work in that country?"]["confirmed_at"] == "2026-09-01T00:00:00+00:00"


def test_unrecognized_manual_answer_requires_review_before_form_reuse():
    repository = Repository(":memory:")
    repository.initialize()
    repository.add_answer("FACT", "Have you previously applied to this employer?", "No", "USER_CONFIRMED")

    result = resolve_application_fields(
        [FormField("history", "Have you previously applied to this employer?", True, "select", ("Yes", "No"))],
        profile={}, facts=[], answers=repository.answers(), employer="Example Oy",
    )

    assert result.ready is False
    assert result.needs_input == ("history",)


def test_conflicting_confirmed_values_for_one_scoped_question_are_held():
    repository = Repository(":memory:")
    repository.initialize()
    question = "Do you currently have permission to work in that country?"
    repository.add_answer("FACT", question, "Yes", "USER_CONFIRMED", scope_country="Finland")
    repository.add_answer("FACT", question, "No", "USER_CONFIRMED", scope_country="Finland")

    result = resolve_application_fields(
        [FormField("eligibility", question, True, "select", ("Yes", "No"))],
        profile={}, facts=[], answers=repository.answers(), country="Finland",
    )

    assert result.ready is False
    assert result.conflicts == ("eligibility",)
    assert all(answer["answer_state"] == "CONFLICT" for answer in repository.answers())


def test_confirmed_practical_skills_feed_the_candidate_profile(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post(
        "/onboarding/questions/skills",
        data={"practical_skills": "painting; sanding\nwood repair"},
    )
    client.post(
        "/onboarding/confirm",
        data={"confirmed": ["skills:practical_skills"]},
        follow_redirects=False,
    )

    repository = client.app.state.repository
    assert set(repository.confirmed_skills()) == {"painting", "sanding", "wood repair"}
    assert all(
        fact["provenance"] == "USER_CONFIRMED" and fact["source_id"].startswith("answer_bank:")
        for fact in repository.rows("facts")
    )


def test_hobby_and_transferable_skills_help_recommendations_without_becoming_cv_skill_claims(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post(
        "/onboarding/questions/skills",
        data={"other_skills": "customer service, logistics"},
    )
    client.post(
        "/onboarding/confirm",
        data={"confirmed": ["skills:other_skills"]},
        follow_redirects=False,
    )

    repository = client.app.state.repository
    assert repository.confirmed_skills() == []
    assert set(repository.recommendation_skills()) == {"customer service", "logistics"}
    assert "Customer Service Representative" in client.get("/careers").text


def test_reconfirmed_skill_answer_deactivates_old_materialized_facts(tmp_path):
    client = TestClient(create_app(database_path=str(tmp_path / "candidate.db")))
    client.post("/onboarding/questions/skills", data={"practical_skills": "painting"})
    client.post("/onboarding/confirm", data={"confirmed": ["skills:practical_skills"]})

    client.post("/onboarding/questions/skills", data={"practical_skills": "wood repair"})
    client.post("/onboarding/confirm", data={"confirmed": ["skills:practical_skills"]})

    repository = client.app.state.repository
    assert repository.confirmed_skills() == ["wood repair"]
