# Third-party license notes

TenderCite's own source code is licensed under Apache-2.0. That license does **not** automatically apply to dependencies, model weights, user-supplied documents, or example datasets.

The planned/default stack was selected to favor permissive licensing:

| Component | Role | License note |
|---|---|---|
| FastAPI | REST API | MIT |
| Pydantic / pydantic-settings | Validation/config | MIT |
| pypdf | PDF text extraction | BSD-3-Clause |
| Streamlit | UI | Apache-2.0 |
| Chroma | Local vector store | Apache-2.0 |
| sentence-transformers | Embedding runtime | Apache-2.0 |
| `intfloat/e5-small-v2` | Default embedding model candidate | MIT model card |
| Docling | Optional advanced parser candidate | MIT codebase; individual models may have separate licenses |

This table is a project-maintainer aid, not a substitute for checking the exact version and transitive dependencies before a release or redistribution.

## Model policy

Model identifiers, licenses and source URLs must be documented separately from the repository license. TenderCite must not state or imply that Apache-2.0 covers third-party model weights.
