# Deployment and reproducible development

Use Python 3.12 and a trusted single-user Linux environment. From the repository root:

```bash
python -m pip install uv==0.12.19
uv venv .venv
uv pip install --python .venv/bin/python --torch-backend cpu --require-hashes -r requirements-dev.lock
uv pip install --python .venv/bin/python --no-deps -e .
source .venv/bin/activate
uvicorn tendercite.main:app --host 127.0.0.1 --port 8000 --workers 1
# Second terminal, same virtual environment:
streamlit run frontend/app.py --server.address=127.0.0.1 --server.port=8501
```

`requirements.lock` contains runtime + retrieval + UI dependencies. `requirements-dev.lock`
adds tests/lint. Both target Python 3.12 Linux CPU and include artifact hashes. Installing only
`.[dev]` omits optional retrieval/UI integration dependencies; full validation needs the dev lock.
The first model use downloads separately licensed E5 weights. Set writable `HF_HOME` when
required. Model files are not in the image or package lock. Pin `TENDERCITE_EMBEDDING_REVISION`
to an audited model commit for repeatable live evaluation. Reindex every document after changing
model/revision configuration. `HF_HUB_OFFLINE=1` requires a populated compatible cache.

## Environment variables

| Name | Default / purpose |
| --- | --- |
| TENDERCITE_DATA_DIR | `./data`; SQLite, source PDFs and Chroma |
| TENDERCITE_MAX_UPLOAD_MB | 30, allowed range 1–100 |
| TENDERCITE_MAX_PDF_PAGES | 500 |
| TENDERCITE_MAX_EXTRACTED_CHARS | 2000000 |
| TENDERCITE_RETRIEVAL_ENABLED | true; false explicitly selects ingestion-only mode |
| TENDERCITE_EMBEDDING_MODEL | intfloat/e5-small-v2 |
| TENDERCITE_EMBEDDING_REVISION | unset; set for a pinned model revision |
| TENDERCITE_LLM_BASE_URL | unset; operator-configured local or hosted OpenAI-compatible `/v1` base |
| TENDERCITE_LLM_MODEL | unset; served model identifier |
| TENDERCITE_LLM_API_KEY | optional secret required only by some providers |
| TENDERCITE_API_URL | UI process only; default local API port 8000 |
| HF_HOME | Hugging Face model cache (container: `/app/data/models`) |

The API reads `.env`; UI URL must be exported to the UI process. Changing settings requires
an API restart. Provider URLs cannot contain embedded credentials/query secrets. Use HTTPS
for remote providers. Configure JSON-schema chat-completion support on the model server.
No automatic fallback to a paid provider or simulated production analysis exists.

## Docker Compose

```bash
docker compose up --build
```

The same image runs API/UI as UID 10001. Compose binds host ports to loopback and drops
capabilities. The named `tendercite-data` volume retains PDFs, SQLite, Chroma and model cache.
Set provider variables in the shell/Compose `.env`; they are passed only to the API.
A model running on the host needs a host address reachable from the container: container
`localhost` is not the host. Configure this according to your Docker platform.

Health checks verify liveness, not model readiness. Validate with a text-PDF upload, source
quote checks, search and an explicitly configured analysis. `python -m evaluation.run` exercises
live retrieval; `--analysis` additionally sends the synthetic fixture to the provider.

For networks using a custom trusted CA, the Dockerfile supports an optional BuildKit secret:

```bash
docker build --secret id=ca_bundle,src=/path/to/trusted-ca-bundle.pem \
  --build-arg HTTPS_PROXY --build-arg HTTP_PROXY --build-arg NO_PROXY -t tendercite:local .
```

The CA is mounted only during dependency/build installation. Never disable TLS verification.
In managed cloud environments, preserve the platform Docker proxy defaults and registry
configuration. Supply the provided trusted CA (or combined system bundle) to every networked
build step. Running containers need a read-only CA mount and client-specific trust variables
when using that proxy; rebuild or recreate containers after a session proxy changes.
Runtime provider/model access needs appropriate runtime proxy/CA configuration separately.

## Backup, cleanup and troubleshooting

Stop the API before backing up the entire data directory/volume. It includes confidential source
text and analysis/review history. Referenced sources cannot be deleted individually. Removing
an entire data volume is destructive and must be intentional. Do not run `down -v` casually.

A 503 after upload can mean model/download/index failure: the source is retained and retrying
upload or `/documents/{id}/index` is idempotent. A 422 analysis response means missing context or
invalid structured output; 502 means retrieval/provider failure. Check configuration/connectivity
without logging document bodies or secrets. A 409 deletion protects referenced sources.

The API is a local prototype, not a hardened public service. See SECURITY.md and the release gates.

Compose appends the internal `api` service name to the UI process `NO_PROXY` list so local
UI-to-API traffic stays on the Docker network. External requests keep the configured proxy.
