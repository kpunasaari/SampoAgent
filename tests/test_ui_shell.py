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


def test_shell_uses_canonical_navigation_for_agent_and_cv_result() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    agent = client.get("/agent")
    generated = client.post(
        "/cvs/generate",
        data={"language": "en", "role_family": "universal", "filename_pattern": "", "company": ""},
    )

    assert agent.text.count('aria-current="page"') == 1
    assert 'aria-current="page" href="/agent">Agent' in agent.text
    assert generated.text.count('aria-current="page"') == 1
    assert 'aria-current="page" href="/cvs">CVs' in generated.text


def test_shell_uses_scrollable_sidebar_and_accessible_button_palette() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    page = client.get("/")

    assert "overflow-y:auto" in page.text
    assert "--primary:#6654d9" in page.text
    assert "#4b61c4" in page.text
    assert "#bf3658" in page.text
