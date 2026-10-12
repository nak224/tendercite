"""Exact-source validation and conservative offline consolidation; never legal inference."""

import unicodedata
from collections import defaultdict

from evaluation.cloud_llm_contexts import validate_answer
from evaluation.staged_evidence_plan import digest, text_hash
from evaluation.staged_evidence_schemas import PreparedStep, SourceSegment
from tendercite.services.evidence import validate_evidence


def validate_extraction(answer, step, corpus):
    citations, findings, seen = [], [], set()
    identity_valid = answer.question_id == step.question_id and answer.batch_id == step.batch_id
    for finding in answer.findings:
        duplicate_id = finding.finding_id in seen
        seen.add(finding.finding_id)
        evidence_valid = bool(finding.evidence) and not duplicate_id and identity_valid
        for citation in finding.evidence:
            source = next((s for s in step.sources if s.source_id == citation.source_id), None)
            page = corpus.pages.get(citation.document_id, {}).get(citation.page_number)
            checked = validate_evidence(
                document_id=citation.document_id,
                page_number=citation.page_number,
                quote=citation.quote,
                page_text=page or "",
            )
            reason = None
            if not citation.quote.strip():
                reason = "empty_quote"
            elif source is None:
                reason = "source_id_not_supplied_in_batch"
            elif (citation.document_id, citation.pdf_sha256, citation.page_number) != (
                source.document_id,
                source.pdf_sha256,
                source.page_number,
            ):
                reason = "document_hash_or_page_does_not_match_supplied_source"
            elif citation.quote not in source.text:
                reason = "quote_not_verbatim_in_supplied_segment"
            elif page is None or citation.quote not in page:
                reason = "quote_not_verbatim_in_stored_page"
            elif checked.grounding_status != "VERIFIED_QUOTE":
                reason = "source_validator_rejected_quote"
            valid = reason is None
            evidence_valid &= valid
            citations.append(
                {
                    "finding_id": finding.finding_id,
                    "citation": citation.model_dump(),
                    "validation": {
                        **checked.model_dump(mode="json"),
                        "grounding_status": "VERIFIED_QUOTE" if valid else "INVALID_QUOTE",
                    },
                    "failure_reason": reason,
                }
            )
        findings.append(
            {
                "finding_id": finding.finding_id,
                "valid": evidence_valid,
                "failure_reason": "duplicate_finding_id"
                if duplicate_id
                else (
                    "wrong_question_or_batch"
                    if not identity_valid
                    else "missing_or_invalid_evidence"
                    if not evidence_valid
                    else None
                ),
            }
        )
    valid = identity_valid and all(f["valid"] for f in findings)
    return {
        "citation_status": "VERIFIED_QUOTE" if valid else "INVALID_QUOTE",
        "extraction_valid": valid,
        "outcome": answer.status,
        "empty_batch": valid and answer.status == "NO_RELEVANT_FINDINGS",
        "identity_valid": identity_valid,
        "findings": findings,
        "citations": citations,
        "valid_citations": sum(c["failure_reason"] is None for c in citations),
        "invalid_citations": sum(c["failure_reason"] is not None for c in citations),
        "missing_evidence_findings": sum(not f.evidence for f in answer.findings),
        "semantic_correctness": "NOT_INDEPENDENTLY_ESTABLISHED",
    }


