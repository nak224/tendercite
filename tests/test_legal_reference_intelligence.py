"""Original synthetic legal clauses/PDFs only; no external knowledge or provider calls."""

import copy
import hashlib
from textwrap import wrap
from types import SimpleNamespace

import pytest

from evaluation.generate import make_pdf
from evaluation.legal_reference_intelligence import (
    citation_status,
    derive_legal_references,
    instrument_matches,
    provisions,
    review_template,
)
from evaluation.staged_evidence_consolidation import consolidate, validate_extraction
from evaluation.staged_evidence_plan import digest, text_hash
from evaluation.staged_evidence_schemas import AtomicExtraction, PreparedStep, SourceSegment
from tendercite.services.pdf_parser import PyPdfParser

REG = "Regulation (EU) 2016/679"
ALIAS = "General Data Protection Regulation"
DIRECTIVE = "Directive 95/46/EC"


@pytest.fixture
def legal(tmp_path):
    def build(records, *, incomplete=False):
        # Each record has an original self-generated PDF, actually parsed using production code.
        pages, mapping, documents, steps, attempts, inventory = {}, {}, {}, [], {}, []
        for index, records_on_page in enumerate(records):
            name, doc_id = f"synthetic-{index}.pdf", f"synthetic-doc-{index}"
            text = "\n".join(dict.fromkeys(r["quote"] for r in records_on_page)) or "Empty sample."
            data = make_pdf([text])
            path = tmp_path / name
            path.write_bytes(data)
            parsed = PyPdfParser().parse(path)
            page = parsed[0].text
            sha = hashlib.sha256(data).hexdigest()
            pages[doc_id], mapping[name], documents[name] = {1: page}, doc_id, {"sha256": sha}
            source = SourceSegment(
                source_id=f"{doc_id}:page:1:chars:0-{len(page)}",
                document_id=doc_id,
                document_name=name,
                pdf_sha256=sha,
                page_number=1,
                chunk_id=None,
                text=page,
                text_sha256=text_hash(page),
                segment_index=1,
                char_start=0,
                char_end=len(page),
            )
            step = PreparedStep(
                question_id="synthetic-legal-question",
                question="What legal conditions apply?",
                question_type="synthetic",
                strategy="synthetic",
                stage="extraction",
                step_id=digest(index),
                batch_id=f"batch-{index}",
                sources=[source],
            )
            findings = [
                {
                    "finding_id": f"atomic-{index}-{i}",
                    "kind": r.get("kind", "LEGAL_REFERENCE"),
                    "subject": r.get("subject", REG),
                    "exact_identifier": r.get("identifier", REG),
                    "purpose": r.get("purpose", f"Original purpose {i}"),
                    "relationship": r.get("relationship", "OBLIGATION"),
                    "uncertainty": r.get("uncertainty"),
                    "evidence": [
                        {
                            "source_id": source.source_id,
                            "document_id": doc_id,
                            "pdf_sha256": sha,
                            "page_number": 1,
                            "quote": "\n".join(
                                wrap(
                                    r["quote"],
                                    width=80,
                                    break_on_hyphens=False,
                                    break_long_words=False,
                                )
                            ),
                        }
                    ],
                }
                for i, r in enumerate(records_on_page)
            ]
            answer = AtomicExtraction(
                question_id=step.question_id,
                batch_id=step.batch_id,
                status="FINDINGS" if findings else "NO_RELEVANT_FINDINGS",
                findings=findings,
                no_findings_reason=None if findings else "None stated.",
            )
            steps.append(step)
            attempts[step.batch_id] = {
                "completed": True,
                "attempt_id": f"original-{index}",
                "response_file_sha256": text_hash(answer.model_dump_json()),
                "record": {"structured_answer": answer.model_dump()},
            }
            inventory.append(
                {
                    "document_id": doc_id,
                    "document_name": name,
                    "page_number": 1,
                    "pdf_sha256": sha,
                    "batch_ids": [step.batch_id],
                }
            )
        corpus = SimpleNamespace(
            pages=pages,
            mapping=mapping,
            documents=documents,
            provenance={"family": "Original_Synthetic", "dataset_revision": "a" * 40},
        )
        for step in steps:
            record = attempts[step.batch_id]["record"]
            record["citation_validation"] = validate_extraction(
                AtomicExtraction.model_validate(record["structured_answer"]), step, corpus
            )
            assert record["citation_validation"]["extraction_valid"]
        manifest = {
            "plan_id": digest("synthetic-plan"),
            "question_id": "synthetic-legal-question",
            "provenance": corpus.provenance,
            "pages": inventory,
            "batches": [{"batch_id": s.batch_id} for s in steps],
        }
        if incomplete:
            attempts.pop(steps[-1].batch_id)
        core = consolidate(manifest, attempts)
        return core, corpus

    return build


