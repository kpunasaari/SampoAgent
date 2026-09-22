"""Source adapter contracts that explicitly preserve site access boundaries."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryResult:
    capability: str
    jobs: list[object]
    message: str


class BrowserOnlySourceAdapter:
    def __init__(self, name: str, url: str) -> None:
        self.name = name
        self.url = url

    def discover(self) -> DiscoveryResult:
        return DiscoveryResult("Browser search only", [], f"{self.name} requires interactive browser search; no automated scraping was attempted.")


def source_health(url: str, capability: str) -> str:
    """Report only what can be determined without probing remote services."""
    if not url.startswith(("http://", "https://")):
        return "Temporarily unavailable"
    return capability
