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
| intfloat/e5-small-v2 | Default separately downloaded model | MIT according to model card |
| pytest | Tests | MIT |
| Ruff | Lint/format | MIT |
| uv | Installation/build tooling | MIT OR Apache-2.0 |

Model source and license: https://huggingface.co/intfloat/e5-small-v2 . Pin and review the exact
revision before shipping weights. The onboarding environment could not download the model due
to its network policy; inspection of a downloaded weight artifact is not claimed. E5-small-v2
is primarily English; licensing does not establish suitability or accuracy.

Runtime/development lockfiles record exact dependency versions and package hashes. The table
covers principal components, not every transitive bundled artifact. Retain dependency notices
when redistributing images/wheels; inspect exact upstream metadata and base-image packages.
A configured LLM has separate provider/model terms and may impose additional usage conditions.
No LLM weights or third-party procurement PDFs are distributed here. Docling is not installed.