def requirement(quote=None, **kwargs):
    return {"quote": quote or f"Under {REG}, the supplier must submit a security report.", **kwargs}


def test_explicit_alias_merges_across_documents_without_collapsing_obligations(legal):
    core, corpus = legal(
        [
            [requirement(f"Under {REG} ({ALIAS}), the supplier must submit a report.")],
            [
                requirement(
                    f"Under {ALIAS}, the supplier must retain a log.",
                    subject=ALIAS,
                    identifier=ALIAS,
                )
            ],
        ]
    )
    before = copy.deepcopy(core)
    result = derive_legal_references(core, corpus)
    assert core == before
    assert result["summary"]["distinct_normalized_instruments"] == 1
    assert result["summary"]["evidence_backed_alias_merges"] == 1
    assert list(result["summary"]["requirements_per_instrument"].values()) == [2]
    assert len(result["associated_findings"]) == 2
    assert len({r["origins"][0]["batch_id"] for r in result["associated_findings"]}) == 2
    proof = result["references"][0]["aliases"][0]
    assert proof["basis"] == "EXPLICIT_SOURCE_EQUIVALENCE" and proof["proof"]["origins"]
    assert all(
        r["semantic_status"] == "SEMANTIC_REVIEW_PENDING" for r in result["associated_findings"]
    )


@pytest.mark.parametrize("between", [" and ", " is similar to ", " (not "])
def test_no_alias_merge_without_equivalence(legal, between):
    core, corpus = legal(
        [
            [requirement(f"Under {REG}{between}{ALIAS}, the supplier must act.")],
            [
                requirement(
                    f"Under {ALIAS}, the supplier must retain a log.",
                    subject=ALIAS,
                    identifier=ALIAS,
                )
            ],
        ]
    )
    result = derive_legal_references(core, corpus)
    assert len(result["references"]) == 2
    assert result["summary"]["evidence_backed_alias_merges"] == 0
    assert result["summary"]["ambiguous_unresolved_references"] == 1


def test_provisions_and_five_requirements_remain_separate(legal):
    records = [
        requirement(f"Under {REG}, supplier must perform synthetic task {i}.", purpose=f"Task {i}")
        for i in range(5)
    ]
    records.append(
        requirement(
            f"Article 4 of {REG} defines what 'record' means.",
            identifier=f"Article 4 of {REG}",
            relationship="DEFINITION",
        )
    )
    core, corpus = legal([records])
    result = derive_legal_references(core, corpus)
    ref = result["references"][0]
    assert len(result["associated_findings"]) == 6
    assert result["summary"]["requirements_per_instrument"][ref["canonical_reference_id"]] == 5
    assert result["summary"]["provisions_per_instrument"][ref["canonical_reference_id"]] == 1
    provision = ref["provisions"][0]
    assert provision["key"] == "article:4" and provision["specificity"] == "SPECIFIC_PROVISION"
    assert provision["parent_reference_id"] == ref["canonical_reference_id"]
    assert any(link["kind"] == "provision_of" for link in result["links"])
    assert result["associated_findings"][-1]["mention_role"] == "DEFINITION"


def test_provision_under_explicitly_defined_alias(legal):
    core, corpus = legal(
        [
            [
                requirement(f"{REG} ({ALIAS}) governs this sample.", relationship="REFERENCE"),
                requirement(
                    f"Article 4 of {ALIAS} defines what 'record' means.",
                    subject=ALIAS,
                    identifier=f"Article 4 of {ALIAS}",
                    relationship="DEFINITION",
                ),
            ]
        ]
    )
    result = derive_legal_references(core, corpus)
    assert len(result["references"]) == 1
    assert any(link["kind"] == "provision_of" for link in result["links"])


