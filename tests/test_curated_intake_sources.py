import copy
import hashlib
import json
from contextlib import contextmanager

import pytest
from curated_intake_helpers import QUOTE, intake, intake_body, repository, upload

from tendercite.domain.curated_intake import CuratedIntakeError, CuratedTenderIntake
from tendercite.repositories.sqlite import SqliteRepository


def test_dry_run_is_one_consistent_read_without_writes(client, monkeypatch):
    document = upload(client)
    repo = repository(client)
    before = repo.database_path.read_bytes()
    connections, statements = [], []
    connect = repo._connect

    @contextmanager
    def traced():
        with connect() as conn:
            connections.append(conn)
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(repo, "_connect", traced)
    result = repo.validate_curated_intake(intake(document))
    assert result.status == "VALIDATED" and result.source_count == result.finding_count == 1
    assert result.quotation_count == 1 and len(connections) == 1
    assert statements[0] == "BEGIN"
    assert not any(s.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for s in statements)
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize(
    "kind, error",
    [
        ("missing_document", "SOURCE_DOCUMENT_NOT_FOUND"),
        ("wrong_hash", "SOURCE_PDF_HASH_MISMATCH"),
        ("missing_page", "SOURCE_PAGE_NOT_FOUND"),
        ("invalid_quote", "SOURCE_QUOTE_NOT_VERIFIED"),
        ("partial_pages", "SOURCE_PAGES_INCOMPLETE"),
        ("huge_page_count", "SOURCE_PAGES_INCOMPLETE"),
        ("wrong_media", "SOURCE_DOCUMENT_NOT_PDF"),
    ],
)
def test_source_errors_fail_entire_intake_before_analysis(client, kind, error):
    document = upload(client)
    repo = repository(client)
    body = intake_body(document)
    if kind == "missing_document":
        body["documents"][0]["document_id"] = "missing"
        body["findings"][0]["evidence"][0]["document_id"] = "missing"
    elif kind == "wrong_hash":
        body["documents"][0]["expected_pdf_sha256"] = "0" * 64
    elif kind == "missing_page":
        body["findings"][0]["evidence"][0]["page_number"] = 3
    elif kind == "invalid_quote":
        body["findings"][0]["evidence"][0]["quote"] = "This sentence was never written."
    elif kind == "partial_pages":
        with repo._connect() as conn:
            conn.execute(
                "DELETE FROM pages WHERE document_id=? AND page_number=2", (document["id"],)
            )
    else:
        with repo._connect() as conn:
            if kind == "huge_page_count":
                conn.execute(
                    "UPDATE documents SET page_count=? WHERE id=?", (10**12, document["id"])
                )
            else:
                conn.execute(
                    "UPDATE documents SET media_type='text/plain' WHERE id=?", (document["id"],)
                )
    request = CuratedTenderIntake.model_validate_json(json.dumps(body))
    for operation in (repo.validate_curated_intake, repo.commit_curated_intake):
        with pytest.raises(CuratedIntakeError, match=error):
            operation(request)
    assert repo.list_findings() == []
    with repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 0


def test_existing_whitespace_normalization_preserves_raw_quote_in_provenance(client):
    document = upload(client)
    request = intake(
        document, quote="  The contractor\n must provide\tPostgreSQL development experience. "
    )
    repo = repository(client)
    result = repo.commit_curated_intake(request)
    run = repo.get_analysis(result.analysis_id)
    evidence = run.findings[0].evidence[0]
    assert evidence.quote == QUOTE and evidence.grounding_status == "VERIFIED_QUOTE"
    assert evidence.chunk_id is None and evidence.char_start == 0
    assert evidence.char_end == len(QUOTE)
    assert (
        run.curated_intake.intake.findings[0].evidence[0].quote
        == request.findings[0].evidence[0].quote
    )
    assert (
        run.curated_intake.validated_pages[0].stored_page_sha256
        == hashlib.sha256(repo.get_page(document["id"], 1).text.encode()).hexdigest()
    )


def test_multiple_quotes_across_real_parsed_documents(client):
    first = upload(client)
    other_quote = "The contractor must maintain liability insurance."
    second = upload(client, pages=(other_quote,))
    body = intake_body(first)
    body["documents"].append(dict(document_id=second["id"], expected_pdf_sha256=second["sha256"]))
    body["findings"][0]["evidence"].extend(
        [
            dict(
                document_id=first["id"],
                page_number=2,
                quote="Offers must arrive before the stated deadline.",
            ),
            dict(document_id=second["id"], page_number=1, quote=other_quote),
        ]
    )
    request = CuratedTenderIntake.model_validate_json(json.dumps(body))
    repo = repository(client)
    result = repo.commit_curated_intake(request)
    assert result.source_count == 2 and result.quotation_count == 3
    run = repo.get_analysis(result.analysis_id)
    assert set(run.request.document_ids) == {first["id"], second["id"]}
    assert len(run.findings[0].evidence) == 3
    assert all(e.grounding_status == "VERIFIED_QUOTE" for e in run.findings[0].evidence)


def test_one_bad_finding_is_never_silently_dropped(client):
    document = upload(client)
    body = intake_body(document)
    invalid = copy.deepcopy(body["findings"][0])
    invalid["item_id"] = "invalid"
    invalid["evidence"][0]["quote"] = "Unavailable evidence"
    body["findings"].append(invalid)
    request = CuratedTenderIntake.model_validate_json(json.dumps(body))
    repo = repository(client)
    with pytest.raises(CuratedIntakeError, match="SOURCE_QUOTE_NOT_VERIFIED"):
        repo.commit_curated_intake(request)
    assert repo.list_findings() == []


@pytest.mark.parametrize("change", ["deleted", "hash", "page"])
def test_commit_rechecks_sources_after_successful_dry_run(client, change):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    assert repo.validate_curated_intake(request).status == "VALIDATED"
    if change == "deleted":
        assert repo.delete_document(document["id"])
    else:
        with repo._connect() as conn:
            if change == "hash":
                conn.execute("UPDATE documents SET sha256=? WHERE id=?", ("0" * 64, document["id"]))
            else:
                conn.execute(
                    "UPDATE pages SET text='Changed source' WHERE document_id=? AND page_number=1",
                    (document["id"],),
                )
    with pytest.raises(CuratedIntakeError):
        repo.commit_curated_intake(request)
    assert repo.list_findings() == []


def test_restart_preserves_original_manual_provenance(client):
    document = upload(client)
    request = intake(document)
    repo = repository(client)
    result = repo.commit_curated_intake(request)
    restarted = SqliteRepository(repo.database_path, read_only=True)
    run, summary = restarted.inspect_curated_intake(result.analysis_id)
    assert (
        summary.status == "INSPECTED" and summary.intake_content_hash == result.intake_content_hash
    )
    assert run.curated_intake.intake == request
    assert (
        run.provider == "manual_operator_entry"
        and run.model == run.embedding_model == "not_invoked"
    )
    assert run.retrieval_plan is None and run.retrieved_chunk_ids == []
    assert run.findings[0].original_output is None and run.findings[0].review_status == "UNREVIEWED"
    assert run.curated_intake.item_identities[0].item_id == "postgresql"
