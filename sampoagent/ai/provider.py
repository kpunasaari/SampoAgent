"""Provider-neutral records with a safe deterministic default."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Interpretation:
    provider: str
    value: str
    used_tokens: int


class DeterministicProvider:
    """Default Minimal mode provider: uses no network, credentials, or tokens."""
    def interpret_requirement(self, job: dict[str, object]) -> Interpretation:
        requirements = ", ".join(str(item) for item in job.get("mandatory_requirements", []))
        return Interpretation("none", requirements or "No explicit requirement extracted.", 0)
