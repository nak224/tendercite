# Contributing

Use the pinned development installation in [deployment.md](docs/deployment.md).
Run `pytest`, `ruff check .` and `ruff format --check .` before committing.
Full tests use SQLite, Chroma, synthetic PDFs and Streamlit AppTest; model adapters are replaced
with deterministic test doubles. No paid API or model-weight download belongs in normal CI.

Keep changes small and retain original model output, page boundaries and server-side quote checks.
Add behavioral tests, document compatibility/security tradeoffs, and use English documentation and
conventional commits. Do not redistribute third-party documents without a clear reuse license.
Never commit credentials, local data or model weights. App keys belong in environment variables.

To refresh Python 3.12 Linux CPU locks using uv 0.12.19:

```bash
uv pip compile pyproject.toml --extra retrieval --extra ui --torch-backend cpu \
  --generate-hashes --emit-index-url --python-version 3.12 -o requirements.lock
uv pip compile pyproject.toml --all-extras --torch-backend cpu \
  --generate-hashes --emit-index-url --python-version 3.12 -o requirements-dev.lock
```

Review dependency changes and licenses, run an advisory audit, clean-install both locks and
rerun tests. A green fake-provider suite is not a live-model benchmark. Re-run the synthetic
live evaluation after changing retrieval, chunking, prompts, schemas or model versions.
