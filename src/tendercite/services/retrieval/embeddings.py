class SentenceTransformerEmbeddingProvider:
    """E5 uses separate query/passage prefixes and unit-length vectors on CPU."""

    def __init__(self, model_id: str = "intfloat/e5-small-v2", revision: str | None = None):
        self.model_id = model_id
        self.revision = revision
        self._model = None

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_id, revision=self.revision, device="cpu", trust_remote_code=False
            )
        return self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(["passage: " + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode(["query: " + text])[0]
