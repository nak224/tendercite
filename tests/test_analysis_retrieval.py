import json
from types import SimpleNamespace

import pytest
from conftest import make_pdf

from tendercite.api.dependencies import get_llm
from tendercite.domain.analysis import AnalysisRequest, AnalysisRun
from tendercite.main import app
from tendercite.services.retrieval.analysis import (
    build_analysis_retrieval_plan,
    retrieve_analysis_context,
)
from tendercite.services.retrieval.base import SearchHit, SearchRequest


class CategoryEmbeddings:
    """Deliberately simple category vectors; tests mechanics, not multilingual quality."""

    model_id = "offline-category-vectors"
    keywords = (
        ("deadline", "angebotsfrist"),
        ("reference", "referenz"),
        ("insurance", "haftpflichtversicherung"),
        ("security", "it-sicherheit"),
        ("scoring", "zuschlagskriterien"),
        ("contract terms", "vertragsbedingungen"),
    )

    def embed_query(self, text):
        category = next(
            (
                i
                for i, keywords in enumerate(self.keywords, start=1)
                if any(word in text.lower() for word in keywords)
            ),
            0,
        )
        return [float(i == category) for i in range(7)]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


def test_plan_is_explicit_bilingual_bounded_and_keeps_user_query():
    plan = build_analysis_retrieval_plan(
        AnalysisRequest(document_ids=["selected"], query="Mein eigener Fokus", top_k=50)
    )
    assert [query.key for query in plan.queries] == [
        "user",
        "deadlines",
        "eligibility",
        "financial",
        "technical",
        "award",
        "contract",
    ]
    assert plan.queries[0].query == "Mein eigener Fokus"
    assert plan.hits_per_query == 3
    assert plan.max_chunks == 21
    assert plan.strategy == "category-aware-round-robin"
    assert plan.version == "1"
    for query, (english, german) in zip(plan.queries[1:], CategoryEmbeddings.keywords, strict=True):
        assert english in query.query.lower()
        assert german in query.query.lower()


def make_hit(chunk_id, score):
    return SearchHit(
        chunk_id=chunk_id,
        document_id="selected",
        document_name="test.pdf",
        page_number=1,
        text=f"Source {chunk_id}",
        score=score,
    )


@pytest.mark.parametrize("budget", [1, 7, 12, 50])
def test_deduplication_max_score_fair_order_and_budget(budget):
    plan = build_analysis_retrieval_plan(AnalysisRequest(document_ids=["selected"], top_k=budget))
    responses = {
        plan.queries[0].query: [make_hit("shared", 0.8), make_hit("user-2", 0.7)],
        plan.queries[1].query: [make_hit("shared", 0.95), make_hit("deadline", 0.6)],
        # Equal scores returned in reverse order must be ordered by chunk ID.
        plan.queries[2].query: [make_hit("ref-b", 0.4), make_hit("ref-a", 0.4)],
    }
    calls = []

    def search(request):
        calls.append(request)
        return responses.get(request.query, [])

    hits, audit = retrieve_analysis_context(plan, ["selected"], SimpleNamespace(search=search))
    expected = ["shared", "deadline", "ref-a", "user-2", "ref-b"][:budget]
    assert [hit.chunk_id for hit in hits] == expected
    assert hits[0].score == 0.95  # stronger score found after its first occurrence
    assert len(hits) <= plan.max_chunks
    assert [call.query for call in calls] == [query.query for query in plan.queries]
    assert all(call.top_k == 3 and call.document_ids == ["selected"] for call in calls)
    assert audit[1].chunk_ids == ["shared", "deadline"]
    assert audit[2].chunk_ids == ["ref-a", "ref-b"]
    assert audit[-1].chunk_ids == []
    again, _ = retrieve_analysis_context(plan, ["selected"], SimpleNamespace(search=search))
    assert again == hits