def test_ambiguous_fr_preserves_articles_without_expansion(legal):
    core, corpus = legal(
        [
            [
                requirement(
                    "Articles 136(1) and 141 of FR govern the sample.",
                    subject="FR",
                    identifier="Articles 136(1) and 141 of FR",
                    relationship="REFERENCE",
                )
            ]
        ]
    )
    result = derive_legal_references(core, corpus)
    ref = result["references"][0]
    assert ref["specificity"] == "UNRESOLVED_REFERENCE"
    assert ref["unresolved_abbreviations"] == ["FR"]
    assert {p["key"] for p in ref["provisions"]} == {"article:136(1)", "article:141"}
    assert result["associated_findings"][0]["reference_specificity"] == "SPECIFIC_PROVISION"


def test_fr_resolves_only_when_cited_source_explicitly_defines_it(legal):
    instrument = "Regulation (EU) 2099/111"
    core, corpus = legal(
        [
            [
                requirement(
                    f"{instrument} (FR) governs this sample.",
                    subject=instrument,
                    identifier=instrument,
                    relationship="REFERENCE",
                ),
                requirement(
                    "Article 136(1) of FR defines what 'record' means.",
                    subject="FR",
                    identifier="Article 136(1) of FR",
                    relationship="DEFINITION",
                ),
            ]
        ]
    )
    layer = derive_legal_references(core, corpus)
    assert layer["summary"]["distinct_normalized_instruments"] == 1
    assert not layer["references"][0]["unresolved_abbreviations"]
    assert layer["references"][0]["instrument_key"] == "regulation:EU:2099/111"
    assert any(link["kind"] == "provision_of" for link in layer["links"])


def test_repealed_title_is_not_operative_even_if_model_relationship_says_obligation(legal):
    title = f"Under {REG}, repealing {DIRECTIVE}, the supplier must protect records."
    core, corpus = legal(
        [[requirement(title), requirement(title, subject=DIRECTIVE, identifier=DIRECTIVE)]]
    )
    result = derive_legal_references(core, corpus)
    assert result["summary"]["distinct_normalized_instruments"] == 2
    directive = result["associated_findings"][1]
    assert directive["mention_role"] == "HISTORICAL_REPEALED_MENTION"
    assert directive["relationship"] == "OBLIGATION"  # Original remains auditable.
    assert not directive["counted_as_operative_legal_requirement"]
    assert result["summary"]["historical_repealed_mentions"] == 1


def test_title_alias_belongs_to_main_instrument_not_repealed_directive(legal):
    title = f"{REG} on synthetic record protection and repealing {DIRECTIVE} ({ALIAS})"
    core, corpus = legal(
        [
            [
                requirement(title, relationship="REFERENCE"),
                requirement(
                    title, subject=DIRECTIVE, identifier=DIRECTIVE, relationship="REFERENCE"
                ),
            ],
            [
                requirement(
                    f"Under {ALIAS}, the supplier must act.", subject=ALIAS, identifier=ALIAS
                )
            ],
        ]
    )
    result = derive_legal_references(core, corpus)
    identities = {r["instrument_key"]: r for r in result["references"]}
    assert len(identities) == 2
    assert identities["regulation:EU:2016/679"]["aliases"][0]["alias"] == ALIAS
    assert identities["directive:EC:95/46"]["aliases"] == []
    assert result["associated_findings"][1]["mention_role"] == "HISTORICAL_REPEALED_MENTION"


def test_repealed_and_independently_operative_clauses_flag_conflict(legal):
    core, corpus = legal(
        [[requirement(f"{DIRECTIVE} is repealed.", subject=DIRECTIVE, identifier=DIRECTIVE)]]
    )
    finding = core["findings"][0]
    extra = copy.deepcopy(finding["evidence"][0])
    extra["citation"]["quote"] = f"Under {DIRECTIVE}, the supplier must file a report."
    corpus.pages[extra["citation"]["document_id"]][1] += extra["citation"]["quote"]
    finding["evidence"].append(extra)
    result = derive_legal_references(core, corpus)
    assert result["associated_findings"][0]["mention_role"] == "UNRESOLVED_MENTION"
    assert not result["associated_findings"][0]["counted_as_operative_legal_requirement"]


def test_unspecified_laws_are_not_counted_as_operative_instruments(legal):
    core, corpus = legal(
        [
            [
                requirement(
                    "Union or Member State law must be respected.",
                    subject="Union or Member State law",
                    identifier="Union or Member State law",
                )
            ]
        ]
    )
    result = derive_legal_references(core, corpus)
    assert result["summary"]["distinct_normalized_instruments"] == 0
    assert result["summary"]["unspecified_legal_references"] == 1
    assert not result["associated_findings"][0]["counted_as_operative_legal_requirement"]


