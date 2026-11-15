"""A deletion intent identifies one immutable temporary object, never a prefix."""

import re
from dataclasses import dataclass, field

from video.domain.publications import valid_identifier


@dataclass(frozen=True)
class OriginalDeletion:
    upload_id: int
    object_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not valid_identifier(self.upload_id)
            or re.fullmatch(r"incoming/[0-9a-f]{32}", self.object_key) is None
        ):
            raise ValueError("Invalid temporary original identity")
