# Third-party licenses

TenderCite's own code is Apache-2.0. The original synthetic fixture in `evaluation/gold.json`
is also explicitly Apache-2.0. Neither grant automatically covers dependencies, model weights,
provider services, user uploads or external datasets.

| Component | Role | Declared upstream license |
| --- | --- | --- |
| FastAPI | API | MIT |
| Pydantic / pydantic-settings | Validation/config | MIT |
| pypdf | PDF extraction | BSD-3-Clause |
| HTTPX / Uvicorn / Starlette | HTTP/runtime | BSD-3-Clause |
| Streamlit | UI | Apache-2.0 |
| Chroma | Embedded vector index | Apache-2.0 |
| sentence-transformers / Transformers | Embeddings/runtime | Apache-2.0 |
| PyTorch | CPU tensor runtime | BSD-3-Clause (see bundled third-party notices) |
| NumPy / SciPy / scikit-learn | Numerical dependencies | BSD-3-Clause |
| intfloat/multilingual-e5-small | Default separately downloaded model | MIT according to model card |
| pytest | Tests | MIT |
| Ruff | Lint/format | MIT |
| uv | Installation/build tooling | MIT OR Apache-2.0 |

Model source and declared license: https://huggingface.co/intfloat/multilingual-e5-small .
Revision `614241f622f53c4eeff9890bdc4f31cfecc418b3` was downloaded via the official
Hugging Face mechanism, inspected and loaded with SentenceTransformers on CPU. Its model card/API
declare MIT; the safetensors SHA-256 matches upstream metadata. The default is pinned to that
revision and remains configurable. See the [evaluation report](docs/evaluation/multilingual-e5-synthetic-tender-3.md)
for artifact hashes, configuration and narrow synthetic English/German measurements. Weights
are not redistributed here. Licensing and synthetic results do not establish real-tender accuracy.

Runtime/development lockfiles record exact dependency versions and package hashes. The table
covers principal components, not every transitive bundled artifact. Retain dependency notices
when redistributing images/wheels; inspect exact upstream metadata and base-image packages.
A configured LLM has separate provider/model terms and may impose additional usage conditions.
No LLM weights or third-party procurement PDFs are distributed here. Docling is not installed.
