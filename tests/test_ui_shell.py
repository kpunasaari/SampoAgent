from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_shell_marks_the_current_navigation_item() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    page = client.get("/profile")

    assert 'class="nav-link active"' in page.text
    assert 'aria-current="page" href="/profile">Profile' in page.text


def test_shell_exposes_responsive_and_accessible_design_tokens() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    page = client.get("/")

    assert "--surface-0" in page.text
    assert "@media (max-width: 760px)" in page.text
    assert "Skip to content" in page.text


def test_dashboard_shows_safe_mode_and_actionable_metric_cards() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    page = client.get("/")

    assert "Dry Run is on" in page.text
    assert 'class="metric-grid"' in page.text
    assert "Jobs found" in page.text
    assert "What to do next" in page.text
