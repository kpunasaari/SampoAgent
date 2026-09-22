"""Browser-agent interface. Real providers must preserve the safety policy."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SubmissionResult:
    submitted: bool
    manual_action_required: bool
    message: str
    final_url: str | None = None
    confirmation_id: str | None = None


class BrowserAgent(Protocol):
    def open(self, url: str) -> None: ...
    def inspect(self) -> str: ...
    def fill(self, field: str, value: str) -> None: ...
    def submit(self, url: str) -> SubmissionResult: ...


class UnconfiguredBrowserAgent:
    """Safe fallback used until a Codex, Claude, or other adapter is connected."""
    def open(self, url: str) -> None:
        return None

    def inspect(self) -> str:
        return "Browser agent not configured."

    def fill(self, field: str, value: str) -> None:
        return None

    def submit(self, url: str) -> SubmissionResult:
        return SubmissionResult(False, True, "Browser agent is not configured; manual action required.", final_url=url)
