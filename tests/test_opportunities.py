"""Native opportunity contracts and atomic independent persistence, entirely offline."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from opportunity_helpers import OPPORTUNITY_ID, create_body, create_input, update_input
from persistent_mapping_helpers import approved_mapping, seeded_repository
from profile_helpers import create_input as profile_input
from pydantic import ValidationError

from tendercite.domain.models import AssessmentRequest
from tendercite.domain.opportunities import NativeOpportunity, OpportunityCreate, OpportunityError
from tendercite.repositories.sqlite import SqliteRepository


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "opportunities.db")


@pytest.mark.parametrize("kind", ["FREELANCE_PROJECT", "PUBLIC_TENDER", "OTHER"])
def test_create_retrieve_restart_and_unverified_manual_origin(repo, kind):
    request = create_input(kind=kind)
    result = repo.create_opportunity(request)
    assert result.status == "CREATED" and result.record.revision == 1
    assert result.record.opportunity.source_origin == "manual_operator_entry"
    assert result.record.opportunity.verification_status == "NOT_VERIFIED"
    assert result.record.opportunity.opportunity_type == kind
    restarted = SqliteRepository(repo.database_path, read_only=True)
    assert restarted.get_opportunity(OPPORTUNITY_ID) == result.record
    assert repo.list_findings() == [] and repo.list_profiles().profiles == ()
    with repo._connect() as conn:
        assert conn.execute("SELECT count(*) FROM analyses").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM native_profiles").fetchone()[0] == 0


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///tmp/a",
        "data:text/html,x",
        "ftp://example.invalid/a",
        "//example.invalid/a",
        "https://",
        "https://user:secret@example.invalid/a",
        "https://user@example.invalid/a",
        "https://example.invalid:bad/a",
        "https://example.invalid:0/a",
        "https://example.invalid:99999/a",
        "https://exa mple.invalid/a",
        "https://example.invalid/\nprivate",
        "https://example.invalid\\@other.invalid/",
    ],
)
def test_unsupported_and_malformed_url_references_are_rejected(url):
    body = create_body()
    body["opportunity"]["source_url"] = url
    with pytest.raises(ValidationError):
        OpportunityCreate.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.invalid/opportunity",
        "https://example.invalid/a?q=1#source",
        "https://example.invalid:8443/opportunity",
    ],
)
def test_http_reference_preserved_without_normalizing_or_fetching(url):
    body = create_body()
    body["opportunity"]["source_url"] = url
    assert OpportunityCreate.model_validate_json(json.dumps(body)).opportunity.source_url == url


@pytest.mark.parametrize(
    "change",
    [
        {"title": " "},
        {"opportunity_type": "JOB"},
        {"schema_version": "future"},
        {"deadline": "2027-02-29"},
        {"deadline": "2027-03-01T12:00:00Z"},
        {"required_skills": ["Python", " ＰＹＴＨＯＮ "]},
        {"required_skills": [" "]},
        {"required_skills": [str(i) for i in range(101)]},
        {"description": "x" * 2001},
        {"analysis_id": "not-a-relationship"},
        {"profile_id": "not-a-relationship"},
        {"verification_status": "VERIFIED"},
        {"source_origin": "team-capacity-engine"},
    ],
)
def test_strict_contract_rejects_invalid_fields_budgets_and_implied_verification(change):
    body = create_body()
    body["opportunity"].update(change)
    with pytest.raises(ValidationError):
        OpportunityCreate.model_validate_json(json.dumps(body))


def test_only_title_and_type_required_and_date_only_deadlines_are_supported():
    value = NativeOpportunity.model_validate_json(
        '{"title":"  Synthetic opportunity ","opportunity_type":"OTHER"}'
    )
    assert value.title == "Synthetic opportunity" and value.deadline is None
    assert value.source_url == "" and value.required_skills == ()
    body = create_body()
    body["opportunity"]["deadline"] = "2028-02-29"
    assert (
        str(OpportunityCreate.model_validate_json(json.dumps(body)).opportunity.deadline)
        == "2028-02-29"
    )


def test_update_skills_general_fields_and_concurrency_binding(repo):
    record = repo.create_opportunity(create_input()).record
    body = record.opportunity.model_dump(mode="json")
    body.update(
        title="Changed synthetic opportunity",
        required_skills=["React"],
        deadline=None,
        source_url="",
        work_arrangement="HYBRID",
    )
    value = NativeOpportunity.model_validate_json(json.dumps(body))
    updated = repo.update_opportunity(OPPORTUNITY_ID, update_input(value)).record
    assert updated.revision == 2 and updated.created_at == record.created_at
    assert updated.opportunity == value
    with pytest.raises(OpportunityError, match="OPPORTUNITY_REVISION_CONFLICT"):
        repo.update_opportunity(OPPORTUNITY_ID, update_input(record.opportunity))
    assert repo.get_opportunity(OPPORTUNITY_ID) == updated


def test_multiple_opportunities_bounded_listing_and_identical_creation(repo):
    for i, kind in enumerate(("OTHER", "PUBLIC_TENDER", "FREELANCE_PROJECT")):
        repo.create_opportunity(create_input(kind=kind, opportunity_id=str(UUID(int=i))))
    listing = repo.list_opportunities(limit=2)
    assert len(listing.opportunities) == 2 and listing.next_offset == 2
    assert [r.opportunity_id.int for r in listing.opportunities] == [0, 1]
    assert listing.opportunities[0].organization == "Synthetic organization"
    assert (
        "source_url" not in listing.model_dump_json()
        and "required_skills" not in listing.model_dump_json()
    )
    assert len(repo.list_opportunities(offset=2).opportunities) == 1
    record = repo.create_opportunity(create_input()).record
    before = repo.database_path.read_bytes()
    assert repo.create_opportunity(create_input()).status == "ALREADY_PRESENT"
    assert repo.database_path.read_bytes() == before
    body = create_body()
    body["opportunity"]["title"] = "Different content"
    with pytest.raises(OpportunityError, match="OPPORTUNITY_ID_CONFLICT"):
        repo.create_opportunity(OpportunityCreate.model_validate_json(json.dumps(body)))
    repo.update_opportunity(OPPORTUNITY_ID, update_input(record.opportunity))
    with pytest.raises(OpportunityError, match="OPPORTUNITY_ID_CONFLICT"):
        repo.create_opportunity(create_input())


def test_concurrent_creates_and_updates_do_not_overwrite_each_other(repo):
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(
            pool.map(lambda _: repo.create_opportunity(create_input()).status, range(4))
        )
    assert statuses.count("CREATED") == 1 and statuses.count("ALREADY_PRESENT") == 3
    value = create_input().opportunity

    def update(_):
        try:
            return repo.update_opportunity(OPPORTUNITY_ID, update_input(value)).status
        except OpportunityError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(update, range(4)))
    assert statuses.count("UPDATED") == 1 and statuses.count("OPPORTUNITY_REVISION_CONFLICT") == 3


def test_missing_invalid_read_only_and_corrupt_records_fail_safely(repo):
    with pytest.raises(OpportunityError, match="OPPORTUNITY_NOT_FOUND"):
        repo.get_opportunity(OPPORTUNITY_ID)
    with pytest.raises(OpportunityError, match="INVALID_OPPORTUNITY_INPUT"):
        repo.list_opportunities(limit=101)
    with pytest.raises(OpportunityError, match="INVALID_OPPORTUNITY_INPUT"):
        repo.get_opportunity("bad")
    with pytest.raises(OpportunityError, match="DATABASE_FAILED"):
        SqliteRepository(repo.database_path, read_only=True).create_opportunity(create_input())
    repo.create_opportunity(create_input())
    with repo._connect() as conn:
        conn.execute("UPDATE native_opportunities SET payload='{}'")
    before = repo.database_path.read_bytes()
    with pytest.raises(OpportunityError, match="OPPORTUNITY_STORAGE_INTEGRITY"):
        repo.get_opportunity(OPPORTUNITY_ID)
    assert repo.database_path.read_bytes() == before


def test_additive_initialization_and_saves_preserve_profiles_and_tender_data(tmp_path):
    repo = seeded_repository(tmp_path / "existing.db")
    approved_mapping(repo)
    repo.create_profile(profile_input())
    repo.set_assessment(
        "finding-1", AssessmentRequest(status="PARTIAL", rationale="Synthetic judgment")
    )
    with repo._connect() as conn:
        conn.execute("DROP TABLE native_opportunities")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {t: list(conn.execute(f'SELECT * FROM "{t}"')) for t in tables}
    repo = SqliteRepository(repo.database_path)
    record = repo.create_opportunity(create_input(kind="PUBLIC_TENDER")).record
    repo.update_opportunity(OPPORTUNITY_ID, update_input(record.opportunity))
    with repo._connect() as conn:
        assert {t: list(conn.execute(f'SELECT * FROM "{t}"')) for t in tables} == before