def test_unsupported_named_law_stays_unresolved_rather_than_unspecified(legal):
    name = "Synthetic Public Records Law 2099"
    core, corpus = legal(
        [
            [
                requirement(
                    f"Under {name}, the supplier must retain a log.", subject=name, identifier=name
                )
            ]
        ]
    )
    layer = derive_legal_references(core, corpus)
    assert layer["references"][0]["specificity"] == "UNRESOLVED_REFERENCE"
    assert layer["summary"]["unspecified_legal_references"] == 0


def test_nonlegal_atomic_records_remain_separately_auditable(legal):
    core, corpus = legal(
        [[requirement("The meeting is scheduled on Monday.", kind="OTHER", relationship="OTHER")]]
    )
    layer = derive_legal_references(core, corpus)
    assert layer["references"] == []
    assert layer["associated_findings"][0]["canonical_reference_id"] is None
    assert layer["associated_findings"][0]["original_atomic_records"] == core["raw_records"]


@pytest.mark.parametrize(
    "connector,relationship,kind",
    [("unless", "CONDITION", "exception_to"), ("if", "CONDITION", "condition_of")],
)
def test_explicit_subordinate_clause_links_to_parent(legal, connector, relationship, kind):
    child = f"{connector} a competent court prohibits notification"
    parent = f"Under {REG}, the supplier must notify transfers {child}."
    core, corpus = legal(
        [
            [
                requirement(parent, purpose="Notify"),
                requirement(child, purpose="Restriction", relationship=relationship),
            ]
        ]
    )
    result = derive_legal_references(core, corpus)
    matches = [link for link in result["links"] if link["kind"] == kind]
    assert len(matches) == 1
    assert matches[0]["to_finding_id"] == result["associated_findings"][0]["finding_id"]
    assert matches[0]["provenance"]["child"]["citation"]["quote"] == child


def test_shared_instrument_and_page_do_not_authorize_exception_link(legal):
    core, corpus = legal(
        [
            [
                requirement(),
                requirement("unless a court forbids notification", relationship="CONDITION"),
            ]
        ]
    )
    result = derive_legal_references(core, corpus)
    assert not any(link["kind"] == "exception_to" for link in result["links"])
    assert any(link["kind"] == "exception_to" for link in result["unresolved_links"])


def test_conflicting_identifiers_never_fuzzy_merge(legal):
    core, corpus = legal(
        [
            [
                requirement(subject="Synthetic Record Regulation"),
                requirement(
                    "Under Regulation (EU) 2016/678, supplier must file a report.",
                    identifier="Regulation (EU) 2016/678",
                    subject="Synthetic Record Regulation",
                ),
            ]
        ]
    )
    result = derive_legal_references(core, corpus)
    assert result["summary"]["distinct_normalized_instruments"] == 2
    assert any(i["kind"] == "SAME_WRITTEN_NAME_DIFFERENT_IDENTITIES" for i in result["issues"])


def test_conflicting_subject_and_identifier_are_unresolved_even_with_both_in_quote(legal):
    core, corpus = legal(
        [
            [
                requirement(
                    f"{REG} and {DIRECTIVE} are mentioned in this sample.",
                    subject=DIRECTIVE,
                    identifier=REG,
                    relationship="REFERENCE",
                )
            ]
        ]
    )
    layer = derive_legal_references(core, corpus)
    assert layer["summary"]["distinct_normalized_instruments"] == 0
    assert layer["associated_findings"][0]["uncertainty"] == ["conflicting_instrument_identities"]


def test_identifiers_cannot_be_invented_by_extraction(legal):
    core, corpus = legal([[requirement("The supplier must provide an audit report.")]])
    result = derive_legal_references(core, corpus)
    assert result["summary"]["distinct_normalized_instruments"] == 0
    assert result["summary"]["ambiguous_unresolved_references"] == 1


@pytest.mark.parametrize("change", ["page", "hash", "quote", "document"])
def test_bad_citation_is_visible_and_cannot_support_identity(legal, change):
    core, corpus = legal([[requirement()]])
    citation = core["findings"][0]["evidence"][0]["citation"]
    citation[
        {"page": "page_number", "hash": "pdf_sha256", "quote": "quote", "document": "document_id"}[
            change
        ]
    ] = {"page": 9, "hash": "f" * 64, "quote": "Invented quote", "document": "unknown"}[change]
    result = derive_legal_references(core, corpus)
    assert result["associated_findings"][0]["citation_status"] == "CITATION_INVALID"
    assert result["summary"]["distinct_normalized_instruments"] == 0
    assert not result["links"]


