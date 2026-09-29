from urllib.parse import parse_qs, urlsplit
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from sampoagent.app.main import create_app
from sampoagent.applications.worker import run_worker_cycle
from sampoagent.db.repository import Repository
from sampoagent.jobs.adapters import SourceAdapterError
from sampoagent.jobs.runner import run_discovery


PRIVATE_ERROR = (
    "Rejected candidate@example.test phone +358 40 123 4567; "
    "access_token=synthetic-secret C:\\Users\\candidate\\cv.pdf"
)


def test_candidate_record_form_does_not_echo_untrusted_exception_details(tmp_path):
    app = create_app(database_path=tmp_path / "redaction.db", demo_data=True)

    def fail_record_write(*args, **kwargs):
        raise ValueError(PRIVATE_ERROR)

    app.state.repository.add_candidate_record = fail_record_write

    with TestClient(app) as client:
        response = client.post(
            "/profile/records",
            data={"record_type": "experience", "title": "Synthetic role"},
            follow_redirects=False,
        )

    notice = parse_qs(urlsplit(response.headers["location"]).query).get("notice", [""])[0]
    assert response.status_code == 303
    assert notice == "Could not save this profile entry. Check the fields and try again."
    assert "candidate@example.test" not in response.headers["location"]
    assert "+358" not in response.headers["location"]
    assert "synthetic-secret" not in response.headers["location"]
    assert "C:" not in response.headers["location"]


def test_worker_failure_persists_only_a_generic_status(tmp_path, monkeypatch):
    database_path = tmp_path / "worker-redaction.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.load_demo()
    assert repository.acquire_worker_lease("synthetic-worker")

    class FailingBrowser:
        configured = True

    def fail_queue(*args, **kwargs):
        raise RuntimeError(PRIVATE_ERROR)

    monkeypatch.setattr("sampoagent.applications.worker.enqueue_eligible_applications", fail_queue)

    try:
        try:
            run_worker_cycle(
                repository,
                FailingBrowser(),
                discover=False,
                owner="synthetic-worker",
            )
        except Exception:
            pass

        state = repository.worker_status()
        assert state["status"] == "failed"
        assert state["last_result"] == "Worker cycle failed. No error details were stored."
        assert PRIVATE_ERROR not in str(state)
    finally:
        repository.release_worker_lease("synthetic-worker")
        repository.connection.close()


def test_cli_worker_failure_does_not_emit_exception_text_or_chain(tmp_path, monkeypatch):
    import sampoagent.cli as cli

    class FakeBrowser:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    def fail_cycle(*_args, **_kwargs):
        raise RuntimeError(PRIVATE_ERROR)

    monkeypatch.setattr(cli, "PlaywrightBrowserAgent", FakeBrowser)
    monkeypatch.setattr(cli, "run_worker_cycle", fail_cycle)
    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "automate", "--database", str(tmp_path / "cli-redaction.db"),
        "--storage-dir", str(tmp_path / "cli-redaction-storage"), "--no-discover",
    ])

    with pytest.raises(SystemExit) as raised:
        cli.main()

    assert str(raised.value) == "Automation worker cycle failed. Error details were not written to the console."
    assert raised.value.__cause__ is None
    assert "candidate@example.test" not in str(raised.value)


def test_unhandled_web_error_returns_generic_response_and_logs_only_error_type(tmp_path, caplog):
    app = create_app(database_path=tmp_path / "web-redaction.db", demo_data=True)

    def fail_usage_summary():
        raise RuntimeError(PRIVATE_ERROR)

    app.state.repository.ai_usage_summary = fail_usage_summary

    with caplog.at_level(logging.ERROR, logger="sampoagent.app"):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/agent")

    assert response.status_code == 500
    assert "request could not be completed" in response.text.casefold()
    assert PRIVATE_ERROR not in response.text
    assert "RuntimeError" in caplog.text
    assert "candidate@example.test" not in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_discovery_persists_generic_source_failure_not_provider_exception(tmp_path):
    repository = Repository(tmp_path / "source-redaction.db")
    repository.initialize()
    repository.save_profile("Synthetic Candidate", "en")
    repository.add_target_occupation("Cleaner", "Siivooja")
    source_id = repository.add_source(
        name="Synthetic feed", url="https://feed.example.test/jobs", country="Finland",
        source_type="feed", capability="RSS/Atom feed",
    )

    class FailingFeed:
        def search(self, *_args, **_kwargs):
            raise SourceAdapterError(PRIVATE_ERROR)

    report = run_discovery(repository, rss_adapter=FailingFeed())
    result = next(
        item for item in repository.discovery_source_results(report.run_id)
        if item["source_id"] == source_id
    )

    assert result["status"] == "failed"
    assert result["message"] == "Source check failed. No credentials, URLs, or request details were stored."
    assert PRIVATE_ERROR not in str(result)
    repository.connection.close()


def test_local_server_disables_access_logs_that_could_include_oauth_query_codes(tmp_path, monkeypatch):
    import sampoagent.cli as cli

    captured = {}

    def fake_uvicorn_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(sys, "argv", [
        "sampoagent", "run", "--database", str(tmp_path / "no-access-log.db"),
        "--storage-dir", str(tmp_path / "no-access-log-storage"),
    ])

    cli.main()

    assert captured["access_log"] is False
    captured["app"].state.repository.connection.close()
