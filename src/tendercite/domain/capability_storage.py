"""Local metadata only; no snapshot-to-tender associations or authority inference."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class CapabilitySnapshotMetadata:
    content_hash: str
    schema_version: str
    source_system: str
    assessment_period_start: date
    assessment_period_end: date
    imported_at: datetime


@dataclass(frozen=True)
class CapabilityImportResult:
    inserted: bool
    metadata: CapabilitySnapshotMetadata