@pytest.mark.parametrize("language", ["en", "de"])
def test_categories_recover_context_missed_by_broad_query(client, retrieval, language):
    retrieval.embeddings = CategoryEmbeddings()
    pages = {
        "en": [
            "The submission deadline is 30 November 2026.",
            "Two comparable reference projects are required.",
            "Liability insurance must cover EUR 1000000 per claim.",
            "IT security requires multifactor authentication for administrators.",
            "Award scoring gives price a weight of 40 percent.",
            "Contract terms allow termination after repeated service failures.",
        ],
        "de": [
            "Die Angebotsfrist endet am 30. November 2026.",
            "Zwei vergleichbare Referenzprojekte sind nachzuweisen.",
            "Die Haftpflichtversicherung muss 1000000 EUR je Schadensfall decken.",
            "Die IT-Sicherheit verlangt Mehrfaktor-Authentisierung für Administratoren.",
            "Die Zuschlagskriterien gewichten den Preis mit 40 Prozent.",
            "Die Vertragsbedingungen erlauben die Kündigung bei wiederholten Ausfällen.",
        ],
    }[language]

    def upload(name, texts):
        response = client.post(
            "/api/v1/documents", files={"file": (name, make_pdf(texts), "application/pdf")}
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    # Eight generic passages crowd all seven hits out of the former broad search.
    overview = upload("overview.pdf", [f"Tender administrative overview {i}." for i in range(8)])
    conditions = upload("conditions.pdf", pages[:3])
    annex = upload("annex.pdf", pages[3:])
    unrelated = upload("unselected.pdf", ["Other contract terms: excluded from this package."])
    request = AnalysisRequest(
        document_ids=[overview, conditions, annex], query="Summarize the tender package", top_k=7
    )
    old_hits = retrieval.search(SearchRequest(**request.model_dump()))
    assert len(old_hits) == 7
    assert {hit.document_id for hit in old_hits} == {overview}

    class CapturingLLM:
        provider = "fake"
        model = "offline"

        async def generate_structured(self, *, user_prompt, response_model, **kwargs):
            self.prompt = json.loads(user_prompt)
            return response_model(findings=[])

    llm = CapturingLLM()
    app.dependency_overrides[get_llm] = lambda: llm
    response = client.post("/api/v1/analyses", json=request.model_dump())
    assert response.status_code == 201, response.text
    run = response.json()
    sources = llm.prompt["sources"]
    assert llm.prompt["task"] == request.query
    assert len(sources) == len(run["retrieved_chunk_ids"]) == 7
    assert len(set(run["retrieved_chunk_ids"])) == 7
    assert all(any(text in hit["text"] for hit in sources) for text in pages)
    assert {hit["document_id"] for hit in sources} == {overview, conditions, annex}
    assert unrelated not in {hit["document_id"] for hit in sources}
    assert [hit["chunk_id"] for hit in sources] == run["retrieved_chunk_ids"]
    assert len(run["retrieval_plan"]["queries"]) == len(run["retrieval_query_results"]) == 7
    assert run["retrieval_plan"]["queries"][0]["query"] == request.query
    assert run["retrieval_plan"]["max_chunks"] == 7
    assert set(run["retrieved_chunk_ids"]) <= {
        chunk_id for result in run["retrieval_query_results"] for chunk_id in result["chunk_ids"]
    }
    assert client.get("/api/v1/analyses/" + run["id"]).json() == run

    # Historical JSON snapshots remain readable without invented retrieval metadata.
    run.pop("retrieval_plan")
    run.pop("retrieval_query_results")
    legacy = AnalysisRun.model_validate(run)
    assert legacy.retrieval_plan is None
    assert legacy.retrieval_query_results == []


@pytest.mark.parametrize("budget", [1, 7, 12, 50])
def test_full_plan_keeps_category_turns_and_caps_context(budget):
    plan = build_analysis_retrieval_plan(AnalysisRequest(document_ids=["selected"], top_k=budget))
    responses = {
        query.query: [make_hit(f"{query.key}-{rank}", 1 - rank / 10) for rank in range(3)]
        for query in plan.queries
    }
    hits, _ = retrieve_analysis_context(
        plan, ["selected"], SimpleNamespace(search=lambda request: responses[request.query])
    )
    expected = [f"{query.key}-{rank}" for rank in range(3) for query in plan.queries]
    assert [hit.chunk_id for hit in hits] == expected[:budget]
    assert len(hits) == min(budget, 21)


def test_no_context_does_not_call_model_or_save_analysis(client, retrieval):
    doc = client.post(
        "/api/v1/documents",
        files={"file": ("empty-index.pdf", make_pdf(["Text"]), "application/pdf")},
    ).json()
    retrieval.store.delete_document(doc["id"])

    class UnusedLLM:
        async def generate_structured(self, **kwargs):
            pytest.fail("Model must not be called without retrieved context")

    app.dependency_overrides[get_llm] = UnusedLLM
    response = client.post("/api/v1/analyses", json={"document_ids": [doc["id"]]})
    assert response.status_code == 422
    assert retrieval.repository.list_findings() == []


@pytest.mark.parametrize(
    "mode",
    ["valid", "wrong_document", "wrong_page", "unselected_chunk", "outside_chunk", "changed_page"],
)
def test_plan_preserves_all_evidence_boundaries(client, retrieval, monkeypatch, mode):
    quote = "Two reference projects are required."
    later_quote = "Insurance cover is mandatory."
    text = quote + "\n" + "Historical archive navigation information. " * 45 + "\n" + later_quote
    document = client.post(
        "/api/v1/documents",
        files={"file": ("sources.pdf", make_pdf([text, "Other source page."]), "application/pdf")},
    ).json()
    other = client.post(
        "/api/v1/documents",
        files={"file": ("other.pdf", make_pdf([quote + " Other tender."]), "application/pdf")},
    ).json()
    chunks = retrieval.repository.list_chunks(document["id"])
    later_chunk = next(chunk for chunk in chunks if later_quote in chunk.text)

    class EvidenceLLM:
        model = "offline-evidence-boundaries"

        async def generate_structured(self, *, user_prompt, response_model, **kwargs):
            sources = json.loads(user_prompt)["sources"]
            assert len(sources) == 1
            hit = sources[0]
            assert quote in hit["text"]
            assert later_quote not in hit["text"]
            evidence = {key: hit[key] for key in ("chunk_id", "document_id", "page_number")}
            evidence["quote"] = quote
            if mode == "wrong_document":
                evidence["document_id"] = other["id"]
            elif mode == "wrong_page":
                evidence["page_number"] = 2
            elif mode == "unselected_chunk":
                evidence["chunk_id"] = later_chunk.id
                evidence["quote"] = later_quote
            elif mode == "outside_chunk":
                evidence["quote"] = later_quote
            return response_model.model_validate(
                {"findings": [{"statement": quote, "category": "MUST", "evidence": [evidence]}]}
            )

    if mode == "changed_page":
        original = retrieval.repository.get_page
        monkeypatch.setattr(
            retrieval.repository,
            "get_page",
            lambda doc_id, page: original(doc_id, page).model_copy(update={"text": "Changed."}),
        )
    app.dependency_overrides[get_llm] = EvidenceLLM
    response = client.post(
        "/api/v1/analyses",
        json={"document_ids": [document["id"]], "query": "reference", "top_k": 1},
    )
    assert response.status_code == 201, response.text
    expected = "VERIFIED_QUOTE" if mode == "valid" else "INVALID_QUOTE"
    assert response.json()["findings"][0]["grounding_status"] == expected
    # A candidate can appear in per-query audit results without entering the model context.
    assert later_chunk.id in {
        chunk_id
        for result in response.json()["retrieval_query_results"]
        for chunk_id in result["chunk_ids"]
    }
    assert later_chunk.id not in response.json()["retrieved_chunk_ids"]
