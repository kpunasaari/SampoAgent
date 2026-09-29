from threading import Event, Thread
from concurrent.futures import ThreadPoolExecutor

from sampoagent.agents.browser import FormInspection, SubmissionResult
from sampoagent.applications.field_resolver import FormField
from sampoagent.applications.runner import process_application
from sampoagent.applications.worker import run_worker_cycle
from sampoagent.db.repository import Repository


class FakeBrowser:
    configured = True

    def __init__(self):
        self.opened = []
        self.submitted = 0
        self.values = {}

    def open(self, url):
        self.opened.append(url)

    def inspect_form(self):
        return FormInspection((FormField("name", "Full name", True), FormField("email", "Email address", True, "email")), signature="stable", final_url="https://careers.northstar-logistics.fi/apply/warehouse", values=dict(self.values))

    def fill(self, field, value):
        self.values[field] = value

    def upload(self, field, path):
        return None

    def validation_errors(self):
        return ()

    def submit(self, url, *, expected_signature=None, expected_uploads=None, pre_click_check=None):
        if pre_click_check is not None and not pre_click_check():
            return SubmissionResult(False, True, "Authorization changed before the click", final_url=url)
        self.submitted += 1
        return SubmissionResult(True, False, "Application received", "https://careers.northstar-logistics.fi/confirmation")


def _repository(tmp_path):
    repository = Repository(tmp_path / "worker.db")
    repository.initialize()
    repository.load_demo()
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.connection.execute("UPDATE jobs SET application_url=? WHERE id=1", ("https://careers.northstar-logistics.fi/apply/warehouse",))
    repository.connection.commit()
    repository.mark_job_user_reviewed(1, reviewed_current=True)
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "3")
    repository.set_setting("dry_run", "false")
    repository.grant_autopilot()
    repository.queue_application(1, language="en", cv_path=None)
    return repository


def test_worker_processes_queue_under_a_single_database_lease(tmp_path):
    repository = _repository(tmp_path)
    browser = FakeBrowser()

    report = run_worker_cycle(repository, browser, discover=False, owner="worker-a")

    assert report.status == "completed"
    assert report.results == ((1, "APPLIED"),)
    assert browser.submitted == 1
    assert repository.worker_status()["status"] == "completed"
    assert repository.acquire_worker_lease("worker-b") is True
    repository.release_worker_lease("worker-b")
    repository.connection.close()


def test_second_worker_cannot_start_a_browser_cycle_while_lease_is_live(tmp_path):
    repository = _repository(tmp_path)
    assert repository.acquire_worker_lease("worker-a")
    browser = FakeBrowser()

    report = run_worker_cycle(repository, browser, discover=False, owner="worker-b")

    assert report.status == "busy"
    assert browser.opened == []
    repository.release_worker_lease("worker-a")
    repository.connection.close()


def test_two_repository_connections_cannot_claim_the_same_preparation(tmp_path):
    repository = _repository(tmp_path)
    other = Repository(tmp_path / "worker.db")
    other.initialize()
    application_id = 1

    first = repository.claim_application_preparation(application_id, owner_token="worker-a")
    second = other.claim_application_preparation(application_id, owner_token="worker-b")

    assert first is True
    assert second is False
    assert repository.application(application_id)["queue_state"] == "PREPARING"
    repository.connection.close()
    other.connection.close()


def test_unique_job_claim_prevents_duplicate_queue_insert_across_connections(tmp_path):
    database_path = tmp_path / "worker.db"
    repository = Repository(database_path)
    repository.initialize()
    repository.load_demo()
    other = Repository(tmp_path / "worker.db")
    other.initialize()
    barrier = Event()

    def enqueue(connection):
        barrier.wait(timeout=3)
        try:
            return connection.queue_application(1, language="en", cv_path=None)
        except ValueError as error:
            assert "already exists" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(enqueue, repository)
        second = executor.submit(enqueue, other)
        barrier.set()
        results = (first.result(timeout=5), second.result(timeout=5))
    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1
    assert repository.count("applications") == 1
    repository.connection.close()
    other.connection.close()


def test_daily_submission_slot_is_reserved_once_across_two_process_connections(tmp_path):
    repository = _repository(tmp_path)
    other = Repository(tmp_path / "worker.db")
    other.initialize()
    second_id = repository.queue_application(2, language="en", cv_path=None)
    assert repository.claim_application_preparation(1, owner_token="worker-a")
    assert other.claim_application_preparation(second_id, owner_token="worker-b")
    barrier = Event()

    def reserve(connection, application_id, token):
        barrier.wait(timeout=3)
        return connection.reserve_submission_attempt(
            application_id,
            daily_limit=1,
            package_hash=f"package-{application_id}",
            preparation_token=token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reserve, repository, 1, "worker-a")
        second = executor.submit(reserve, other, second_id, "worker-b")
        barrier.set()
        attempt_ids = (first.result(timeout=5), second.result(timeout=5))

    assert sum(attempt_id is not None for attempt_id in attempt_ids) == 1
    assert repository.submissions_reserved_today() == 1
    repository.connection.close()
    other.connection.close()


