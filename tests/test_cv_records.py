from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.candidate.service import ingest_text_cv
from sampoagent.cv.service import check_pdf_text, generate_cv_pdf


def test_swedish_work_and_education_lines_become_unconfirmed_dated_records(tmp_path):
    result = ingest_text_cv(
        "ARBETSLIVSERFARENHET\n2021-03–2024-05 Städare — Northstar Oy, Vantaa\n"
        "UTBILDNING\n2018–2020 Lokalvårdsutbildning — Omnia\n",
        source_id="sample-sv.txt",
        storage_dir=tmp_path,
    )

    records = {(record.record_type, record.title): record for record in result.records}
    work = records[("experience", "Städare")]
    school = records[("education", "Lokalvårdsutbildning")]
    assert (work.organization, work.start_date, work.end_date, work.is_current) == (
        "Northstar Oy, Vantaa", "2021-03", "2024-05", False,
    )
    assert (school.organization, school.start_date, school.end_date) == ("Omnia", "2018", "2020")
    assert work.evidence.line == 2
    assert work.evidence.excerpt == "2021-03–2024-05 Städare — Northstar Oy, Vantaa"
    assert all(not record.confirmed for record in result.records)


def test_ongoing_work_record_preserves_present_marker_without_guessing_dates(tmp_path):
    result = ingest_text_cv(
        "Work Experience\nCleaner | Northstar Oy | 2022-present\n",
        source_id="sample-en.txt",
        storage_dir=tmp_path,
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert (record.title, record.organization, record.start_date, record.end_date, record.is_current) == (
        "Cleaner", "Northstar Oy", "2022", "", True,
    )


def test_generated_cv_includes_confirmed_structured_dates_and_employer(tmp_path):
    path = generate_cv_pdf(
        output_dir=tmp_path,
        language="en",
        role_family="cleaning_facilities",
        candidate={"name": "Aino Example", "email": "aino@example.test"},
        facts=[],
        records={"experience": [{
            "title": "Cleaner",
            "organization": "Northstar Oy",
            "start_date": "2021-02",
            "end_date": "2023-04",
            "is_current": False,
            "details": "2021-02–2023-04 Cleaner | Northstar Oy",
            "evidence": {"excerpt": "2021-02–2023-04 Cleaner | Northstar Oy"},
        }]},
    )

    report = check_pdf_text(path, required=["Cleaner", "Northstar Oy", "2021-02", "2023-04"])
    assert report.passed is True


def test_cv_upload_keeps_structured_records_as_review_drafts_until_confirmed(tmp_path):
    app = create_app(database_path=tmp_path / "records.db", storage_dir=tmp_path / "storage")
    client = TestClient(app)
    response = client.post(
        "/cvs/upload",
        files={"file": ("history.txt", b"Work Experience\n2022-present Cleaner | Northstar Oy\n", "text/plain")},
        follow_redirects=True,
    )

    repository = app.state.repository
    row = next(item for item in repository.candidate_record_rows("experience") if item["title"] == "Cleaner")
    assert response.status_code == 200
    assert "Northstar Oy" in response.text
    assert "Review needed" in response.text
    assert repository.candidate_records("experience") == []

    confirmed = client.post(f"/profile/records/{row['id']}/confirm", follow_redirects=True)
    assert confirmed.status_code == 200
    assert repository.candidate_records("experience")[0]["title"] == "Cleaner"
    assert repository.candidate_records("experience")[0]["start_date"] == "2022"


def test_changed_cv_does_not_replace_confirmed_history_without_user_resolution(tmp_path):
    app = create_app(database_path=tmp_path / "changed-records.db", storage_dir=tmp_path / "storage")
    client = TestClient(app)
    client.post(
        "/cvs/upload",
        files={"file": ("one.txt", b"Work Experience\n2020-2022 Cleaner | Northstar Oy\n", "text/plain")},
    )
    repository = app.state.repository
    old_draft = next(item for item in repository.candidate_record_rows("experience") if item["title"] == "Cleaner")
    client.post(f"/profile/records/{old_draft['id']}/confirm")

    client.post(
        "/cvs/upload",
        files={"file": ("two.txt", b"Work Experience\n2021-2023 Cleaner | Northstar Oy\n", "text/plain")},
    )

    rows = [item for item in repository.candidate_record_rows("experience") if item["title"] == "Cleaner"]
    existing = next(item for item in rows if item.get("review_state") == "CONFIRMED")
    conflict = next(item for item in rows if item.get("review_state") == "CONFLICT")
    assert existing["start_date"] == "2020"
    assert conflict["start_date"] == "2021"
    assert conflict["conflict_with_record_id"] == existing["id"]
    assert len(repository.candidate_records("experience")) == 1
    assert repository.candidate_records("experience")[0]["start_date"] == "2020"

    page = client.get("/profile").text
    assert "This CV version differs" in page
    assert "Keep existing record" in page
    assert "Use CV version" in page

    resolved = client.post(f"/profile/records/{conflict['id']}/resolve", data={"decision": "use_new"}, follow_redirects=False)
    assert resolved.status_code == 303
    assert len(repository.candidate_records("experience")) == 1
    assert repository.candidate_records("experience")[0]["start_date"] == "2021"
    assert next(item for item in repository.candidate_record_rows("experience") if item["id"] == existing["id"])["review_state"] == "SUPERSEDED"
