import hashlib
import json
from datetime import date
from decimal import Decimal

import pytest
from capability_helpers import CONTENT_HASH, FILE_HASH, FIXTURE, capacity, encode, example, member

from tendercite.domain.capabilities_v1 import AssessmentPeriod, CapabilitySnapshot, canonical_json
from tendercite.services.capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    validate_capability_json,
)


def invalid(body, code=CapabilityErrorCode.INVALID_CONTRACT):
    with pytest.raises(CapabilityError) as error:
        validate_capability_json(encode(body))
    assert error.value.code == code


def test_exact_producer_fixture_and_independent_hash():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == FILE_HASH
    snapshot = validate_capability_json(raw)
    body = snapshot.model_dump(mode="json")
    body.pop("content_hash")
    independently_serialized = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    assert "sha256:" + hashlib.sha256(independently_serialized).hexdigest() == CONTENT_HASH
    assert snapshot.content_hash == CONTENT_HASH
    assert snapshot == CapabilitySnapshot.model_validate_json(canonical_json(snapshot))
    assert isinstance(snapshot.workforce_members[0].capacity.remaining_hours, Decimal)


def test_hash_uses_validated_representation_not_pretty_printed_input():
    body = example()
    member(body)["person_id"] = "00000000000000000000000000000001"
    capacity(body)["scheduled_hours"] = "20.000"
    body["skill_catalog"][0]["skill_id"] = "{00000000-0000-0000-0000-000000000064}"
    snapshot = validate_capability_json(json.dumps(body, indent=4).encode())
    assert snapshot.content_hash == CONTENT_HASH
    assert b'"scheduled_hours":"20.00"' in canonical_json(snapshot)


@pytest.mark.parametrize("version", ["1.1", "2.0", 1, None])
def test_unsupported_versions_fail_explicitly(version):
    body = example()
    body["schema_version"] = version
    invalid(body, CapabilityErrorCode.UNSUPPORTED_SCHEMA)


@pytest.mark.parametrize("level", ["root", "period", "member", "skill", "capacity", "catalog"])
@pytest.mark.parametrize("field", ["full_name", "email", "notes", "is_active"])
def test_unknown_fields_rejected_at_every_level(level, field):
    body = example()
    targets = {
        "root": body,
        "period": body["assessment_period"],
        "member": member(body),
        "skill": member(body)["skills"][0],
        "capacity": capacity(body),
        "catalog": body["skill_catalog"][0],
    }
    targets[level][field] = "synthetic-private-marker"
    invalid(body)


@pytest.mark.parametrize("level", ["root", "period", "member", "skill", "capacity", "catalog"])
def test_every_field_is_required_including_nullables(level):
    original = example()
    paths = {
        "root": (),
        "period": ("assessment_period",),
        "member": ("workforce_members", 0),
        "skill": ("workforce_members", 0, "skills", 0),
        "capacity": ("workforce_members", 0, "capacity"),
        "catalog": ("skill_catalog", 0),
    }
    target = original
    for part in paths[level]:
        target = target[part]
    for field in target:
        body = example()
        nested = body
        for part in paths[level]:
            nested = nested[part]
        del nested[field]
        # A missing content_hash must not be supplied by the test sealing helper.
        with pytest.raises(CapabilityError) as error:
            validate_capability_json(encode(body, rehash=field != "content_hash"))
        assert error.value.code == CapabilityErrorCode.INVALID_CONTRACT


@pytest.mark.parametrize("kind", ["members", "assignments", "catalog"])
@pytest.mark.parametrize("change", ["duplicate", "reverse", "invalid_uuid"])
def test_uuid_order_uniqueness_and_format(kind, change):
    body = example()
    values, key = {
        "members": (body["workforce_members"], "person_id"),
        "assignments": (member(body)["skills"], "skill_id"),
        "catalog": (body["skill_catalog"], "skill_id"),
    }[kind]
    if change == "duplicate":
        values.append(values[0])
    elif change == "reverse":
        values.reverse()
    else:
        values[0][key] = "not-a-uuid"
    invalid(body)


@pytest.mark.parametrize(
    "change", ["missing_catalog", "unused_catalog", "wrong_skill_source", "engagement", "source"]
)
def test_references_and_source_enums(change):
    body = example()
    if change == "missing_catalog":
        body["skill_catalog"].pop()
    elif change == "unused_catalog":
        body["skill_catalog"].append(
            {
                "skill_id": "00000000-0000-0000-0000-000000000099",
                "name": "Synthetic",
                "category": "Test",
            }
        )
    elif change == "wrong_skill_source":
        member(body)["skills"][0]["source"] = "verified_experience"
    elif change == "engagement":
        member(body)["engagement_type"] = "consultant"
    else:
        body["source_system"] = "unsupported-system"
    invalid(body)