def test_second_runner_cannot_open_or_submit_an_application_claimed_by_another_process(tmp_path):
    repository = _repository(tmp_path)
    other = Repository(tmp_path / "worker.db")
    other.initialize()
    entered = Event()
    release = Event()
    first_results = []

    class BlockingBrowser(FakeBrowser):
        def open(self, url):
            super().open(url)
            entered.set()
            assert release.wait(timeout=3)

    first_browser = BlockingBrowser()
    first = Thread(target=lambda: first_results.append(process_application(repository, 1, first_browser)))
    first.start()
    assert entered.wait(timeout=3)
    second_browser = FakeBrowser()

    second_result = process_application(other, 1, second_browser)

    release.set()
    first.join(timeout=3)
    assert not first.is_alive()
    assert second_result == "NOT_READY"
    assert second_browser.opened == []
    assert first_results == ["APPLIED"]
    assert first_browser.submitted == 1
    assert repository.connection.execute("SELECT COUNT(*) FROM application_attempts").fetchone()[0] == 1
    repository.connection.close()
    other.connection.close()


def test_stale_preparation_recovery_never_requeues_a_submission_attempt(tmp_path):
    repository = _repository(tmp_path)
    application_id = 1
    assert repository.claim_application_preparation(application_id, owner_token="worker-a")
    submitting_id = repository.queue_application(2, language="en", cv_path=None)
    assert repository.claim_application_preparation(submitting_id, owner_token="worker-b")
    attempt_id = repository.reserve_submission_attempt(
        submitting_id,
        daily_limit=5,
        package_hash="second-package",
        preparation_token="worker-b",
    )
    assert attempt_id is not None

    recovered = repository.recover_preparing_applications()

    assert recovered == 1
    assert repository.application(application_id)["queue_state"] == "READY"
    assert repository.ready_applications()[0]["id"] == application_id
    assert repository.application(submitting_id)["queue_state"] == "SUBMITTING"
    assert repository.recover_interrupted_submissions() == 1
    assert repository.application(submitting_id)["queue_state"] == "DO_NOT_RETRY"
    assert repository.connection.execute("SELECT state FROM application_attempts WHERE id=?", (attempt_id,)).fetchone()[0] == "UNKNOWN"
    repository.connection.close()


def test_worker_recovers_abandoned_preparation_before_processing_queue(tmp_path):
    repository = _repository(tmp_path)
    assert repository.claim_application_preparation(1, owner_token="crashed-worker")
    browser = FakeBrowser()

    report = run_worker_cycle(repository, browser, discover=False, owner="recovery-worker")

    assert report.recovered_preparing == 1
    assert report.results == ((1, "APPLIED"),)
    assert browser.submitted == 1
    repository.connection.close()


def test_worker_status_keeps_heartbeat_and_watch_next_run_for_dashboard(tmp_path):
    repository = _repository(tmp_path)

    report = run_worker_cycle(repository, FakeBrowser(), discover=False, owner="worker-ui", next_run_seconds=30)
    status = repository.worker_status()

    assert report.status == "completed"
    assert status["status"] == "completed"
    assert status["last_heartbeat"]
    assert status["next_run_at"]
    assert "1 application result" in status["last_result"]
    repository.connection.close()


def test_full_autopilot_holds_captcha_job_and_continues_with_other_eligible_jobs(tmp_path):
    repository = _repository(tmp_path)
    # The second demo job is a customer-service role, so add it to the user's
    # explicit target scope and re-authorize before the worker runs.
    repository.add_target_occupation("Customer Service Representative", "Asiakaspalvelija")
    repository.grant_autopilot()
    repository.connection.execute(
        "UPDATE jobs SET application_url=? WHERE id=2",
        ("https://careers.cleaning.example.fi/apply/cleaner",),
    )
    repository.connection.commit()
    repository.mark_job_user_reviewed(2, reviewed_current=True)
    second_id = repository.queue_application(2, language="en", cv_path=None)

    class ChallengeThenNormalBrowser(FakeBrowser):
        current_url = ""

        def open(self, url):
            super().open(url)
            self.current_url = url

        def inspect_form(self):
            if "warehouse" in self.current_url:
                return FormInspection(
                    (FormField("name", "Full name", True),),
                    captcha_detected=True,
                    signature="challenge",
                    final_url=self.current_url,
                )
            fields = (FormField("name", "Full name", True), FormField("email", "Email address", True, "email"))
            return FormInspection(fields, signature="stable", final_url=self.current_url, values=dict(self.values))

        def submit(self, url, *, expected_signature=None, expected_uploads=None, pre_click_check=None):
            if pre_click_check is not None and not pre_click_check():
                return SubmissionResult(False, True, "Authorization changed before the click", final_url=url)
            self.submitted += 1
            return SubmissionResult(True, False, "Application received", "https://careers.cleaning.example.fi/confirmation")

    browser = ChallengeThenNormalBrowser()

    report = run_worker_cycle(repository, browser, discover=False, owner="worker-a")

    assert report.results == ((1, "CAPTCHA_HOLD"), (second_id, "APPLIED"))
    assert repository.captcha_tasks()[0]["application_id"] == 1
    assert repository.application(second_id)["status"] == "APPLIED"
    assert browser.submitted == 1
    repository.connection.close()
