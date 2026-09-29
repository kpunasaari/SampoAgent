def test_cv_page_can_generate_confirmed_fact_only_pdf() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:", demo_data=True))
    response = client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"}, follow_redirects=True)

    assert response.status_code == 200
    assert "Generated" in response.text


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