def canonical(value):
    # Formatting only: retain punctuation and provision numbers; no fuzzy/legal alias matching.
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def consolidate(manifest, attempts):
    """Keep raw records; failed outputs are quarantined rather than silently accepted as empty."""
    raw_records, findings, completed, empty, failed, missing = [], {}, [], [], [], []
    valid_citations, invalid_citations, missing_citations = 0, 0, 0
    for batch in manifest["batches"]:
        attempt = attempts.get(batch["batch_id"])
        if attempt is None:
            missing.append(batch["batch_id"])
            continue
        record = attempt["record"]
        validation = record.get("citation_validation", {})
        answer = record.get("structured_answer")
        valid_citations += validation.get("valid_citations", 0)
        invalid_citations += validation.get("invalid_citations", 0)
        missing_citations += validation.get("missing_evidence_findings", 0)
        if answer:
            raw_records.extend(
                {
                    "batch_id": batch["batch_id"],
                    "attempt_id": attempt["attempt_id"],
                    "response_file_sha256": attempt.get("response_file_sha256"),
                    "finding": finding,
                    "accepted": attempt["completed"],
                }
                for finding in answer["findings"]
            )
        if not attempt["completed"]:
            failed.append(
                {
                    "batch_id": batch["batch_id"],
                    "generation_status": record.get("generation_status"),
                    "accounting_status": record.get("accounting_status"),
                    "reason": attempt.get("error")
                    or record.get("error")
                    or "Extraction was incomplete or had invalid evidence",
                }
            )
            continue
        completed.append(batch["batch_id"])
        if answer["status"] == "NO_RELEVANT_FINDINGS":
            empty.append(batch["batch_id"])
        for finding in answer["findings"]:
            group = (
                finding["kind"],
                canonical(finding["subject"]),
                canonical(finding["exact_identifier"]),
            )
            key = (
                *group,
                canonical(finding["purpose"]),
                finding["relationship"],
                canonical(finding["uncertainty"] or ""),
            )
            origin = {
                "batch_id": batch["batch_id"],
                "finding_id": finding["finding_id"],
                "attempt_id": attempt["attempt_id"],
                "response_file_sha256": attempt["response_file_sha256"],
            }
            if key not in findings:
                findings[key] = {
                    **{k: v for k, v in finding.items() if k not in ("finding_id", "evidence")},
                    "consolidated_id": "finding-" + digest(key)[:24],
                    "reference_group_id": "reference-" + digest(group)[:24],
                    "evidence": [],
                    "origins": [],
                }
            combined = findings[key]
            combined["origins"].append(origin)
            for citation in finding["evidence"]:
                existing = next(
                    (e for e in combined["evidence"] if e["citation"] == citation), None
                )
                if existing is None:
                    combined["evidence"].append({"citation": citation, "origins": [origin]})
                else:
                    existing["origins"].append(origin)
    groups, identifiers = defaultdict(list), defaultdict(set)
    issues = []
    consolidated = list(findings.values())
    for finding in consolidated:
        groups[(finding["reference_group_id"], canonical(finding["purpose"]))].append(finding)
        identifiers[(finding["kind"], canonical(finding["exact_identifier"]))].add(
            canonical(finding["subject"])
        )
        if finding["uncertainty"]:
            issues.append(
                {
                    "kind": "EXPLICIT_UNCERTAINTY",
                    "finding_id": finding["consolidated_id"],
                    "note": finding["uncertainty"],
                }
            )
    for (group_id, purpose), records in groups.items():
        relationships = {f["relationship"] for f in records}
        if len(relationships) > 1:
            issues.append(
                {
                    "kind": "POTENTIAL_RELATIONSHIP_CONFLICT",
                    "reference_group_id": group_id,
                    "purpose": purpose,
                    "relationships": sorted(relationships),
                    "interpretation": "Human review required; no inferred contradiction",
                }
            )
    for (kind, identifier), names in identifiers.items():
        if len(names) > 1:
            issues.append(
                {
                    "kind": "AMBIGUOUS_IDENTIFIER_NAMES",
                    "record_kind": kind,
                    "identifier": identifier,
                    "subjects": sorted(names),
                    "merged": False,
                }
            )
    coverage = [
        {
            **page,
            "completed_batch_ids": [bid for bid in page["batch_ids"] if bid in completed],
            "complete": all(bid in completed for bid in page["batch_ids"]),
        }
        for page in manifest["pages"]
    ]
    accepted_raw = sum(r["accepted"] for r in raw_records)
    return {
        "schema_version": "staged-evidence-consolidation-1",
        "plan_id": manifest["plan_id"],
        "question_id": manifest["question_id"],
        "provenance": manifest["provenance"],
        "status": "COMPLETE" if len(completed) == len(manifest["batches"]) else "INCOMPLETE",
        "planned_batches": len(manifest["batches"]),
        "completed_batches": len(completed),
        "no_relevant_findings_batches": empty,
        "missing_batches": missing,
        "failed_batches": failed,
        "source_coverage_inventory": coverage,
        "source_pages_planned": len(coverage),
        "source_pages_completed": sum(p["complete"] for p in coverage),
        "raw_finding_count": len(raw_records),
        "accepted_raw_finding_count": accepted_raw,
        "valid_citation_count": valid_citations,
        "invalid_citation_count": invalid_citations,
        "missing_evidence_findings": missing_citations,
        "unique_consolidated_reference_count": len({f["reference_group_id"] for f in consolidated}),
        "consolidated_finding_count": len(consolidated),
        "duplicate_findings_removed": accepted_raw - len(consolidated),
        "raw_records": raw_records,
        "findings": consolidated,
        "issues": issues,
        "api_calls": 0,
        "semantic_correctness": "NOT_ASSESSED",
    }


