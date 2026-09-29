"""Application-lifecycle supervisor for the local serial automation worker."""

from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from uuid import uuid4

from sampoagent.applications.worker import WorkerReport, run_worker_cycle
from sampoagent.db.repository import Repository


def _browser_factory(profile_dir: Path) -> object:
    from sampoagent.agents.playwright_adapter import PlaywrightBrowserAgent

    return PlaywrightBrowserAgent(profile_dir)


class AutopilotWorkerController:
    """Start/stop one local worker as application settings and grants change.

    Each cycle owns its SQLite connection and the visible browser is created and
    closed on the same background thread. This avoids sharing the UI repository
    connection across threads and keeps TestClient/app embeddings inert unless
    the controller is explicitly enabled.
    """

    def __init__(
        self,
        database_path: str | Path,
        storage_dir: str | Path = "application_data",
        *,
        browser_factory: Callable[[Path], object] = _browser_factory,
        cycle_runner: Callable[..., WorkerReport] = run_worker_cycle,
        interval_seconds: int = 30,
        discovery_interval_seconds: int = 3600,
    ) -> None:
        if interval_seconds < 5 or discovery_interval_seconds < 60:
            raise ValueError("Worker polling must be at least 5 seconds and discovery at least 1 minute")
        self.database_path = str(database_path)
        self.storage_dir = Path(storage_dir)
        self.browser_factory = browser_factory
        self.cycle_runner = cycle_runner
        self.interval_seconds = interval_seconds
        self.discovery_interval_seconds = discovery_interval_seconds
        self._lock = Lock()
        self._thread: Thread | None = None
        self._stop_event: Event | None = None
        self._state = "stopped"
        self._closed = False
        self._restart_requested = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and not (
                self._stop_event and self._stop_event.is_set()
            )

    def _eligible(self, repository: Repository) -> bool:
        mode = repository.setting("application_mode") or "review_everything"
        if (
            mode not in {"smart_approval", "autopilot"}
            or repository.setting("dry_run") != "false"
            or repository.setting("automation_paused") == "true"
        ):
            return False
        try:
            if int(repository.setting("daily_limit") or "0") <= 0:
                return False
        except ValueError:
            return False
        return mode != "autopilot" or repository.autopilot_authorized()

    def _authorized_now(self) -> bool:
        repository = Repository(self.database_path)
        try:
            return self._eligible(repository)
        except Exception:
            return False
        finally:
            repository.connection.close()

    def _launch_locked(self) -> None:
        stop_event = Event()
        worker = Thread(
            target=self._run,
            args=(stop_event,),
            name="sampoagent-application-worker",
            daemon=True,
        )
        self._stop_event = stop_event
        self._thread = worker
        self._state = "starting"
        worker.start()

    def _request_stop_locked(self) -> str:
        worker = self._thread
        stop_event = self._stop_event
        if worker is not None and worker.is_alive() and stop_event is not None:
            stop_event.set()
            self._state = "stopping"
            return "stopping"
        self._state = "stopped"
        return "stopped"

    def sync(self) -> str:
        """Reconcile worker state with the current grant, mode, and stop switch."""
        eligible = self._authorized_now()
        with self._lock:
            if self._closed:
                return "stopped"
            if not eligible:
                self._restart_requested = False
                return self._request_stop_locked()
            if self._thread is not None and self._thread.is_alive():
                if self._stop_event is not None and self._stop_event.is_set():
                    self._restart_requested = True
                    return "stopping"
                return self._state
            self._launch_locked()
            return "started"

    def _run(self, stop_event: Event) -> None:
        browser: object | None = None
        terminal_state = "stopped"
        try:
            browser = self.browser_factory(self.storage_dir / "browser-profile")
            last_discovery = 0.0
            while not stop_event.is_set():
                repository = Repository(self.database_path)
                try:
                    if not self._eligible(repository):
                        break
                    now = monotonic()
                    discover = now - last_discovery >= self.discovery_interval_seconds
                    self.cycle_runner(
                        repository,
                        browser,
                        discover=discover,
                        owner=f"app-{uuid4()}",
                        storage_dir=self.storage_dir,
                        next_run_seconds=self.interval_seconds,
                    )
                    if discover:
                        last_discovery = monotonic()
                    with self._lock:
                        if not stop_event.is_set():
                            self._state = "running"
                except Exception:
                    # Provider/browser error details can contain candidate data or
                    # secrets. The persistent worker status is intentionally generic.
                    with self._lock:
                        if not stop_event.is_set():
                            self._state = "error"
                finally:
                    repository.connection.close()
                if stop_event.wait(self.interval_seconds):
                    break
        except Exception:
            # Do not surface browser/provider exceptions that might embed a
            # candidate value or secret in the console or dashboard.
            terminal_state = "error"
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            should_restart = False
            with self._lock:
                self._state = terminal_state
                if self._thread is current_thread():
                    self._thread = None
                    self._stop_event = None
                should_restart = self._restart_requested and not self._closed
                self._restart_requested = False
            if should_restart and self._authorized_now():
                with self._lock:
                    if not self._closed and self._thread is None:
                        self._launch_locked()

    def close(self, *, timeout: float = 5.0) -> None:
        """Request a safe stop and wait briefly for the current cycle to unwind."""
        with self._lock:
            self._closed = True
            self._restart_requested = False
            self._request_stop_locked()
            worker = self._thread
        if worker is not None and worker is not current_thread():
            worker.join(timeout=max(0.0, timeout))
