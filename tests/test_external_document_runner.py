"""Offline runner checks; real parsing/Chroma with fake embeddings, mocked HF only."""

import json
import sys
from types import SimpleNamespace

import pytest
from test_external_document_metrics import corpus as corpus
from test_external_document_metrics import prepare
from test_retrieval import FakeEmbeddings

from evaluation import external_document_retrieval as runner
from tendercite.services.retrieval.base import SearchRequest
from tendercite.services.retrieval.chroma import ChromaVectorStore


def test_real_components_index_all_parseable_family_pdfs_together(corpus):
    prepared = prepare(corpus)
    store = ChromaVectorStore(corpus.parent / "run/chroma", "test-documents")
    search, mapping, documents = runner.index_documents(
        prepared, corpus.parent / "run", FakeEmbeddings(), store
    )
    assert set(mapping) == {"notice.pdf", "contract.pdf"}
    assert set(mapping.values()) == {document.id for document in search.repository.list_documents()}
    assert sum(document["pages"] for document in documents) == 3
    assert store.collection.count() == sum(document["chunks"] for document in documents) == 3
    hits = search.search(
        SearchRequest(query="reference", document_ids=list(mapping.values()), top_k=50)
    )
    assert set(hit.document_id for hit in hits) == set(mapping.values())
    assert all(mapping[hit.document_name] == hit.document_id for hit in hits)
    reopened = ChromaVectorStore(corpus.parent / "run/chroma", "test-documents")
    assert reopened.collection.count() == 3
    second_store = ChromaVectorStore(corpus.parent / "second/chroma", "test-documents")
    _, second_mapping, _ = runner.index_documents(
        prepared, corpus.parent / "second", FakeEmbeddings(), second_store
    )
    assert mapping == second_mapping


def test_hash_is_rechecked_before_indexing(corpus):
    prepared = prepare(corpus)
    next(iter(prepared.documents.values()))["local_path"].write_bytes(b"modified")
    store = ChromaVectorStore(corpus.parent / "run/chroma", "test-documents")
    with pytest.raises(ValueError, match="size"):
        runner.index_documents(prepared, corpus.parent / "run", FakeEmbeddings(), store)
    assert store.collection.count() == 0


@pytest.mark.parametrize("failure", ["missing_weight", "wrong_revision", "wrong_checksum"])
def test_missing_or_unverified_model_artifacts_never_produce_metrics(corpus, monkeypatch, failure):
    cache = corpus.parent / "model-cache"
    snapshot = cache / "models--intfloat--multilingual-e5-small/snapshots" / runner.MODEL_REVISION
    snapshot.mkdir(parents=True)
    if failure == "wrong_checksum":
        (snapshot / "model.safetensors").write_bytes(b"original test bytes")
    calls = []

    def model_info(model, **kwargs):
        assert model == runner.MODEL
        assert kwargs["revision"] == runner.MODEL_REVISION
        assert kwargs["files_metadata"]
        return SimpleNamespace(
            sha="b" * 40 if failure == "wrong_revision" else runner.MODEL_REVISION,
            siblings=[
                SimpleNamespace(rfilename="model.safetensors", lfs=SimpleNamespace(sha256="a" * 64))
            ],
        )

    def snapshot_download(model, **kwargs):
        calls.append(kwargs)
        assert kwargs["revision"] == runner.MODEL_REVISION
        assert kwargs["cache_dir"] == cache
        assert kwargs["token"] is False
        assert "verify" not in kwargs and "endpoint" not in kwargs
        return str(snapshot)

    monkeypatch.setenv("HF_XET_CACHE", str(cache / ".xet-cache"))
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(
            HfApi=lambda **kwargs: SimpleNamespace(model_info=model_info),
            constants=SimpleNamespace(HF_XET_CACHE=str(cache / ".xet-cache")),
            snapshot_download=snapshot_download,
        ),
    )
    with pytest.raises((FileNotFoundError, ValueError)):
        runner.load_model(cache)
    assert len(calls) == (0 if failure == "wrong_revision" else 1)


def test_missing_dataset_is_recorded_without_model_loading(corpus):
    output = corpus.parent / "failed-run"
    record = runner.run(corpus.parent / "absent", output, corpus.parent / "cache")
    assert record["status"] == "failed"
    assert record["failed_stage"] == "case_preparation"
    assert record["failure"]["error_type"] == "FileNotFoundError"
    assert "metrics" not in record
    assert json.loads((output / "results.json").read_text())["status"] == "failed"


def test_model_blocker_is_reported_without_fabricated_scores(corpus, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            set_num_threads=lambda _: None,
            set_num_interop_threads=lambda _: None,
        ),
    )
    monkeypatch.setattr(runner, "prepare_cases", lambda _: prepare(corpus))

    def blocked(_):
        raise RuntimeError(
            "blocked by proxy https://cas-server.xethub.hf.co/download?secret=redacted"
        )

    monkeypatch.setattr(runner, "load_model", blocked)
    record = runner.run(corpus, corpus.parent / "failed-model", corpus.parent / "cache")
    assert record["failed_stage"] == "model_download_or_loading"
    assert record["failure"]["hostname"] == "cas-server.xethub.hf.co"
    assert record["failure"]["proxy_denial_observed"]
    assert "secret=" not in record["failure"]["error"]
    assert "metrics" not in record and "model" not in record
    assert record["llm_called"] is False


def test_cli_requires_explicit_real_model_opt_in(corpus):
    with pytest.raises(SystemExit) as exc:
        runner.main(
            [
                "--acquisition-dir",
                str(corpus),
                "--output-dir",
                str(corpus.parent / "run"),
                "--model-cache",
                str(corpus.parent / "cache"),
            ]
        )
    assert exc.value.code == 2


def test_existing_output_is_preserved(corpus):
    output = corpus.parent / "existing"
    output.mkdir()
    original = output / "results.json"
    original.write_text("original test observation")
    with pytest.raises(FileExistsError):
        runner.run(corpus, output, corpus.parent / "cache")
    assert original.read_text() == "original test observation"


def test_same_pdf_hash_preserves_filename_alias_mapping(corpus):
    from test_external_benchmark import FAMILY, REVISION, FakeHub

    from evaluation import external_benchmark as acquisition

    hub = FakeHub()
    hub.data[f"data/{FAMILY}/copy.pdf"] = hub.data[f"data/{FAMILY}/notice.pdf"]
    directory = corpus.parent / "duplicates"
    acquisition.acquire(REVISION, directory, family=FAMILY, download_pdfs=True, source=hub)
    prepared = prepare(directory)
    store = ChromaVectorStore(corpus.parent / "run/chroma", "test-documents")
    search, mapping, _ = runner.index_documents(
        prepared, corpus.parent / "run", FakeEmbeddings(), store
    )
    assert mapping["copy.pdf"] == mapping["notice.pdf"]
    assert len(search.repository.list_documents()) == 2
    assert store.collection.count() == 3


def test_empty_eligible_dataset_does_not_load_model_or_claim_zero_score(corpus, monkeypatch):
    prepared = prepare(corpus)
    prepared.eligible = []
    monkeypatch.setattr(runner, "prepare_cases", lambda _: prepared)
    record = runner.run(corpus, corpus.parent / "empty-run", corpus.parent / "cache")
    assert record["failed_stage"] == "case_preparation"
    assert "No eligible" in record["failure"]["error"]
    assert "metrics" not in record
