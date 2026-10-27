"""Synthetic contract/persistence regressions for independent native profiles."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from persistent_mapping_helpers import approved_mapping, seeded_repository
from profile_helpers import PROFILE_ID, create_body, create_input, update_input
from pydantic import ValidationError

from tendercite.domain.profiles import NativeCapabilityProfile, ProfileCreate, ProfileError
from tendercite.repositories.sqlite import SqliteRepository


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "profiles.db")


@pytest.mark.parametrize("kind", ["FREELANCER", "COMPANY"])
def test_create_read_restart_and_native_provenance(repo, kind):
    body = create_body()
    body["profile"]["profile_type"] = kind
    request = ProfileCreate.model_validate_json(json.dumps(body))
    result = repo.create_profile(request)
    assert result.status == "CREATED" and result.record.revision == 1
    assert result.record.profile.profile_type == kind
    assert result.record.profile.source_origin == "manual_operator_entry"
    assert result.record.profile.skills[0].verification_status == "NOT_VERIFIED"
    assert result.record.profile.qualification == "NOT_VERIFIED"
    assert "team-capacity-engine" not in result.model_dump_json()
    assert (
        SqliteRepository(repo.database_path, read_only=True).get_profile(PROFILE_ID)
        == result.record
    )


@pytest.mark.parametrize(
    "failure",
    [
        "version",
        "extra",
        "blank",
        "type",
        "arrangement",
        "provenance",
        "verified",
        "duplicate-name",
        "duplicate-id",
        "duplicate-keyword",
        "skills-budget",
        "keyword-budget",
        "long-name",
    ],
)
def test_strict_contract(failure):
    body = create_body()
    profile = body["profile"]
    if failure == "version":
        profile["schema_version"] = "unsupported"
    if failure == "extra":
        profile["experience_years"] = 10
    if failure == "blank":
        profile["name"] = " "
    if failure == "type":
        profile["profile_type"] = "PERSON"
    if failure == "arrangement":
        profile["work_arrangement"] = "remote"
    if failure == "provenance":
        profile["source_origin"] = "team-capacity-engine"
    if failure == "verified":
        profile["skills"][0]["verification_status"] = "VERIFIED"
    if failure == "duplicate-name":
        profile["skills"].append({"skill_id": str(UUID(int=802)), "name": " ＰＹＴＨＯＮ "})
    if failure == "duplicate-id":
        profile["skills"].append({**profile["skills"][0], "name": "React"})
    if failure == "duplicate-keyword":
        profile["opportunity_keywords"] = [" AI ", "ai"]
    if failure == "skills-budget":
        profile["skills"] = [{"skill_id": str(UUID(int=i)), "name": str(i)} for i in range(101)]
    if failure == "keyword-budget":
        profile["opportunity_keywords"] = [str(i) for i in range(21)]
    if failure == "long-name":
        profile["name"] = "x" * 121
    with pytest.raises(ValidationError):
        ProfileCreate.model_validate_json(json.dumps(body))


def test_trimmed_labels_and_empty_optional_fields_are_supported():
    profile = NativeCapabilityProfile.model_validate_json(
        '{"name":"  Synthetic  ","profile_type":"COMPANY"}'
    )
    assert profile.name == "Synthetic"
    assert profile.skills == () and profile.opportunity_keywords == ()
    assert profile.work_arrangement is None


def test_multiple_profiles_listing_is_bounded_deterministic_metadata(repo):
    for i in (804, 800, 803):
        repo.create_profile(create_input(profile_id=str(UUID(int=i))))
    first = repo.list_profiles(limit=2)
    assert [item.profile_id.int for item in first.profiles] == [800, 803]
    assert first.next_offset == 2
    assert repo.list_profiles(limit=2, offset=2).profiles[0].profile_id.int == 804
    assert repo.list_profiles(limit=2, offset=2).next_offset is None
    assert '"skills"' not in first.model_dump_json()
    for kwargs in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": True}):
        with pytest.raises(ProfileError, match="INVALID_PROFILE_INPUT"):
            repo.list_profiles(**kwargs)


def test_update_add_remove_and_revision_conflict(repo):
    original = repo.create_profile(create_input()).record
    body = original.profile.model_dump(mode="json")
    body.update(
        name="Synthetic company",
        profile_type="COMPANY",
        headline="Changed",
        location="",
        work_arrangement="HYBRID",
    )
    body["skills"] = [{"skill_id": str(UUID(int=802)), "name": "React"}]
    profile = NativeCapabilityProfile.model_validate_json(json.dumps(body))
    saved = repo.update_profile(PROFILE_ID, update_input(profile))
    assert saved.status == "UPDATED" and saved.record.revision == 2
    assert saved.record.created_at == original.created_at
    assert saved.record.profile == profile
    with pytest.raises(ProfileError, match="PROFILE_REVISION_CONFLICT"):
        repo.update_profile(PROFILE_ID, update_input(original.profile))
    assert repo.get_profile(PROFILE_ID) == saved.record


def test_idempotent_creation_never_replaces_an_edited_profile(repo):
    request = create_input()
    result = repo.create_profile(request)
    before = repo.database_path.read_bytes()
    assert repo.create_profile(request).status == "ALREADY_PRESENT"
    assert repo.database_path.read_bytes() == before
    body = request.profile.model_dump(mode="json")
    body["name"] = "Changed"
    changed = NativeCapabilityProfile.model_validate_json(json.dumps(body))
    with pytest.raises(ProfileError, match="PROFILE_ID_CONFLICT"):
        repo.create_profile(create_input(profile=body))
    repo.update_profile(PROFILE_ID, update_input(changed))
    with pytest.raises(ProfileError, match="PROFILE_ID_CONFLICT"):
        repo.create_profile(request)
    assert repo.get_profile(PROFILE_ID).revision == 2
    assert result.record.profile.name == "Synthetic freelancer"


def test_concurrent_creation_and_updates_serialize_and_never_lose_edits(repo):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.create_profile(create_input()).status, range(4)))
    assert results.count("CREATED") == 1 and results.count("ALREADY_PRESENT") == 3
    profile = repo.get_profile(PROFILE_ID).profile

    def update(_):
        try:
            return repo.update_profile(PROFILE_ID, update_input(profile)).status
        except ProfileError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(update, range(4)))
    assert results.count("UPDATED") == 1 and results.count("PROFILE_REVISION_CONFLICT") == 3
    assert repo.get_profile(PROFILE_ID).revision == 2


def test_missing_invalid_and_read_only_operations_are_safe(repo):
    with pytest.raises(ProfileError, match="PROFILE_NOT_FOUND"):
        repo.get_profile(PROFILE_ID)
    with pytest.raises(ProfileError, match="PROFILE_NOT_FOUND"):
        repo.update_profile(PROFILE_ID, update_input(create_input().profile))
    with pytest.raises(ProfileError, match="INVALID_PROFILE_INPUT"):
        repo.get_profile("bad")
    with pytest.raises(ProfileError, match="INVALID_PROFILE_INPUT"):
        repo.create_profile({"PRIVATE": "PRIVATE"})
    with pytest.raises(ProfileError, match="DATABASE_FAILED"):
        SqliteRepository(repo.database_path, read_only=True).create_profile(create_input())


@pytest.mark.parametrize("corruption", ["payload", "identity", "revision"])
def test_storage_integrity_checks(repo, corruption):
    record = repo.create_profile(create_input()).record
    body = record.model_dump(mode="json")
    if corruption == "identity":
        body["profile_id"] = str(UUID(int=999))
    if corruption == "revision":
        body["revision"] = 20
    with repo._connect() as conn:
        conn.execute(
            "UPDATE native_profiles SET payload=?",
            ("{}" if corruption == "payload" else json.dumps(body),),
        )
    before = repo.database_path.read_bytes()
    with pytest.raises(ProfileError, match="PROFILE_STORAGE_INTEGRITY"):
        repo.get_profile(PROFILE_ID)
    assert repo.database_path.read_bytes() == before


def test_non_destructive_initialization_and_profile_writes_preserve_all_other_tables(tmp_path):
    repo = seeded_repository(tmp_path / "existing.db")
    approved_mapping(repo)
    with repo._connect() as conn:
        conn.execute("DROP TABLE native_profiles")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {t: list(conn.execute(f'SELECT * FROM "{t}"')) for t in tables}
    repo = SqliteRepository(repo.database_path)
    result = repo.create_profile(create_input())
    repo.update_profile(PROFILE_ID, update_input(result.record.profile))
    with repo._connect() as conn:
        assert {t: list(conn.execute(f'SELECT * FROM "{t}"')) for t in tables} == before
