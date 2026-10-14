"""Independent consumer of Team Capacity Engine's pinned schema 1.0 JSON contract.

No workforce claim becomes tender evidence merely by satisfying this contract.
"""

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, model_validator
from pydantic_core import PydanticCustomError

PRODUCER_COMMIT = "602f979c2932c4be9438e69e0a3e219518e86810"
SOURCE_LIMITATIONS = (
    "internal_skill_assignments_are_not_verified_experience",
    "proficiency_is_not_years_of_experience",
    "capacity_is_shared_unreserved_and_not_contractual_commitment",
    "unknown_capacity_is_neither_zero_nor_unlimited",
    "no_skill_hours_coverage_or_bidder_qualification_is_proven",
    "current_recurring_schedules_have_no_historical_versions",
    "date_only_totals_do_not_prove_intraday_availability",
)


class EngagementType(StrEnum):
    EMPLOYEE = "employee"
    FREELANCER = "freelancer"
    EXTERNAL_PARTNER = "external_partner"


def _hours(value: object) -> Decimal:
    if isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value):
        value = Decimal(value)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Invalid decimal-hour representation")
    return value


Hours = Annotated[
    Decimal,
    BeforeValidator(_hours),
    Field(allow_inf_nan=False, max_digits=24, decimal_places=2),
    PlainSerializer(lambda hours: format(hours, ".2f"), return_type=str, when_used="json"),
]
NonnegativeHours = Annotated[Hours, Field(ge=0)]


def _date_only(value: object) -> object:
    if type(value) is date or (
        isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
    ):
        return value
    raise ValueError("Invalid date-only representation")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class AssessmentPeriod(BaseModel):
    # Match the producer's DateRange behavior, including its Python date inputs.
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, frozen=True, hide_input_in_errors=True
    )
    start_date: Annotated[date, BeforeValidator(_date_only)]
    end_date: Annotated[date, BeforeValidator(_date_only)]

    @model_validator(mode="after")
    def inclusive_period(self) -> Self:
        if not 1 <= (self.end_date - self.start_date).days + 1 <= 366:
            raise ValueError("Invalid inclusive assessment period")
        return self


class Capacity(ContractModel):
    capacity_state: Literal["known", "unknown"]
    scheduled_hours: NonnegativeHours | None
    unavailable_hours: NonnegativeHours
    available_hours: NonnegativeHours | None
    allocated_hours: NonnegativeHours
    remaining_hours: Hours | None
    overallocated: bool | None
    overallocated_hours: NonnegativeHours | None
    has_daily_overallocation: bool | None
    daily_overallocated_hours: NonnegativeHours | None

    @model_validator(mode="after")
    def assessed_fields(self) -> Self:
        values = (
            self.scheduled_hours,
            self.available_hours,
            self.remaining_hours,
            self.overallocated,
            self.overallocated_hours,
            self.has_daily_overallocation,
            self.daily_overallocated_hours,
        )
        if self.capacity_state == "unknown" and any(v is not None for v in values):
            raise ValueError("Unknown capacity has assessed values")
        if self.capacity_state == "known" and any(v is None for v in values):
            raise ValueError("Known capacity lacks assessed values")
        return self


class AssignedSkill(ContractModel):
    skill_id: UUID
    proficiency: Annotated[int, Field(ge=1, le=5)] | None
    source: Literal["internal_skill_assignment"]


class CatalogSkill(ContractModel):
    skill_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    category: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]


def _ordered_unique(values: tuple[UUID, ...]) -> bool:
    return values == tuple(sorted(set(values)))


class WorkforceMember(ContractModel):
    person_id: UUID
    engagement_type: EngagementType
    skills: tuple[AssignedSkill, ...]
    capacity: Capacity

    @model_validator(mode="after")
    def skill_order(self) -> Self:
        if not _ordered_unique(tuple(s.skill_id for s in self.skills)):
            raise ValueError("Invalid skill assignment UUID order or uniqueness")
        return self


def canonical_json(snapshot: BaseModel, *, exclude: set[str] | None = None) -> bytes:
    """Canonical validated content, independent of input whitespace and object-key order."""
    return json.dumps(
        snapshot.model_dump(mode="json", exclude=exclude),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class CapabilitySnapshot(ContractModel):
    schema_version: Literal["1.0"]
    source_system: Literal["team-capacity-engine"]
    assessment_period: AssessmentPeriod
    workforce_members: tuple[WorkforceMember, ...]
    skill_catalog: tuple[CatalogSkill, ...]
    source_limitations: tuple[str, ...]
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def references_and_integrity(self) -> Self:
        if self.source_limitations != SOURCE_LIMITATIONS:
            raise ValueError("Invalid source limitations")
        if not _ordered_unique(tuple(m.person_id for m in self.workforce_members)):
            raise ValueError("Invalid member UUID order or uniqueness")
        catalog = tuple(s.skill_id for s in self.skill_catalog)
        if not _ordered_unique(catalog):
            raise ValueError("Invalid catalog UUID order or uniqueness")
        assigned = {s.skill_id for m in self.workforce_members for s in m.skills}
        if assigned != set(catalog):
            raise ValueError("Invalid catalog references")
        expected = (
            "sha256:" + hashlib.sha256(canonical_json(self, exclude={"content_hash"})).hexdigest()
        )
        if self.content_hash != expected:
            raise PydanticCustomError("capability_hash_mismatch", "Content hash mismatch")
        return self
