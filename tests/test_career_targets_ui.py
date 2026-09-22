def test_recommended_occupation_requires_explicit_target_activation() -> None:
    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:")
    client = TestClient(app)
    response = client.post(
        "/careers/targets",
        data={"title_en": "Warehouse Worker", "title_fi": "Varastotyöntekijä"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "My target occupations" in response.text
    assert "Warehouse Worker / Varastotyöntekijä" in response.text
    assert app.state.repository.target_occupations()[0]["enabled"] == 1


def test_target_occupation_can_be_deactivated_without_deleting_it() -> None:
    from fastapi.testclient import TestClient

    from sampoagent.app.main import create_app

    app = create_app(database_path=":memory:")
    repository = app.state.repository
    target_id = repository.add_target_occupation("Cleaner", "Siivooja")
    client = TestClient(app)

    response = client.post(f"/careers/targets/{target_id}/toggle", follow_redirects=True)

    assert response.status_code == 200
    assert "Inactive" in response.text
    assert repository.target_occupations()[0]["enabled"] == 0
