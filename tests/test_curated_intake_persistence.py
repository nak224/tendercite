import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from curated_intake_helpers import intake, intake_body, repository, upload

from tendercite.domain.curated_intake import (
    CuratedIntakeError,
    CuratedTenderIntake,
    intake_analysis_id,
    intake_finding_id,
)
from tendercite.domain.models import AssessmentRequest
from tendercite.domain.review import ReviewRequest
from tendercite.repositories.sqlite import SqliteRepository


def test_repeated_commit_keeps_reviewed_analysis_and_decisions(client):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    created = repo.commit_curated_intake(request)
    finding_id = repo.get_analysis(created.analysis_id).findings[0].id
    repo.add_review(finding_id, ReviewRequest(action="CONFIRM", comment="Synthetic simulation"))
    repo.set_assessment(
        finding_id,
        AssessmentRequest(status="CLARIFICATION_NEEDED", rationale="Synthetic operator decision"),
    )
    before = repo.database_path.read_bytes()
    matrix = repo.list_matrix()
    repeated = repo.commit_curated_intake(request)
    assert repeated.status == "ALREADY_PRESENT" and repeated.analysis_id == created.analysis_id
    assert repo.database_path.read_bytes() == before
    assert repo.list_matrix() == matrix
    assert repo.get_finding(finding_id).review_status == "CONFIRMED"
    assert len(repo.list_reviews(finding_id)) == 1


def test_distinct_revision_preserves_original_analysis_and_reviews(client):
    document = upload(client)
    repo = repository(client)
    old = repo.commit_curated_intake(intake(document))
    original = repo.get_analysis(old.analysis_id)
    repo.add_review(original.findings[0].id, ReviewRequest(action="CONFIRM"))
    body = intake_body(document)
    body["findings"][0]["statement"] = "A changed manually entered interpretation."
    new = repo.commit_curated_intake(CuratedTenderIntake.model_validate_json(json.dumps(body)))
    assert old.analysis_id != new.analysis_id
    assert repo.get_analysis(old.analysis_id) == original
    assert repo.get_finding(original.findings[0].id).review_status == "CONFIRMED"
    assert repo.get_analysis(new.analysis_id).findings[0].review_status == "UNREVIEWED"
    assert set(f.id for f in repo.list_findings(old.analysis_id)).isdisjoint(
        f.id for f in repo.list_findings(new.analysis_id)
    )


def test_actual_sql_failure_rolls_back_analysis_and_all_findings(client):
    document = upload(client)
    body = intake_body(document)
    second = copy.deepcopy(body["findings"][0])
    second["item_id"] = "second"
    body["findings"].append(second)
    request = CuratedTenderIntake.model_validate_json(json.dumps(body))
    repo = repository(client)
    with repo._connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON findings "
            "WHEN NEW.id='" + intake_finding_id(request, "second") + "' "
            "BEGIN SELECT RAISE(ABORT,'Synthetic private failure'); END"
        )
    with pytest.raises(CuratedIntakeError, match="INTAKE_IDENTITY_CONFLICT"):
        repo.commit_curated_intake(request)
    assert repo.get_analysis(intake_analysis_id(request)) is None
    assert repo.list_findings() == [] and len(repo.list_documents()) == 1


def test_concurrent_duplicates_are_serialized_without_duplicate_records(client):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    barrier = Barrier(2)

    def commit():
        writer = SqliteRepository(repo.database_path, initialize=False)
        barrier.wait(timeout=10)
        return writer.commit_curated_intake(request)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: commit(), range(2)))
    assert sorted(r.status for r in results) == ["ALREADY_PRESENT", "CREATED"]
    assert len({r.analysis_id for r in results}) == 1
    assert len(repo.list_findings()) == 1
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 1


def test_identity_collision_never_replaces_existing_analysis(client):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    existing = repo.commit_curated_intake(request)
    old = repo.get_analysis(existing.analysis_id)
    finding_id = old.findings[0].id
    repo.add_review(finding_id, ReviewRequest(action="CONFIRM"))
    # Simulate a pre-existing conflicting ordinary analysis at the derived ID.
    conflict = old.model_copy(update={"curated_intake": None, "provider": "synthetic_model"})
    with repo._connect() as conn:
        conn.execute(
            "UPDATE analyses SET payload=? WHERE id=?", (conflict.model_dump_json(), old.id)
        )
    before = repo.database_path.read_bytes()
    with pytest.raises(CuratedIntakeError, match="INTAKE_IDENTITY_CONFLICT"):
        repo.commit_curated_intake(request)
    assert repo.database_path.read_bytes() == before
    assert repo.get_analysis(old.id) == conflict
    assert repo.get_finding(finding_id).review_status == "CONFIRMED"


def test_changed_page_with_still_valid_quote_cannot_rewrite_old_source_state(client):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    result = repo.commit_curated_intake(request)
    original = repo.get_analysis(result.analysis_id)
    with repo._connect() as conn:
        conn.execute(
            "UPDATE pages SET text=text || ' Additional context.' "
            "WHERE document_id=? AND page_number=1",
            (document["id"],),
        )
    with pytest.raises(CuratedIntakeError, match="INTAKE_SOURCE_STATE_CHANGED"):
        repo.commit_curated_intake(request)
    assert repo.get_analysis(result.analysis_id) == original


@pytest.mark.parametrize("tamper", ["finding", "missing_finding", "metadata"])
def test_inconsistent_existing_records_are_not_silently_repaired(client, tamper):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    result = repo.commit_curated_intake(request)
    run = repo.get_analysis(result.analysis_id)
    with repo._connect() as conn:
        if tamper == "metadata":
            changed = run.model_copy(update={"model": "a-provider-that-never-ran"})
            conn.execute(
                "UPDATE analyses SET payload=? WHERE id=?", (changed.model_dump_json(), run.id)
            )
        elif tamper == "missing_finding":
            conn.execute("DELETE FROM findings WHERE id=?", (run.findings[0].id,))
        else:
            changed = run.findings[0].model_copy(
                update={"statement": "Tampered original candidate"}
            )
            conn.execute(
                "UPDATE findings SET payload=? WHERE id=?", (changed.model_dump_json(), changed.id)
            )
    before = repo.database_path.read_bytes()
    with pytest.raises(CuratedIntakeError, match="INTAKE_STORAGE_INTEGRITY"):
        repo.commit_curated_intake(request)
    assert repo.database_path.read_bytes() == before


def test_commit_on_deleted_existing_database_does_not_create_an_empty_one(client):
    document = upload(client)
    writer = SqliteRepository(repository(client).database_path, initialize=False)
    path = writer.database_path
    path.unlink()
    with pytest.raises(CuratedIntakeError, match="DATABASE_FAILED"):
        writer.commit_curated_intake(intake(document))
    assert not path.exists()