@pytest.mark.parametrize("change", ["reverse", "missing", "modified"])
def test_source_limitations_are_exact_and_ordered(change):
    body = example()
    if change == "reverse":
        body["source_limitations"].reverse()
    elif change == "missing":
        body["source_limitations"].pop()
    else:
        body["source_limitations"][0] = "qualification_proven"
    invalid(body)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2027-02-02", "2027-02-01"),
        ("2027-01-01", "2028-01-02"),
        ("2027-2-1", "2027-02-01"),
        ("2027-02-01T00:00:00Z", "2027-02-01"),
        (20270201, "2027-02-01"),
        ("2027-02-29", "2027-03-01"),
    ],
)
def test_invalid_date_ranges(start, end):
    body = example()
    body["assessment_period"] = {"start_date": start, "end_date": end}
    invalid(body)


def test_empty_workforce_catalog_and_366_day_period():
    body = example()
    body["workforce_members"], body["skill_catalog"] = [], []
    body["assessment_period"] = {"start_date": "2028-01-01", "end_date": "2028-12-31"}
    assert validate_capability_json(encode(body)).workforce_members == ()
    assert (
        AssessmentPeriod(start_date=date(2028, 1, 1), end_date="2028-12-31").end_date.year == 2028
    )


@pytest.mark.parametrize(
    "field",
    [
        "scheduled_hours",
        "available_hours",
        "remaining_hours",
        "overallocated",
        "overallocated_hours",
        "has_daily_overallocation",
        "daily_overallocated_hours",
    ],
)
@pytest.mark.parametrize("state", ["known", "unknown"])
def test_capacity_assessed_field_nullability(field, state):
    body = example()
    cap = capacity(body) if state == "known" else body["workforce_members"][2]["capacity"]
    cap[field] = None if state == "known" else (False if "hours" not in field else "0.00")
    invalid(body)


@pytest.mark.parametrize(
    "value", ["1.001", "NaN", "Infinity", "-Infinity", "1e2", "01.00", "1" * 25, 1.25, 1, True]
)
def test_invalid_decimal_precision_magnitude_and_json_types(value):
    body = example()
    capacity(body)["allocated_hours"] = value
    invalid(body)


@pytest.mark.parametrize(
    "field",
    [
        "scheduled_hours",
        "unavailable_hours",
        "available_hours",
        "allocated_hours",
        "overallocated_hours",
        "daily_overallocated_hours",
    ],
)
def test_nonnegative_hours_do_not_accept_negative_amounts(field):
    body = example()
    capacity(body)[field] = "-0.01"
    invalid(body)


@pytest.mark.parametrize("value", [0, 6, "3", 3.0, True])
def test_nullable_proficiency_is_strict_integer_one_to_five(value):
    body = example()
    member(body)["skills"][0]["proficiency"] = value
    invalid(body)


@pytest.mark.parametrize("field", ["overallocated", "has_daily_overallocation"])
@pytest.mark.parametrize("value", [0, 1, "false"])
def test_capacity_indicators_are_strict_booleans(field, value):
    body = example()
    capacity(body)[field] = value
    invalid(body)


@pytest.mark.parametrize("value", ["0.00", "-0.00", "-" + "9" * 22 + ".99"])
def test_valid_zero_negative_and_maximum_precision_hour_values(value):
    body = example()
    capacity(body)["remaining_hours"] = value
    assert validate_capability_json(encode(body)).workforce_members[
        0
    ].capacity.remaining_hours == Decimal(value)


def test_members_without_assigned_skills_allow_an_empty_catalog():
    body = example()
    for person in body["workforce_members"]:
        person["skills"] = []
    body["skill_catalog"] = []
    snapshot = validate_capability_json(encode(body))
    assert len(snapshot.workforce_members) == 3 and snapshot.skill_catalog == ()


def test_preserve_negative_remaining_zero_unknown_and_daily_overload():
    body = example()
    first = capacity(body)
    first["remaining_hours"] = "-1.25"
    second = body["workforce_members"][1]["capacity"]
    for field in second:
        if field.endswith("hours"):
            second[field] = "0.00"
    third = body["workforce_members"][2]["capacity"]
    third["unavailable_hours"] = "3.25"
    snapshot = validate_capability_json(encode(body))
    assert snapshot.workforce_members[0].capacity.remaining_hours == Decimal("-1.25")
    assert snapshot.workforce_members[1].capacity.scheduled_hours == Decimal("0")
    assert snapshot.workforce_members[2].capacity.scheduled_hours is None
    assert snapshot.workforce_members[2].capacity.allocated_hours == Decimal("2")
    assert snapshot.workforce_members[2].capacity.unavailable_hours == Decimal("3.25")
    assert snapshot.workforce_members[0].skills[1].proficiency is None
    unchanged = validate_capability_json(FIXTURE.read_bytes()).workforce_members[0].capacity
    assert unchanged.remaining_hours > 0 and unchanged.has_daily_overallocation
    assert unchanged.overallocated and unchanged.overallocated_hours == 0


def test_unicode_content_and_content_changes_are_hashed():
    body = example()
    body["skill_catalog"][0]["category"] = "Données"
    raw = encode(body)
    snapshot = validate_capability_json(raw)
    assert b"Donn\xc3\xa9es" in canonical_json(snapshot)
    body["content_hash"] = CONTENT_HASH
    with pytest.raises(CapabilityError) as error:
        validate_capability_json(encode(body, rehash=False))
    assert error.value.code == CapabilityErrorCode.HASH_MISMATCH
