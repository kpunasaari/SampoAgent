def test_cv_page_can_generate_confirmed_fact_only_pdf() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    response = client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"}, follow_redirects=True)

    assert response.status_code == 200
    assert "Generated" in response.text
    assert "CV text check:" in response.text
    assert "not an ATS compatibility or hiring-success score" in response.text


def test_cv_page_uses_safe_filename_pattern_and_exposes_download() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    response = client.post(
        "/cvs/generate",
        data={
            "language": "en",
            "role_family": "warehouse_logistics",
            "filename_pattern": "{first}_{last}_{role}_{language}.pdf",
            "company": "",
        },
        follow_redirects=True,
    )

    import re
    generated = re.search(r"Aino_Example_warehouse_logistics_en_[a-f0-9]{8}\.pdf", response.text)
    assert generated
    assert f"/cvs/generated/{generated.group(0)}" in response.text

    download = client.get(f"/cvs/generated/{generated.group(0)}")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")


def test_cv_archive_explains_text_check_without_claiming_ats_or_hiring_success(tmp_path) -> None:
    from hashlib import sha256
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:", demo_data=True, storage_dir=tmp_path)
    path = tmp_path / "synthetic-cv.pdf"
    path.write_bytes(b"synthetic PDF placeholder for archive display")
    app.state.repository.archive_cv(
        path=str(path), checksum=sha256(path.read_bytes()).hexdigest(), language="en",
        role_family="warehouse_logistics", source_job_id=1, fit_score=91,
        ats_score=82, strategy="generated",
    )
    untested_path = tmp_path / "uploaded-cv.pdf"
    untested_path.write_bytes(b"synthetic uploaded CV placeholder")
    app.state.repository.archive_cv(
        path=str(untested_path), checksum=sha256(untested_path.read_bytes()).hexdigest(),
        language="en", role_family="universal", source_job_id=None, fit_score=0,
        strategy="uploaded",
    )
    checked_zero_path = tmp_path / "checked-zero-cv.pdf"
    checked_zero_path.write_bytes(b"synthetic generated CV with no expected text")
    app.state.repository.archive_cv(
        path=str(checked_zero_path), checksum=sha256(checked_zero_path.read_bytes()).hexdigest(),
        language="en", role_family="warehouse_logistics", source_job_id=1,
        fit_score=91, ats_score=0, strategy="generated",
    )

    response = TestClient(app).get("/cvs")

    assert response.status_code == 200
    assert "<th>CV text check</th>" in response.text
    assert "presence of expected confirmed candidate text" in response.text
    assert "not an ATS compatibility or hiring-success score" in response.text
    assert "<td>82%</td>" in response.text
    assert "<td>0%</td><td>Not checked</td>" in response.text
    assert "<td>91%</td><td>0%</td>" in response.text
