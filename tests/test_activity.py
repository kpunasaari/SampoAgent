from pathlib import Path

from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.db.repository import Repository


PRIVATE_ACTIVITY_DETAILS = (
    "candidate@example.test +358401234567 access_token=synthetic-secret "
    "C:\\Users\\candidate\\cv.pdf confidential CV excerpt"
)

def test_activity_log_records_important_local_actions(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "agent.db")
    repository.initialize()
    repository.add_skill("cleaning")

    actions = repository.recent_activity()
    assert actions[0]["action"] == "skill_added"


def test_activity_log_never_persists_caller_supplied_details_or_actions(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "activity-redaction.db")
    repository.initialize()

    repository.log("skill_added", PRIVATE_ACTIVITY_DETAILS)
    repository.log(PRIVATE_ACTIVITY_DETAILS, PRIVATE_ACTIVITY_DETAILS)
    repository.connection.commit()

    rows = repository.connection.execute(
        "SELECT action, details FROM activity_log ORDER BY id DESC LIMIT 2"
    ).fetchall()
    assert rows[0]["action"] == "activity"
    assert rows[1]["action"] == "skill_added"
    assert all(row["details"] == "Activity details omitted to protect privacy." for row in rows)
    assert PRIVATE_ACTIVITY_DETAILS not in str([dict(row) for row in rows])


def test_dashboard_hides_legacy_raw_activity_details(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "legacy-activity.db", demo_data=True)
    app.state.repository.connection.execute(
        "INSERT INTO activity_log(action, details, created_at) VALUES (?, ?, ?)",
        ("candidate_record_added", PRIVATE_ACTIVITY_DETAILS, "2026-09-30T12:00:00+00:00"),
    )
    app.state.repository.connection.commit()

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert PRIVATE_ACTIVITY_DETAILS not in response.text
    assert "Activity details omitted to protect privacy." in response.text
    assert app.state.repository.recent_activity(1)[0]["details"] == "Activity details omitted to protect privacy."
    assert app.state.repository.rows("activity_log")[0]["details"] == "Activity details omitted to protect privacy."


def test_historical_activity_cleanup_requires_local_token_and_exact_confirmation(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "activity-cleanup-guard.db")
    repository = app.state.repository
    repository.connection.execute(
        "INSERT INTO activity_log(action, details, created_at) VALUES (?, ?, ?)",
        ("skill_added", PRIVATE_ACTIVITY_DETAILS, "2026-09-29T12:00:00+00:00"),
    )
    repository.connection.commit()
    client = TestClient(app, follow_redirects=False)

    response = client.post(
        "/settings/privacy/redact-activity",
        data={"csrf_token": app.state.local_action_token, "confirmation": "REDACT"},
    )

    assert response.status_code == 403
    stored = repository.connection.execute("SELECT details FROM activity_log").fetchone()[0]
    assert stored == PRIVATE_ACTIVITY_DETAILS

    settings = client.get("/settings")
    assert "1 older activity entry contains stored detail text" in settings.text
    assert "Type REDACT to permanently replace older detail text" in settings.text
    response = client.post(
        "/settings/privacy/redact-activity",
        data={"csrf_token": app.state.local_action_token, "confirmation": "yes"},
    )

    assert response.status_code == 303
    stored = repository.connection.execute("SELECT details FROM activity_log").fetchone()[0]
    assert stored == PRIVATE_ACTIVITY_DETAILS


def test_historical_activity_cleanup_preserves_event_and_timestamp(tmp_path: Path) -> None:
    app = create_app(database_path=tmp_path / "activity-cleanup.db")
    repository = app.state.repository
    created_at = "2026-09-29T12:00:00+00:00"
    repository.connection.execute(
        "INSERT INTO activity_log(action, details, created_at) VALUES (?, ?, ?)",
        ("skill_added", PRIVATE_ACTIVITY_DETAILS, created_at),
    )
    repository.connection.commit()
    client = TestClient(app, follow_redirects=False)
    client.get("/settings")

    response = client.post(
        "/settings/privacy/redact-activity",
        data={"csrf_token": app.state.local_action_token, "confirmation": "REDACT"},
    )

    assert response.status_code == 303
    row = repository.connection.execute(
        "SELECT action, details, created_at FROM activity_log WHERE action='skill_added'"
    ).fetchone()
    assert tuple(row) == ("skill_added", "Activity details omitted to protect privacy.", created_at)
    audit = repository.connection.execute(
        "SELECT action, details FROM activity_log WHERE action='activity_details_redacted'"
    ).fetchone()
    assert tuple(audit) == ("activity_details_redacted", "Activity details omitted to protect privacy.")
    assert PRIVATE_ACTIVITY_DETAILS not in response.headers["location"]
