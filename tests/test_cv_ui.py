def test_cv_page_can_generate_confirmed_fact_only_pdf() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
    response = client.post("/cvs/generate", data={"language": "en", "role_family": "warehouse_logistics"}, follow_redirects=True)

    assert response.status_code == 200
    assert "Generated" in response.text


def test_cv_page_uses_safe_filename_pattern_and_exposes_download() -> None:
    from fastapi.testclient import TestClient
    from sampoagent.app.main import create_app

    client = TestClient(create_app(database_path=":memory:"))
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

    assert "Aino_Example_warehouse_logistics_en.pdf" in response.text
    assert "/cvs/generated/Aino_Example_warehouse_logistics_en.pdf" in response.text

    download = client.get("/cvs/generated/Aino_Example_warehouse_logistics_en.pdf")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
