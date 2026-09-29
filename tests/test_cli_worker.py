import sys

import uvicorn

from sampoagent import cli
from sampoagent.applications.worker_controller import AutopilotWorkerController


def test_run_cli_attaches_lifecycle_managed_worker_and_stays_local(monkeypatch, tmp_path):
    database = tmp_path / "cli.db"
    storage = tmp_path / "storage"
    launched = {}
    monkeypatch.setattr(
        sys,
        "argv",
        ["sampoagent", "run", "--database", str(database), "--storage-dir", str(storage), "--port", "8790"],
    )
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launched.update(app=app, **kwargs))

    cli.main()

    assert launched["host"] == "127.0.0.1"
    assert launched["port"] == 8790
    assert isinstance(launched["app"].state.automation_worker, AutopilotWorkerController)
    assert launched["app"].state.automation_worker.storage_dir == storage
    launched["app"].state.automation_worker.close()
    launched["app"].state.repository.connection.close()
