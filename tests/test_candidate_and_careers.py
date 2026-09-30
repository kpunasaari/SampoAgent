from pathlib import Path


def test_cv_text_extraction_creates_unconfirmed_provenanced_facts(tmp_path: Path) -> None:
    from sampoagent.candidate.service import ingest_text_cv

    result = ingest_text_cv(
        "Aino Example\naino@example.test\nSkills: forklift operation, customer service\nLanguages: Finnish, English",
        source_id="cv-1",
        storage_dir=tmp_path,
    )

    assert result.warning is None
    assert {fact.value for fact in result.facts} >= {"forklift operation", "customer service"}
    assert all(fact.provenance == "CV_EXTRACTED" for fact in result.facts)
    assert all(fact.confirmed is False for fact in result.facts)


def test_cv_extraction_supports_finnish_sections_and_preserves_page_line_evidence(tmp_path: Path) -> None:
    from sampoagent.candidate.service import ingest_text_cv

    result = ingest_text_cv(
        "OSAAMINEN\n- Lattianhoito\n- Trukin k\u00e4ytt\u00f6\fKIELET\n- Suomi (sujuva)\nTODISTUKSET\n- Ty\u00f6turvallisuuskortti\nTUTKINNOT\n- Ammattitutkinto",
        source_id="sample-fi.pdf",
        storage_dir=tmp_path,
    )

    extracted = {(fact.type, fact.value): fact for fact in result.facts}
    skill = extracted[("skill", "Lattianhoito")]
    language = extracted[("language", "Suomi (sujuva)")]
    certificate = extracted[("certificate", "Ty\u00f6turvallisuuskortti")]
    education = extracted[("education", "Ammattitutkinto")]
    assert skill.evidence.page == 1
    assert skill.evidence.line == 2
    assert skill.evidence.excerpt == "- Lattianhoito"
    assert language.evidence.page == 2
    assert certificate.evidence.page == 2
    assert education.evidence.page == 2
    assert all(not fact.confirmed for fact in result.facts)


def test_cv_extraction_supports_common_english_resume_headings_and_deduplicates(tmp_path: Path) -> None:
    from sampoagent.candidate.service import ingest_text_cv

    result = ingest_text_cv(
        "CORE COMPETENCIES\nForklift operation\nCustomer service\nKEY SKILLS\nCustomer service\nWORK EXPERIENCE\nWarehouse associate — Example Oy\nEDUCATION & TRAINING\nForklift safety course",
        source_id="sample-en.txt",
        storage_dir=tmp_path,
    )

    pairs = [(fact.type, fact.value) for fact in result.facts]
    assert pairs.count(("skill", "Customer service")) == 1
    assert ("skill", "Forklift operation") in pairs
    assert ("experience", "Warehouse associate — Example Oy") in pairs
    assert ("education", "Forklift safety course") in pairs
    assert all(fact.evidence.line > 0 and fact.evidence.start_char < fact.evidence.end_char for fact in result.facts)


def test_scanned_cv_reports_that_optional_ocr_or_manual_review_is_needed(tmp_path: Path) -> None:
    from sampoagent.candidate.service import ingest_text_cv

    result = ingest_text_cv("\f\f", source_id="scanned.pdf", storage_dir=tmp_path)

    assert result.facts == []
    assert result.warning
    assert "OCR" in result.warning


def test_cv_upload_keeps_claim_unconfirmed_and_shows_escaped_source_evidence(tmp_path: Path) -> None:
    import json

    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app

    app = create_app(database_path=tmp_path / "evidence.db", storage_dir=tmp_path / "storage")
    client = TestClient(app)
    response = client.post(
        "/cvs/upload",
        files={"file": ("candidate.txt", b"Skills:\n- Cleaning <office>\n", "text/plain")},
        follow_redirects=True,
    )

    fact = next(row for row in app.state.repository.rows("facts") if row["value"] == "Cleaning <office>")
    evidence = json.loads(str(fact["evidence_json"]))
    assert response.status_code == 200
    assert "1 draft claims are ready for review" in response.text
    assert "candidate.txt" in response.text
    assert "Cleaning &lt;office&gt;" in response.text
    assert evidence["line"] == 2
    assert fact["confirmed"] == 0


def test_unreadable_upload_warning_is_visible_and_does_not_create_facts(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=tmp_path / "unreadable.db", storage_dir=tmp_path / "storage"))
    response = client.post(
        "/cvs/upload",
        files={"file": ("scan.txt", b"\n", "text/plain")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "No readable CV text was found" in response.text
    assert "did not guess or create candidate facts" in response.text
    assert client.app.state.repository.rows("facts") == []


def test_existing_candidate_facts_table_is_migrated_without_losing_rows(tmp_path: Path) -> None:
    import sqlite3

    from sampoagent.db.repository import Repository

    path = tmp_path / "legacy-facts.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE facts (id INTEGER PRIMARY KEY, type TEXT NOT NULL, value TEXT NOT NULL, provenance TEXT NOT NULL, source_id TEXT, confidence REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, rejected INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute("INSERT INTO facts(type,value,provenance,source_id,confidence,confirmed,rejected) VALUES ('skill','cleaning','USER_CONFIRMED','profile',1,1,0)")
    connection.commit()
    connection.close()

    repository = Repository(path)
    repository.initialize()

    assert repository.rows("facts")[0]["value"] == "cleaning"
    assert repository.rows("facts")[0]["evidence_json"] == ""


def test_confirmed_skill_recommends_related_roles_without_auto_targeting() -> None:
    from sampoagent.careers.recommendations import recommend_occupations

    recommendations = recommend_occupations(["forklift operation"], ignored=[])

    roles = {recommendation.title_en: recommendation for recommendation in recommendations}
    assert "Warehouse Worker" in roles
    assert "Logistics Worker" in roles
    assert "Terminal Worker" in roles
    assert roles["Warehouse Worker"].auto_target is False
    assert "forklift operation" in roles["Warehouse Worker"].supporting_facts


def test_builtin_recommendations_cover_multiple_sectors_and_finnish_skill_labels() -> None:
    from sampoagent.careers.recommendations import recommend_occupations

    cases = (
        ("kirjanpito", "Accountant"),
        ("ohjelmistokehitys", "Software Developer"),
        ("leivonta", "Baker"),
    )

    for skill, expected_title in cases:
        recommendations = recommend_occupations([skill], ignored=[])
        roles = {recommendation.title_en: recommendation for recommendation in recommendations}

        assert expected_title in roles
        assert roles[expected_title].supporting_facts == [skill]
        assert roles[expected_title].auto_target is False


def test_builtin_recommendations_do_not_count_case_variants_as_separate_skills() -> None:
    from sampoagent.careers.recommendations import recommend_occupations

    recommendations = recommend_occupations(["kirjanpito", "KIRJANPITO"], ignored=[])
    accountant = next(item for item in recommendations if item.title_en == "Accountant")

    assert accountant.supporting_facts == ["kirjanpito"]
    assert accountant.score == 75


def test_cv_file_text_reader_supports_txt_and_rejects_unknown_extensions(tmp_path: Path) -> None:
    from sampoagent.candidate.service import read_cv_file

    txt = tmp_path / "candidate.txt"
    txt.write_text("Skills: cleaning", encoding="utf-8")
    assert "cleaning" in read_cv_file(txt)
    unknown = tmp_path / "candidate.exe"
    unknown.write_bytes(b"not executable")
    try:
        read_cv_file(unknown)
    except ValueError as error:
        assert "Unsupported" in str(error)
    else:
        raise AssertionError("Expected unsupported file type to be rejected")
