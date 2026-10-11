# Cloud LLM comparison foundation (Milestone 3B-d1)

This milestone prepares an offline comparison of recorded E5 retrieval plus a cloud LLM
(`e5_rag`) against complete parsed document context plus the same LLM (`full_context`).
It builds requests and checks declared context/cost budgets. **Paid execution is disabled**,
including with `--execute`. No answer accuracy or provider billing measurements are available.

## Shared requests and source validation

The evaluation modules reuse the `StructuredLLM` contract and existing OpenAI-compatible
HTTP adapter. Its new optional temperature, output-token and timeout settings preserve existing
production defaults. A pure `build_payload` method builds the same JSON payload used by the
adapter, without creating an HTTP client. Production retrieval, APIs, UI and human annotations
are unchanged; no additional provider, tokenizer dependency or model download is introduced.

Both arms use the original nine eligible questions, identical instructions, one `QAAnswer`
schema, and the same configured model, temperature and maximum output tokens:

| Strategy | Input selection |
| --- | --- |
| `e5_rag` | First `rag_top_k` recorded ranked chunks per question, initially 12, configurable from 1 to 50; original ranks and document/page/chunk IDs retained. No E5 rerun |
| `full_context` | Every parsed page of all three parseable family documents, ordered by filename and PDF page. Includes the annex PDF even though eligible candidate labels do not reference it. No silent truncation or reference-label filtering |

Full-context source IDs are `<document_id>:page:<1-based PDF page>`; RAG source IDs are the
original chunk IDs. Both retain filenames and PDF SHA-256 hashes. The pinned acquisition,
eligibility, case identities, document mappings, PDF bytes and all recorded chunks are checked
against reconstructed production parser/chunker output before preparing either arm. The saved
retrieval model must be `intfloat/multilingual-e5-small` at
`614241f622f53c4eeff9890bdc4f31cfecc418b3`. No human gold is used to select context, and
upstream reference answers/documents are not included as answer hints in prompts.

Response schema version `external-cloud-qa-answer-1` contains `question_id`, `status`
(`ANSWERED` / `UNABLE_TO_ANSWER`), `answer`, `evidence` and `inability_reason`. Every evidence
record has `document_id`, `page_number`, `quote` and an optional `chunk_id`. Inability requires
an explicit reason. `validate_answer` uses TenderCite's existing case-sensitive,
whitespace-normalized page-quote validation and additionally requires the whole quote within
one source actually supplied in that strategy. A supplied chunk ID must match that source.
Wrong question/document/page/chunk or unsupported quotes are invalid; absent/empty evidence is
missing. Results distinguish `VERIFIED_QUOTE`, `INVALID_QUOTE`, `MISSING_EVIDENCE` and
`NOT_INDEPENDENTLY_ESTABLISHED` semantic correctness. Native provider citations cannot replace
this validation. Quote occurrence does not establish that an answer follows from the source.

## Configuration and conservative preflight

Copy [the configuration template](../../evaluation/cloud-llm-config.example.json) into ignored
local storage. It deliberately has no provider prices or model context limit and fails until
you supply them. Select an HTTPS OpenAI-compatible base URL and model; verify that the chosen
provider/model supports this adapter's JSON-schema response format, `temperature` and
`max_tokens`. Record the usable context limit and its documentation in `usable_context_tokens`
and `context_budget_source`.

Set `pricing` to an object with these required fields, using an actual dated provider quote:

- `scheme`: `uncached_text_per_million_tokens` (the only supported pricing scheme).
- `base_url` and `model`: exactly the selected endpoint/model.
- `currency`: `EUR` or `USD`.
- `input_per_million`, `output_per_million`: explicit nonnegative rates in that currency.
- `source`: pricing documentation/quotation reference; `as_of`: ISO date.

Missing, nonfinite, negative or unsupported pricing is rejected. No live pricing verification,
cache discount, tier adjustment or currency conversion occurs. Prices and spending limits must
use the same currency. The preliminary experiment allowance is **20 EUR**, with a configurable
1 EUR per-request safeguard. USD quotes require explicitly supplied USD per-request and
cumulative limits; the EUR defaults cannot simply be relabeled. This tool does not determine
an exchange rate or establish how a USD allowance relates to the user's EUR budget.

The supported `utf8_bytes_v1` estimate counts the complete UTF-8 serialized request, including
source identifiers, all source text and both schema copies, plus 256 overhead units and a 1.10
token safety multiplier by default. It is a deliberately conservative approximation for usual
byte-based tokenizers, **not exact tokenization or a universal provider guarantee**. Estimated
input plus reserved maximum output must fit the declared usable context budget; overflow blocks
preflight and retains full source text. Provider/model-specific token accounting still needs
verification before a later execution phase.

