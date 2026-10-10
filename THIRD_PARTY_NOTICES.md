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

The [TED pilot](docs/evaluation/ted-pilot.md) uses public notice metadata from TED / the
Publications Office of the European Union and submitting contracting authorities. The
[candidate manifest](evaluation/ted-pilot/candidates.json) records official sources, dates,
languages and original metadata-based curation notes. Notice content is not covered by
TenderCite's Apache-2.0 grant. Review the [TED legal notice](https://ted.europa.eu/en/legal-notice),
source attribution, personal-data and third-party rights before redistributing a corpus.
PDF/XML downloads default to ignored local storage; none was obtained or redistributed in this
pilot due to TED WAF challenges. Offline TED test fixtures are original synthetic responses and
generated documents, not copies of real procurement documents.

The [manual PDF corpus importer](docs/evaluation/real-pdf-import.md) likewise leaves downloaded
bytes outside Git and flags missing/unverified provenance and rights. A curator's verification
notes must describe the actual source and reuse basis; importing a public PDF does not grant
redistribution rights. Its automated fixtures are original generated PDFs, and no real procurement
PDFs are distributed with this milestone.

The [external EU Tenders QA pilot](docs/evaluation/external-benchmark-pilot.md) records the upstream
card's MIT declaration for `tmskss/eu-tenders-with-questions-for-agentic-checklist-filling` at a
pinned revision. It does not verify rights to the underlying procurement PDFs, redistribution
permissions, attribution or personal data. The card describes LLM-based curation, without evidence
of independent human gold review. Raw QA/PDF content remains ignored local data; the committed
report contains our validation observations, filenames, IDs and hashes, with no source answers
or PDF text. TenderCite's Apache-2.0 grant does not cover those external source documents.
