"""Explicit, bounded retrieval for analysis; no model-generated query expansion."""

from tendercite.domain.analysis import (
    AnalysisRequest,
    AnalysisRetrievalPlan,
    RetrievalQuery,
    RetrievalQueryResult,
)
from tendercite.services.retrieval.base import SearchHit, SearchRequest
from tendercite.services.retrieval.service import RetrievalService

CATEGORY_QUERIES = (
    RetrievalQuery(
        key="deadlines",
        query="Tender submission deadline and clarification dates. "
        "Angebotsfrist, Abgabefrist und Fristen für Bieterfragen.",
    ),
    RetrievalQuery(
        key="eligibility",
        query="Bidder eligibility, reference projects, evidence and certificates. "
        "Eignung, Referenzprojekte, Nachweise und Zertifikate des Bieters.",
    ),
    RetrievalQuery(
        key="financial",
        query="Required turnover, financial standing and insurance coverage. "
        "Mindestumsatz, finanzielle Leistungsfähigkeit und Haftpflichtversicherung.",
    ),
    RetrievalQuery(
        key="technical",
        query="Technical requirements, IT security and personal data protection. "
        "Technische Anforderungen, IT-Sicherheit und Datenschutz.",
    ),
    RetrievalQuery(
        key="award",
        query="Award criteria, scoring, price and quality weighting. "
        "Zuschlagskriterien, Wertung, Preis- und Qualitätsgewichtung.",
    ),
    RetrievalQuery(
        key="contract",
        query="Contract terms, liability, penalties and termination. "
        "Vertragsbedingungen, Haftung, Vertragsstrafen und Kündigung.",
    ),
)
HITS_PER_QUERY = 3


def build_analysis_retrieval_plan(request: AnalysisRequest) -> AnalysisRetrievalPlan:
    queries = (RetrievalQuery(key="user", query=request.query), *CATEGORY_QUERIES)
    return AnalysisRetrievalPlan(
        strategy="category-aware-round-robin",
        version="1",
        queries=queries,
        hits_per_query=HITS_PER_QUERY,
        max_chunks=min(request.top_k, len(queries) * HITS_PER_QUERY),
    )


def retrieve_analysis_context(
    plan: AnalysisRetrievalPlan,
    document_ids: list[str],
    retrieval: RetrievalService,
) -> tuple[list[SearchHit], list[RetrievalQueryResult]]:
    """Round-robin unique hits, retaining maximum score across all query results.

    Query order is fixed, each list sorts by descending score then chunk ID. A
    duplicate does not consume a turn: advance that query to its next unseen hit.
    Cross-query scores never decide which category contributes first.
    """
    ranked = []
    results = []
    strongest: dict[str, SearchHit] = {}
    for query in plan.queries:
        hits = sorted(
            retrieval.search(
                SearchRequest(
                    query=query.query, document_ids=document_ids, top_k=plan.hits_per_query
                )
            ),
            key=lambda hit: (-hit.score, hit.chunk_id),
        )[: plan.hits_per_query]
        ranked.append(iter(hits))
        results.append(
            RetrievalQueryResult(query_key=query.key, chunk_ids=[hit.chunk_id for hit in hits])
        )
        for hit in hits:
            if hit.chunk_id not in strongest or hit.score > strongest[hit.chunk_id].score:
                strongest[hit.chunk_id] = hit

    selected: dict[str, SearchHit] = {}
    while len(selected) < plan.max_chunks:
        previous_count = len(selected)
        for candidates in ranked:
            hit = next((hit for hit in candidates if hit.chunk_id not in selected), None)
            if hit is not None:
                selected[hit.chunk_id] = strongest[hit.chunk_id]
            if len(selected) == plan.max_chunks:
                break
        if len(selected) == previous_count:
            break
    return list(selected.values()), results
