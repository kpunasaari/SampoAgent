from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_learning_summary_setting_is_opt_in_and_can_be_revoked(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "learning-settings.db"), follow_redirects=False)

    page = client.get("/settings")
    assert page.status_code == 200
    assert "sharing is disabled" in page.text
    assert "Codex learning summary" in page.text

    enabled = client.post("/settings/codex-learning", data={"enabled": "yes"})
    assert enabled.status_code == 303
    assert client.app.state.repository.setting("codex_learning_summary_enabled") == "true"

    revoked = client.post("/settings/codex-learning", data={})
    assert revoked.status_code == 303
    assert client.app.state.repository.setting("codex_learning_summary_enabled") == "false"


def test_learning_summary_setting_rejects_unrecognized_values(tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "learning-invalid-setting.db"), follow_redirects=False)
    client.app.state.repository.set_setting("codex_learning_summary_enabled", "true")

    response = client.post("/settings/codex-learning", data={"enabled": "maybe"})

    assert response.status_code == 303
    assert client.app.state.repository.setting("codex_learning_summary_enabled") == "true"
