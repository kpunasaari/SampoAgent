from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


def test_shell_marks_the_current_navigation_item() -> None:
    client = TestClient(create_app(database_path=":memory:"))

    page = client.get("/profile")

    assert 'class="nav-link active"' in page.text
    assert 'aria-current="page" href="/profile">Profile' in page.text


def test_primary_navigation_is_shorter_and_secondary_pages_stay_accessible():
    client = TestClient(create_app(database_path=":memory:"))
    page = client.get("/")
    primary_links = page.text.split("<details class='nav-more'", 1)[0].count("class=\"nav-link")
    assert primary_links == 8
    assert "More tools" in page.text
    agent = client.get("/agent")
    assert '<details class=\'nav-more\' open>' in agent.text
    assert 'aria-current="page" href="/agent">Agent' in agent.text
    assert agent.text.count('aria-current="page"') == 1


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
    assert "--primary:#5141b6" in page.text
    assert "#392c89" in page.text
    assert "#98233f" in page.text


def test_primary_and_danger_button_palettes_meet_normal_text_contrast():
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def contrast(first: str, second: str) -> float:
        high, low = sorted((luminance(first), luminance(second)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    assert min(contrast("#ffffff", color) for color in ("#5141b6", "#392c89", "#98233f", "#68152b")) >= 4.5
