from threading import Event

from sampoagent.applications.worker_controller import AutopilotWorkerController
from sampoagent.db.repository import Repository


class FakeBrowser:
    def __init__(self, profile_dir):
        self.profile_dir = profile_dir
        self.closed = False

    def close(self):
        self.closed = True


def _authorized_database(path):
    repository = Repository(path)
    repository.initialize()
    repository.load_demo()
    repository.add_target_occupation("Warehouse Worker", "Varastotyöntekijä")
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "2")
    repository.set_setting("dry_run", "false")
    repository.grant_autopilot()
    repository.connection.close()


def test_controller_starts_a_worker_for_a_valid_autopilot_grant(tmp_path):
    database = tmp_path / "autopilot.db"
    _authorized_database(database)
    cycle_started = Event()
    created_browsers = []

    def make_browser(profile_dir):
        browser = FakeBrowser(profile_dir)
        created_browsers.append(browser)
        return browser

    def run_cycle(repository, browser, **kwargs):
        cycle_started.set()
        return None

    controller = AutopilotWorkerController(
        database,
        tmp_path / "application_data",
        browser_factory=make_browser,
        cycle_runner=run_cycle,
        interval_seconds=3600,
    )

    assert controller.sync() == "started"
    assert cycle_started.wait(2)
    controller.close(timeout=2)

    assert controller.state == "stopped"
    assert len(created_browsers) == 1
    assert created_browsers[0].closed


def test_controller_does_not_start_without_grant_or_when_dry_run_is_on(tmp_path):
    database = tmp_path / "not-authorized.db"
    repository = Repository(database)
    repository.initialize()
    repository.load_demo()
    repository.set_setting("application_mode", "autopilot")
    repository.set_setting("daily_limit", "2")
    repository.set_setting("dry_run", "true")
    repository.connection.close()
    cycle_started = Event()
    controller = AutopilotWorkerController(
        database,
        tmp_path / "application_data",
        browser_factory=FakeBrowser,
        cycle_runner=lambda *args, **kwargs: cycle_started.set(),
    )

    assert controller.sync() == "stopped"

    controller.close(timeout=1)
    assert not cycle_started.is_set()
    assert controller.state == "stopped"


def test_controller_stops_after_grant_revocation(tmp_path):
    database = tmp_path / "revoked.db"
    _authorized_database(database)
    cycle_started = Event()
    release_cycle = Event()

    def run_cycle(repository, browser, **kwargs):
        cycle_started.set()
        release_cycle.wait(2)

    controller = AutopilotWorkerController(
        database,
        tmp_path / "application_data",
        browser_factory=FakeBrowser,
        cycle_runner=run_cycle,
        interval_seconds=3600,
    )

    assert controller.sync() == "started"
    assert cycle_started.wait(2)
    repository = Repository(database)
    repository.revoke_autopilot("Test revocation")
    repository.connection.commit()
    repository.connection.close()
    assert controller.sync() == "stopping"
    release_cycle.set()
    controller.close(timeout=2)

    assert controller.state == "stopped"


def test_controller_does_not_start_while_emergency_stop_is_active(tmp_path):
    database = tmp_path / "paused.db"
    _authorized_database(database)
    repository = Repository(database)
    repository.set_setting("automation_paused", "true")
    repository.connection.close()
    cycle_started = Event()
    controller = AutopilotWorkerController(
        database,
        tmp_path / "application_data",
        browser_factory=FakeBrowser,
        cycle_runner=lambda *args, **kwargs: cycle_started.set(),
    )

    assert controller.sync() == "stopped"
    controller.close(timeout=1)

    assert not cycle_started.is_set()


def test_controller_recovers_browser_after_temporary_factory_and_cycle_failures(tmp_path):
    database = tmp_path / "recovering.db"
    _authorized_database(database)
    factory_failed = Event()
    cycle_failed = Event()
    cycle_recovered = Event()
    created_browsers = []
    factory_attempts = 0
    cycle_attempts = 0

    def make_browser(profile_dir):
        nonlocal factory_attempts
        factory_attempts += 1
        if factory_attempts == 1:
            factory_failed.set()
            raise RuntimeError("private browser diagnostic must not escape")
        browser = FakeBrowser(profile_dir)
        created_browsers.append(browser)
        return browser

    def run_cycle(repository, browser, **kwargs):
        nonlocal cycle_attempts
        cycle_attempts += 1
        if cycle_attempts == 1:
            cycle_failed.set()
            raise RuntimeError("private provider diagnostic must not escape")
        cycle_recovered.set()

    controller = AutopilotWorkerController(
        database,
        tmp_path / "application_data",
        browser_factory=make_browser,
        cycle_runner=run_cycle,
        interval_seconds=5,
    )

    assert controller.sync() == "started"
    assert factory_failed.wait(2)
    assert controller.is_running, "temporary browser failure should keep the authorized worker alive"
    assert cycle_failed.wait(7)
    assert cycle_recovered.wait(12), "the worker should create a fresh browser and resume after a failed cycle"
    controller.close(timeout=2)

    assert factory_attempts >= 3
    assert cycle_attempts >= 2
    assert len(created_browsers) >= 2
    assert created_browsers[0].closed
    assert controller.state == "stopped"
