"""Exploratory document metrics over bounded ranked chunks, never source-span metrics."""

from collections import defaultdict
from time import perf_counter

from tendercite.services.retrieval.base import SearchHit, SearchRequest


def rank_documents(hits: list[SearchHit], mapping: dict[str, str]) -> list[dict]:
    """First appearance in supplied similarity order, with a strict family ID allowlist."""
    allowed = set(mapping.values())
    seen, ranked = set(), []
    for chunk_rank, hit in enumerate(hits, 1):
        if hit.document_id not in allowed or mapping.get(hit.document_name) != hit.document_id:
            raise ValueError(
                "Retrieved document does not match selected-family filename/ID mapping"
            )
        if hit.document_id in seen:
            continue
        seen.add(hit.document_id)
        ranked.append(
            {
                "document_id": hit.document_id,
                "document_rank": len(ranked) + 1,
                "first_chunk_rank": chunk_rank,
                "first_chunk_id": hit.chunk_id,
                "score": hit.score,
            }
        )
    return ranked


def case_metrics(expected_ids: list[str], ranking: list[dict], ks: tuple[int, ...]) -> dict:
    expected = set(expected_ids)
    if not expected:
        raise ValueError("Positive-reference metrics require at least one expected document")
    if not ks or any(type(k) is not int or not 1 <= k <= 50 for k in ks):
        raise ValueError("Document k must be between 1 and the production search limit 50")
    ranked = [hit["document_id"] for hit in ranking]
    if len(ranked) != len(set(ranked)):
        raise ValueError("Metrics require a unique-document ranking")
    ranks = {doc_id: ranked.index(doc_id) + 1 if doc_id in ranked else None for doc_id in expected}
    return {
        "expected_document_ranks": dict(sorted(ranks.items())),
        "any_referenced_document_at_k": {
            str(k): bool(expected.intersection(ranked[:k])) for k in ks
        },
        "all_referenced_documents_at_k": {str(k): expected.issubset(ranked[:k]) for k in ks},
        "single_reference_reciprocal_rank": (
            (1 / ranks[expected_ids[0]] if ranks[expected_ids[0]] else 0.0)
            if len(expected_ids) == 1
            else None
        ),
    }


def aggregate(cases: list[dict], ks: tuple[int, ...]) -> dict:
    def summarize(rows):
        singles = [row["single_reference_reciprocal_rank"] for row in rows]
        singles = [value for value in singles if value is not None]
        return {
            "cases": len(rows),
            "any_referenced_document_at_k": {
                str(k): sum(row["any_referenced_document_at_k"][str(k)] for row in rows) / len(rows)
                if rows
                else None
                for k in ks
            },
            "all_referenced_documents_at_k": {
                str(k): sum(row["all_referenced_documents_at_k"][str(k)] for row in rows)
                / len(rows)
                if rows
                else None
                for k in ks
            },
            "single_reference_cases": len(singles),
            "single_reference_mrr": sum(singles) / len(singles) if singles else None,
        }

    groups = defaultdict(list)
    for case in cases:
        groups[case["question_type"]].append(case)
    return {
        "combined": summarize(cases),
        "by_question_type": {kind: summarize(rows) for kind, rows in sorted(groups.items())},
    }


def evaluate_cases(cases, mapping, search, *, chunk_top_k=50, ks=(1, 2, 3)) -> dict:
    results = []
    for case in cases:
        if any(name not in mapping for name in case.source_documents):
            raise ValueError("Expected source document has no indexed filename/ID mapping")
        expected = [mapping[name] for name in case.source_documents]
        request = SearchRequest(
            query=case.question, document_ids=sorted(set(mapping.values())), top_k=chunk_top_k
        )
        started = perf_counter()
        hits = search.search(request)
        seconds = perf_counter() - started
        ranking = rank_documents(hits, mapping)
        metrics = case_metrics(expected, ranking, ks)
        results.append(
            {
                "id": case.id,
                "question": case.question,  # Local ignored output only.
                "question_type": case.question_type,
                "expected_source_documents": case.source_documents,
                "expected_document_ids": expected,
                "retrieved_documents": ranking,
                "retrieved_chunks": [hit.model_dump() for hit in hits],
                "retrieved_chunk_count": len(hits),
                "retrieved_unique_document_count": len(ranking),
                "chunk_pool_limit_reached": len(hits) == chunk_top_k,
                "search_seconds": seconds,
                **metrics,
            }
        )
    return {"cases": results, "metrics": aggregate(results, ks)}