def test_incomplete_coverage_and_empty_citation_display(legal):
    core, corpus = legal([[], [requirement()]], incomplete=True)
    result = derive_legal_references(core, corpus)
    assert result["source_coverage"]["status"] == "INCOMPLETE"
    assert result["source_coverage"]["source_pages_completed"] == 1
    assert result["source_coverage"]["missing_batches"] == ["batch-1"]
    assert result["summary"]["no_citations_applicable_batches"] == 1
    assert not result["associated_findings"]
    assert (
        citation_status({"empty_batch": True, "citations": [], "citation_status": "VERIFIED_QUOTE"})
        == "NO_CITATIONS_APPLICABLE"
    )


def test_identity_normalization_is_formatting_only():
    assert (
        instrument_matches("Regulation ( EU ) 2016 / 679")[0]["key"]
        == instrument_matches(REG)[0]["key"]
    )
    assert (
        instrument_matches("Directive 95 / 46 / EC")[0]["key"]
        == instrument_matches(DIRECTIVE)[0]["key"]
    )
    assert instrument_matches("Regulation (EU) 2016/679/EC") == []
    assert provisions("Article 4(1)(a)")[0]["key"] != provisions("Article 4(1)(A)")[0]["key"]
    assert instrument_matches("Regulation 2016/679")[0]["key"] != instrument_matches(REG)[0]["key"]
    assert (
        instrument_matches("Directive (EU) 2016/679")[0]["key"] != instrument_matches(REG)[0]["key"]
    )
    assert provisions("article 4 ( 1 ) ( A )")[0]["key"] == "article:4(1)(A)"


def test_ambiguous_parent_and_missing_instrument_links_are_reported(legal):
    child = "unless a court prohibits notification"
    records = [
        requirement(f"{REG}: supplier must notify {child}.", purpose="Notify"),
        requirement(f"{REG}: supplier must record reasons {child}.", purpose="Record"),
        requirement(child, relationship="CONDITION", purpose="Restriction"),
    ]
    core, corpus = legal([records])
    layer = derive_legal_references(core, corpus)
    assert not any(link["kind"] == "exception_to" for link in layer["links"])
    assert any(link["reason"] == "ambiguous_parent" for link in layer["unresolved_links"])
    assert any(link["kind"] == "obligation_under" for link in layer["unresolved_links"])


def association(core, **changes):
    return {
        "alias": ALIAS,
        "instrument_identifier": REG,
        "author_alias": "annotator-a",
        "reviewer_alias": "reviewer-b",
        "reviewed_by_human": True,
        "reviewed_at": "2026-10-12T12:00:00Z",
        "review_notes": "Synthetic human declaration only.",
        "original_response_sha256s": [core["findings"][0]["origins"][0]["response_file_sha256"]],
        **changes,
    }


def test_reviewed_mapping_is_separate_hash_bound_and_preserves_paid_content(legal):
    core, corpus = legal(
        [
            [
                requirement(
                    f"Under {ALIAS}, the supplier must file a report.",
                    subject=ALIAS,
                    identifier=ALIAS,
                )
            ]
        ]
    )
    mapping = {
        "schema_version": "legal-reference-alias-map-1",
        "consolidation_sha256": digest(core),
        "provenance": corpus.provenance,
        "aliases": [association(core)],
    }
    before = copy.deepcopy(core)
    result = derive_legal_references(core, corpus, alias_map=mapping)
    assert result["summary"]["distinct_normalized_instruments"] == 1
    assert result["references"][0]["aliases"][0]["basis"] == "OPERATOR_REVIEWED_MAPPING"
    assert core == before


@pytest.mark.parametrize(
    "bad", ["hash", "provenance", "artifact", "reviewer", "timestamp", "duplicate"]
)
def test_invalid_mapping_is_rejected(legal, bad):
    core, corpus = legal([[requirement()]])
    mapping = {
        "schema_version": "legal-reference-alias-map-1",
        "consolidation_sha256": digest(core),
        "provenance": corpus.provenance,
        "aliases": [association(core)],
    }
    if bad == "hash":
        mapping["consolidation_sha256"] = "f" * 64
    if bad == "provenance":
        mapping["provenance"] = {"family": "other"}
    if bad == "artifact":
        mapping["aliases"][0]["original_response_sha256s"] = ["f" * 64]
    if bad == "reviewer":
        mapping["aliases"][0]["reviewer_alias"] = "annotator-a"
    if bad == "timestamp":
        mapping["aliases"][0]["reviewed_at"] = "2026-10-12T12:00:00"
    if bad == "duplicate":
        mapping["aliases"] *= 2
    with pytest.raises(ValueError):
        derive_legal_references(core, corpus, alias_map=mapping)


