"""Load module mappings through the same authorized registry as application startup."""

from sqlalchemy import MetaData

from app.module_providers import module_providers
from common.infrastructure.job_error_model import JobErrorRow


def application_metadata() -> MetaData:
    # Provider imports load their own repositories/mappings; no module internals leak here.
    module_providers()
    # Common technical tables have no business-module provider.
    return JobErrorRow.metadata