Costs use the estimated input and maximum output at the explicit uncached rates, with a further
1.25 spending safety multiplier. The cumulative check sums **both arms, every question and all
configured repetitions**, plus `prior_estimated_spend`. Exact cap equality is allowed. Failed
requests are retained in the report rather than dropped to make a run fit. Local caps are
safeguards, **not guaranteed provider billing caps**; later execution needs provider-side budget
controls and actual usage accounting. No retrying/execution loop is implemented here.

Endpoint/model can also be overridden through process environment variables
`TENDERCITE_LLM_BASE_URL` and `TENDERCITE_LLM_MODEL`; overrides must still match pricing.
`TENDERCITE_LLM_TEMPERATURE`, `TENDERCITE_LLM_MAX_OUTPUT_TOKENS` and
`TENDERCITE_LLM_REQUEST_TIMEOUT_SECONDS` override the corresponding numeric settings. This CLI
does not independently load `.env`. `api_key_env` names an environment variable, defaulting to
`TENDERCITE_LLM_API_KEY`; never put the key in JSON. Dry-run checks presence only and works
without credentials. It does not authenticate or transmit the key.

## One-line PowerShell workflow in VS Code

Run from the repository root with the normal environment installed. Reuse the pinned local
acquisition and completed 3B-a `results.json`; if missing, follow the
[acquisition](external-benchmark-pilot.md) and [document retrieval](external-document-retrieval.md)
guides. A COMPLETE human source-span review report is not needed to build these contexts.

Create a fresh ignored configuration directory and copy the template:

```powershell
New-Item -ItemType Directory -Path .\data\evaluation\external\cloud-llm-comparison -ErrorAction Stop | Out-Null; Copy-Item .\evaluation\cloud-llm-config.example.json .\data\evaluation\external\cloud-llm-comparison\config.json -ErrorAction Stop
```

Open that local JSON and enter verified endpoint/model, context and pricing assumptions:

```powershell
code .\data\evaluation\external\cloud-llm-comparison\config.json
```

Build both arms and run local preflight, using a new output directory each time:

```powershell
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_comparison --dry-run --config .\data\evaluation\external\cloud-llm-comparison\config.json --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --output-dir .\data\evaluation\external\cloud-llm-comparison\preflight-1
```

Omitting `--dry-run` has the same behavior. Exit 0 means `READY_DRY_RUN`; input, context or cost
failures return 1. Existing outputs are never overwritten. `preflight.json` records provenance
hashes, configuration, request hashes, token/cost estimates, failures and execution blockers.
`prompts.json` contains prepared sources and exact request payloads, including third-party text.
Both must remain under ignored `data/evaluation/external/`; do not force-add them to Git.

## Transmission permission and data rights

Before any future transmission, a human must confirm that sending these exact artifacts to the
selected provider/model is permitted, considering PDF rights, procurement confidentiality and
provider processing/retention terms. Public availability and the QA dataset's declared license
do not prove rights to transmit each underlying PDF. `--confirm-transmission` records that
operator declaration against the endpoint, model and artifact hashes; it sends nothing and
does not enable execution. An absent declaration and absent credentials are explicit execution
blockers. **Even with both and `--execute`, this milestone refuses API execution.** A later paid
runner will need fresh explicit opt-in, permission, passing budgets and provider validation.

Raw documents, prompts and any future responses remain local and ignored. No real annotations,
reference answers or third-party source text are committed; API key values are not recorded or
logged. Parsed full context retains parser output and page boundaries, not original layout or
OCR. Encrypted/unparseable documents and their three dependent cases retain existing exclusions.

## Actual validation and remaining work

On 2026-10-11, starting from merged main `c280586` (PR #13), the existing pinned artifacts were
revalidated locally. Context preparation succeeded for nine original eligible questions, all
three parseable PDFs and 137 PDF pages: 18 comparison requests, each RAG arm with 12 saved chunks
and each full-context arm with all 137 page sources. No embeddings, downloads or provider calls
were made. A CLI invocation with the unfilled template returned `BLOCKED_INPUT`, identifying
missing usable context/pricing information, and recorded zero API calls. These are preparation
observations, not provider feasibility, cost or answer-quality measurements.

The complete offline suite passed **429 tests**, with one optional live TED smoke test skipped;
Ruff lint and format checks passed. Sixty added tests use original generated PDFs, synthetic
saved chunks, artificial unit-test prices and mocked HTTP only. They cover both strategies,
all-document inclusion, identifiers/page boundaries, provenance/tampering, evidence validation,
missing evidence, context/cost failures, currency/rate assumptions, environment configuration,
credential/permission gates, output preservation and refusal of paid calls. No hosted-provider
compatibility or Windows execution was tested; the PowerShell commands document Windows usage.

Remaining prerequisites are a selected provider/model, verified usable context and tokenizer
accounting, dated supported pricing and matching budget currency, provider compatibility,
credentials and explicit document-transmission permission. Paid execution/usage accounting and
independent human answer review belong to a later milestone. Nine biased upstream QA candidates
from three documents cannot establish general procurement answer accuracy.