def test_conflicting_source_and_reviewed_aliases_remain_unresolved(legal):
    core, corpus = legal(
        [
            [
                requirement(f"{REG} ({ALIAS}) governs this sample.", relationship="REFERENCE"),
                requirement(
                    f"Under {ALIAS}, the supplier must file a report.",
                    subject=ALIAS,
                    identifier=ALIAS,
                ),
            ]
        ]
    )
    mapping = {
        "schema_version": "legal-reference-alias-map-1",
        "consolidation_sha256": digest(core),
        "provenance": corpus.provenance,
        "aliases": [association(core, instrument_identifier="Regulation (EU) 2016/678")],
    }
    result = derive_legal_references(core, corpus, alias_map=mapping)
    assert any(i["kind"] == "CONFLICTING_ALIAS_IDENTITIES" for i in result["issues"])
    assert result["associated_findings"][1]["uncertainty"] == ["conflicting_instrument_identities"]


def test_independent_meaning_review_does_not_create_a_semantic_accuracy_score(legal):
    core, corpus = legal([[requirement()]])
    layer = derive_legal_references(core, corpus)
    review = review_template(layer)
    assert review["reviews"][0]["status"] == "DRAFT"
    review["reviews"][0].update(
        status="VERIFIED",
        author_alias="annotator-a",
        reviewer_alias="reviewer-b",
        reviewed_at="2026-10-12T12:00:00Z",
        review_notes="Synthetic independent review.",
    )
    result = derive_legal_references(core, corpus, meaning_reviews=review)
    assert result["associated_findings"][0]["semantic_status"] == "HUMAN_VERIFIED_MEANING"
    assert result["summary"]["findings_needing_human_review"] == 0
    assert result["semantic_accuracy"] == "NOT_SCORED"
    assert result["reviewed_source_span_coverage"] == "SEPARATE_BENCHMARK_MEASURE"


@pytest.mark.parametrize("status", ["REJECTED", "UNCERTAIN"])
def test_negative_human_meaning_review_excludes_operative_counts(legal, status):
    core, corpus = legal([[requirement()]])
    layer = derive_legal_references(core, corpus)
    review = review_template(layer)
    review["reviews"][0].update(
        status=status,
        author_alias="annotator-a",
        reviewer_alias="reviewer-b",
        reviewed_at="2026-10-12T12:00:00Z",
        review_notes="Synthetic disputed interpretation.",
    )
    revised = derive_legal_references(core, corpus, meaning_reviews=review)
    assert list(revised["summary"]["requirements_per_instrument"].values()) == [0]
    assert revised["associated_findings"][0]["original_atomic_records"] == core["raw_records"]


@pytest.mark.parametrize("bad", ["reviewer", "artifact", "unknown", "duplicate", "timestamp"])
def test_meaning_review_must_target_original_artifacts_and_independent_humans(legal, bad):
    core, corpus = legal([[requirement()]])
    review = review_template(derive_legal_references(core, corpus))
    row = review["reviews"][0]
    row.update(
        status="VERIFIED",
        author_alias="annotator-a",
        reviewer_alias="reviewer-b",
        reviewed_at="2026-10-12T12:00:00Z",
        review_notes="Synthetic review.",
    )
    if bad == "reviewer":
        row["reviewer_alias"] = "annotator-a"
    if bad == "artifact":
        row["original_response_sha256s"] = ["f" * 64]
    if bad == "unknown":
        row["finding_id"] = "unknown"
    if bad == "duplicate":
        review["reviews"] *= 2
    if bad == "timestamp":
        row["reviewed_at"] = None
    with pytest.raises(ValueError):
        derive_legal_references(core, corpus, meaning_reviews=review)


def test_core_provenance_mismatch_is_rejected(legal):
    core, corpus = legal([[requirement()]])
    core["provenance"] = {"family": "other"}
    with pytest.raises(ValueError, match="provenance"):
        derive_legal_references(core, corpus)
