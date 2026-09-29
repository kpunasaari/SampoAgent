from fastapi.testclient import TestClient

from sampoagent.app.main import create_app


class LifecycleProbe:
    state = "stopped"
    is_running = False

    def __init__(self):
        self.sync_count = 0
        self.close_count = 0

    def sync(self):
        self.sync_count += 1

    def close(self, *, timeout=5.0):
        self.close_count += 1


def test_web_app_syncs_worker_on_startup_settings_save_and_shutdown(tmp_path):
    worker = LifecycleProbe()
    app = create_app(
        database_path=tmp_path / "managed-worker.db",
        demo_data=True,
        storage_dir=tmp_path / "storage",
        automation_worker=worker,
    )

    with TestClient(app) as client:
        assert worker.sync_count == 1
        response = client.post(
            "/settings",
            data={
                "application_mode": "autopilot",
                "daily_limit": "2",
                "ai_usage_mode": "minimal",
                "dry_run": "false",
                "autopilot_ack": "yes",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert worker.sync_count == 2
        pause_response = client.post(
            "/automation/pause",
            data={"paused": "true"},
            follow_redirects=False,
        )
        assert pause_response.status_code == 303
        assert worker.sync_count == 3

    assert worker.close_count == 1
