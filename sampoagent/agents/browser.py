"""Browser-agent interface. Real providers must preserve the safety policy."""

from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Callable, Protocol

from sampoagent.agents.forms import FormSchema
from sampoagent.applications.field_resolver import FormField


@dataclass(frozen=True)
class SubmissionResult:
    submitted: bool
    manual_action_required: bool
    message: str
    final_url: str | None = None
    confirmation_id: str | None = None
    captcha_detected: bool = False
    outcome_unknown: bool = False


@dataclass(frozen=True)
class FormInspection:
    fields: tuple[FormField, ...]
    captcha_detected: bool = False
    authentication_required: bool = False
    validation_errors: tuple[str, ...] = ()
    signature: str = ""
    final_url: str = ""
    submit_control_ready: bool = True
    values: dict[str, str] = field(default_factory=dict)
    uploaded_files: dict[str, dict[str, str]] = field(default_factory=dict)
    schema: FormSchema | None = None
    next_step_control_ready: bool = False


class BrowserAgent(Protocol):
    configured: bool
    def open(self, url: str) -> None: ...
    def inspect_form(self) -> FormInspection: ...
    def fill(self, field: str, value: str) -> None: ...
    def upload(self, field: str, path: str) -> None: ...
    def validation_errors(self) -> Sequence[str]: ...
    def advance_step(
        self,
        *,
        expected_signature: str,
        pre_click_check: Callable[[], bool] | None = None,
    ) -> FormInspection: ...
    def submit(
        self,
        url: str,
        *,
        expected_signature: str | None = None,
        expected_uploads: dict[str, dict[str, str]] | None = None,
        pre_click_check: Callable[[], bool] | None = None,
    ) -> SubmissionResult: ...


class UnconfiguredBrowserAgent:
    """Safe fallback used until a Codex, Claude, or other adapter is connected."""
    configured = False

    def open(self, url: str) -> None:
        return None

    def inspect_form(self) -> FormInspection:
        return FormInspection((), authentication_required=True, final_url="", submit_control_ready=False)

    def fill(self, field: str, value: str) -> None:
        return None

    def upload(self, field: str, path: str) -> None:
        return None

    def validation_errors(self) -> Sequence[str]:
        return ()

    def advance_step(
        self,
        *,
        expected_signature: str,
        pre_click_check: Callable[[], bool] | None = None,
    ) -> FormInspection:
        raise RuntimeError("Browser agent is not configured")

    def submit(
        self,
        url: str,
        *,
        expected_signature: str | None = None,
        expected_uploads: dict[str, dict[str, str]] | None = None,
        pre_click_check: Callable[[], bool] | None = None,
    ) -> SubmissionResult:
        return SubmissionResult(False, True, "Browser agent is not configured; manual action required.", final_url=url)
