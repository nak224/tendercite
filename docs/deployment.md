# Deployment

## Local API

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
uvicorn tendercite.main:app --reload
```

Open `http://localhost:8000/docs` for Swagger UI.

## API + UI with Docker Compose

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- UI: `http://localhost:8501`

## Retrieval extras

Semantic retrieval is intentionally optional in v0.1:

```bash
pip install -e ".[retrieval]"
```

The planned default uses local sentence-transformer embeddings and local Chroma persistence, so retrieval does not require a hosted AI service.

## LLM provider configuration

The provider layer uses an OpenAI-compatible HTTP contract. A local or hosted compatible server can be selected by environment variables. Do not commit API keys.

```bash
export TENDERCITE_LLM_BASE_URL=http://localhost:11434/v1
export TENDERCITE_LLM_MODEL=<model-name>
export TENDERCITE_LLM_API_KEY=<optional-key>
```

No LLM call is required for the v0.1 ingestion/evidence-validation slice.