def prepared_synthesis(manifest, consolidation, corpus):
    if consolidation["status"] != "COMPLETE":
        raise ValueError("Final synthesis requires every planned extraction batch to be valid")
    if (consolidation["plan_id"], consolidation["provenance"]) != (
        manifest["plan_id"],
        corpus.provenance,
    ):
        raise ValueError("Consolidation/source plan provenance mismatch")
    source_rows = {}
    for finding in consolidation["findings"]:
        for evidence in finding["evidence"]:
            c = evidence["citation"]
            source_id = c["source_id"] + ":quote:" + text_hash(c["quote"])
            name = next(
                name for name, doc_id in corpus.mapping.items() if doc_id == c["document_id"]
            )
            page = corpus.pages[c["document_id"]][c["page_number"]]
            if c["quote"] not in page or corpus.documents[name]["sha256"] != c["pdf_sha256"]:
                raise ValueError("Consolidated citation no longer matches its stored source")
            start = page.index(c["quote"])
            source_rows[source_id] = SourceSegment(
                source_id=source_id,
                document_id=c["document_id"],
                document_name=name,
                pdf_sha256=c["pdf_sha256"],
                page_number=c["page_number"],
                chunk_id=None,
                text=c["quote"],
                segment_index=1,
                char_start=start,
                char_end=start + len(c["quote"]),
                text_sha256=text_hash(c["quote"]),
            )
    step_id = digest([manifest["plan_id"], "synthesis", digest(consolidation)])
    original = corpus.questions[manifest["question_id"]]
    return PreparedStep(
        question_id=manifest["question_id"],
        question=original.question,
        question_type=original.question_type,
        strategy="staged_synthesis_v1_" + step_id,
        stage="synthesis",
        step_id=step_id,
        batch_id=None,
        sources=list(source_rows.values()),
        consolidated_findings=consolidation["findings"],
        issues=consolidation["issues"],
    )


def validate_synthesis(answer, step, corpus):
    result = validate_answer(answer, step, corpus)
    # Staged synthesis is stricter than production normalization: verbatim supplied evidence only.
    for item in result["citations"]:
        citation = item["citation"]
        if not any(
            s.document_id == citation["document_id"]
            and s.page_number == citation["page_number"]
            and citation["quote"].strip()
            and citation["quote"] in s.text
            for s in step.sources
        ):
            item["failure_reason"] = "quote_not_verbatim_in_validated_synthesis_evidence"
            item["validation"]["grounding_status"] = "INVALID_QUOTE"
            result["citation_status"] = "INVALID_QUOTE"
    return result
