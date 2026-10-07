# Contributing to TenderCite

Thanks for contributing. TenderCite aims to keep procurement-document analysis auditable and easy to review.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev,retrieval,ui]"
pytest
ruff check .
```

## Pull requests

- Keep changes focused and testable.
- Add or update tests for behavioral changes.
- Do not commit real tender documents unless their redistribution license is clear.
- Do not add model weights to the repository.
- Preserve evidence provenance: page numbers and quoted evidence must never be fabricated.
- Document new external services, models, or data sources and their licenses.

## Commit style

Prefer concise conventional-style commits, for example:

- `feat: add page-aware PDF ingestion`
- `fix: reject evidence quotes not present in source page`
- `docs: document local deployment`
