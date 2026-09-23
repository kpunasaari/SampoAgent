from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.db.repository import Repository


def test_candidate_records_and_career_targets_can_be_added_edited_and_removed(tmp_path):
    app = create_app(database_path=tmp_path / "profile-controls.db")
    client = TestClient(app)
    repository = app.state.repository
    repository.save_profile("Kai", "en")

    client.post("/profile/records", data={"record_type": "experience", "title": "Cleaner", "details": "Office cleaning"})
    record = repository.candidate_record_rows("experience")[0]
    assert "Remove record" in client.get("/profile").text
    client.post(f"/profile/records/{record['id']}/edit", data={"title": "Senior Cleaner", "details": "Team lead"})
    assert repository.candidate_records("experience")[0]["title"] == "Senior Cleaner"
    assert "Senior Cleaner" in repository.confirmed_fact_values()

    client.post("/careers/profiles", data={"name": "Cleaning", "notes": "Day work"})
    profile = repository.rows("career_profiles")[0]
    assert "Edit" in client.get("/careers").text
    client.post(f"/careers/profiles/{profile['id']}/edit", data={"name": "Facilities", "notes": "Part-time"})
    assert repository.career_profile(profile["id"])["name"] == "Facilities"

    client.post("/careers/targets", data={"title_en": "Cleaner", "title_fi": "Siivooja"})
    target = repository.target_occupations()[0]
    client.post(f"/careers/targets/{target['id']}/delete")
    assert repository.target_occupation(target["id"]) is None

    client.post(f"/profile/records/{record['id']}/delete")
    assert repository.candidate_record_rows("experience") == []
    assert "Senior Cleaner" not in repository.confirmed_fact_values()


def test_deleting_licence_record_removes_it_from_matching_facts(tmp_path):
    app = create_app(database_path=tmp_path / "licence-delete.db")
    client = TestClient(app)
    repository = app.state.repository
    repository.save_profile("Kai", "en")
    client.post("/profile/records", data={"record_type": "licence", "title": "B-ajokortti", "details": ""})
    record = repository.candidate_record_rows("licence")[0]
    assert "B-ajokortti" in repository.confirmed_fact_values()
    client.post(f"/profile/records/{record['id']}/delete")
    assert "B-ajokortti" not in repository.confirmed_fact_values()


def test_deleting_record_preserves_independently_confirmed_fact(tmp_path):
    app = create_app(database_path=tmp_path / "independent-fact.db")
    client = TestClient(app)
    repository = app.state.repository
    repository.save_profile("Kai", "en")
    client.post("/profile/records", data={"record_type": "licence", "title": "B-ajokortti", "details": ""})
    repository.add_confirmed_fact(fact_type="licence", value="B-ajokortti")
    record = repository.candidate_record_rows("licence")[0]

    client.post(f"/profile/records/{record['id']}/delete")

    assert repository.confirmed_fact_values().count("B-ajokortti") == 1


def test_unambiguous_legacy_record_fact_pair_is_linked_then_deleted(tmp_path):
    app = create_app(database_path=tmp_path / "legacy-linked-fact.db")
    repository = app.state.repository
    record_id = repository.add_candidate_record("licence", {"title": "B-ajokortti", "details": ""})
    repository.add_confirmed_fact(fact_type="licence", value="B-ajokortti", source_id="profile")

    repository.migrate_unambiguous_candidate_fact_links()
    repository.delete_candidate_record(record_id)

    assert "B-ajokortti" not in repository.confirmed_fact_values()


def test_ambiguous_legacy_record_fact_pairs_are_preserved_for_review(tmp_path):
    app = create_app(database_path=tmp_path / "legacy-ambiguous-fact.db")
    repository = app.state.repository
    record_id = repository.add_candidate_record("licence", {"title": "B-ajokortti", "details": ""})
    repository.add_confirmed_fact(fact_type="licence", value="B-ajokortti", source_id="profile")
    repository.add_confirmed_fact(fact_type="licence", value="B-ajokortti", source_id="profile")

    repository.migrate_unambiguous_candidate_fact_links()
    repository.delete_candidate_record(record_id)

    assert repository.confirmed_fact_values().count("B-ajokortti") == 2


def test_reinitializing_does_not_relink_independent_fact_to_new_record(tmp_path):
    db_path = tmp_path / "restart-independent-fact.db"
    app = create_app(database_path=db_path)
    repository = app.state.repository
    record_id = repository.add_candidate_record("licence", {"title": "B-ajokortti", "details": ""})
    repository.add_confirmed_fact(fact_type="licence", value="B-ajokortti", source_id=f"candidate_record:{record_id}")
    repository.add_confirmed_fact(fact_type="licence", value="B-ajokortti", source_id="profile")

    reopened = Repository(db_path)
    reopened.initialize()
    reopened.delete_candidate_record(record_id)

    assert reopened.confirmed_fact_values().count("B-ajokortti") == 1


def test_foreign_host_is_rejected(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "host-check.db"), base_url="http://testserver")
    response = client.get("/profile", headers={"Host": "attacker.example"})
    assert response.status_code == 400
