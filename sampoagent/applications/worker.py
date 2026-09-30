"""Single-process polling worker for local job-application automation."""

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from uuid import uuid4

from sampoagent.applications.packages import enqueue_eligible_applications
from sampoagent.applications.runner import process_application
from sampoagent.jobs.runner import run_discovery
from sampoagent.integrations.email_send import EmailSendResult, send_email_message
from sampoagent.integrations.outbox import EmailOutboxError, prepare_autopilot_email_application, send_approved_application_email


@dataclass(frozen=True)
class WorkerReport:
    status: str
    results: tuple[tuple[int, str], ...] = ()
    discovery_run_id: int | None = None
    recovered_unknown: int = 0
    queued_count: int = 0
    recovered_preparing: int = 0


def run_worker_cycle(repository: object, browser: object, *, discover: bool = True, owner: str | None = None, storage_dir: Path = Path("application_data"), next_run_seconds: int | None = None, email_sender: Callable[..., EmailSendResult] | None = None) -> WorkerReport:
    """Discover, then process ready items while holding the SQLite worker lease."""
    from sampoagent.app.setup_flow import pending

    if pending(repository):
        return WorkerReport("setup_required")
    lease_owner = owner or str(uuid4())
    if not repository.acquire_worker_lease(lease_owner):
        return WorkerReport("busy")
    discovery_run_id = None
    report: WorkerReport | None = None
    try:
        recovered_preparing = repository.recover_preparing_applications()
        recovered = repository.recover_interrupted_submissions()
        repository.recover_interrupted_email_sends()
        if repository.setting("automation_paused") == "true":
            report = WorkerReport("paused", recovered_unknown=recovered, recovered_preparing=recovered_preparing)
            return report
        if discover:
            discovery_run_id = run_discovery(repository).run_id
        queued_count = enqueue_eligible_applications(repository, storage_dir)
        if repository.setting("dry_run") != "false":
            report = WorkerReport("dry_run", discovery_run_id=discovery_run_id, recovered_unknown=recovered, queued_count=queued_count, recovered_preparing=recovered_preparing)
            return report
        outcomes = []
        if repository.setting("application_mode") == "autopilot":
            for application in repository.ready_applications():
                if repository.setting("automation_paused") == "true" or not repository.renew_worker_lease(lease_owner):
                    break
                application_id = int(application["id"])
                route = prepare_autopilot_email_application(repository, storage_dir, application_id)
                if route == "HELD":
                    outcomes.append((application_id, "EMAIL_HELD"))
        if repository.email_send_autopilot_authorized():
            for outbox in repository.ready_email_outbox_items():
                if repository.setting("automation_paused") == "true" or not repository.renew_worker_lease(lease_owner):
                    break
                application_id = int(outbox["application_id"])
                try:
                    result = send_approved_application_email(
                        repository,
                        storage_dir,
                        application_id,
                        package_hash=str(outbox["package_hash"]),
                        confirmed=False,
                        sender=email_sender or send_email_message,
                        autopilot=True,
                    )
                except EmailOutboxError:
                    # Invalidated packages and exhausted quotas stay unsent;
                    # the application screen explains the required review.
                    continue
                outcomes.append((application_id, f"EMAIL_{result.state}"))
        for application in repository.ready_applications():
            if repository.setting("automation_paused") == "true":
                break
            if not repository.renew_worker_lease(lease_owner):
                break
            outcome = process_application(repository, int(application["id"]), browser)
            outcomes.append((int(application["id"]), outcome))
        report = WorkerReport("completed", tuple(outcomes), discovery_run_id, recovered, queued_count, recovered_preparing)
        return report
    except Exception:
        repository.finish_worker_cycle(
            lease_owner,
            status="failed",
            last_result="Worker cycle failed. No error details were stored.",
        )
        raise
    finally:
        if report is not None:
            repository.finish_worker_cycle(
                lease_owner,
                status=report.status,
                last_result=(
                    f"{len(report.results)} application result(s); {report.queued_count} queued; "
                    f"{report.recovered_unknown} uncertain submission(s) held."
                ),
                next_run_seconds=next_run_seconds,
            )
        repository.release_worker_lease(lease_owner)
