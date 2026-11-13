"""Authorization boundary usable by application services, HTTP and CLI."""

from typing import Protocol


class Authorization(Protocol):
    def allows(self, subject: str, resource: str, action: str) -> bool:
        """Evaluate trusted server-side identifiers; unknown rules deny access."""
        ...
