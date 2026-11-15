"""Authorization boundary usable by application services, HTTP and CLI."""

from typing import Protocol


class Authorization(Protocol):
    def allows(self, subject: object, resource: object, action: str) -> bool:
        """Evaluate trusted server-side identifiers; unknown rules deny access."""
        ...
