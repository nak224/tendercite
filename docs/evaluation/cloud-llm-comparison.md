# Guarded cloud LLM comparison (Milestones 3B-d1 / 3B-d2 / 3B-d2a)

The CLI defaults to an offline dry-run. An explicitly authorized operator can now execute
**one original eligible question with both strategies**, using OpenAI Chat Completions and
`gpt-6.1-sol`. Codex implementation, tests and PR review make **no paid API calls**. Live model
compatibility, token usage and answer quality remain unmeasured after the operator's first
quota-rejected pilot. No model fallback, schema weakening, automatic retry or automatic all-question run
is implemented.

## Shared inputs and compatible payloads

Both arms use identical original questions, instructions, model and `QAAnswer` output schema:

| Strategy | Sources |
| --- | --- |
| `e5_rag` | First `rag_top_k` saved E5 chunks (default 12, range 1–50), preserving ranks and document/page/chunk IDs. No E5 rerun |
| `full_context` | Every parsed page of all three parseable family PDFs, including the unreferenced annex, ordered by filename/page. No reference-label filtering or silent truncation |

The pinned acquisition and 3B-a results are revalidated: original questions/exclusions,
PDF bytes/hashes, document IDs and reconstructed parser/chunker output must match. Saved E5
must be `intfloat/multilingual-e5-small` at `614241f622f53c4eeff9890bdc4f31cfecc418b3`.
The original cohort has nine eligible questions, three PDFs and 137 pages; three cases remain
excluded because of encrypted-source dependence. Human gold and upstream reference answers
are not used as prompt hints or to select documents. Existing annotations stay unchanged.

The existing OpenAI-compatible adapter and `StructuredLLM` contract are reused. Its pure
payload builder is shared by preflight and execution. For exact model ID `gpt-6.1-sol`, it sends
`max_completion_tokens`, explicit `reasoning_effort: low`, and strict JSON-schema Structured
Outputs. Every object has `additionalProperties: false`; nullable properties are required
and permit `null`, with schema defaults removed. It sends no `temperature`, `top_p` or other
sampling controls. The configured temperature remains audit metadata for this model; it is
not sent. Other providers retain their existing payload behavior and production defaults.
Actual request payload hashes, effective generation fields, endpoint/model/configuration and
evaluation/adapter code hashes are recorded. A provider error stops execution and preserves
its original response locally, with credential values redacted if echoed.

Response version `external-cloud-qa-answer-1` contains `question_id`, `status`
(`ANSWERED` / `UNABLE_TO_ANSWER`), `answer`, `evidence` and `inability_reason`. Inability requires
a reason and is reported separately from an answer with missing evidence. Citations must match
the stored PDF page and an actually supplied source, using the existing case-sensitive,
whitespace-normalized quote check. **RAG requires an actual supplied chunk ID**; full context
requires the supplied document/page. Whole quotes must fit a supplied chunk/page source.
`VERIFIED_QUOTE`, `INVALID_QUOTE` and `MISSING_EVIDENCE` describe occurrence, not semantic
correctness. Native provider citations are retained separately and never bypass validation.

## Preserve the existing estimates and assumptions

Reuse the operator's existing local configuration. Do not retune rates, token estimation or
safety margins to make the pilot fit. Execution requires `https://api.openai.com/v1`, model
`gpt-6.1-sol`, `api_key_env: TENDERCITE_LLM_API_KEY`, `reasoning_effort: low` (also the default),
one repetition, USD pricing and an exact cumulative experiment limit of **20 USD**. Context,
output-token, timeout and per-request limits must pass preflight. No model price is supplied
or guessed by TenderCite; the committed template remains deliberately unfilled.

The original `utf8_bytes_v1` calculation is unchanged: complete serialized request bytes,
including identifiers/source text/both schema copies, plus configured overhead, multiplied
by the configured token safety factor. Reserved maximum output and estimated input must fit
the declared usable context budget. Costs use the original explicitly configured uncached
input/output rates and spending safety factor. The defaults remain 256 overhead units,
1.10 token factor and 1.25 cost factor; existing configured values are preserved. These are
estimates, not exact provider tokenization or guaranteed billing limits.

The operator reported a previous 18-request preflight of **18.887555 USD** with margin and
zero API calls. That local successful report/configuration is not present in this Codex
checkout; the available local config is an unfilled template. No attempt is made to recreate
that number with invented rates. `--previous-preflight` optionally links the original report
by hash and records its cost/currency/request count, rejecting changed pricing or token
assumptions. Compatibility changes alter request bytes, so rerun dry-run and retain both
estimates. Without the old report, `previous_dry_run.available` is explicitly false.

Configuration and environment overrides from 3B-d1 remain supported: endpoint/model,
temperature, output-token cap and timeout. This CLI does not independently load `.env`.
Only HTTPS is permitted; execution further requires the exact official OpenAI endpoint.
Prices and budgets must use the same currency; no USD/EUR conversion occurs. This 20 USD cap
does not establish equivalence to the earlier preliminary 20 EUR allowance.

## Usage accounting and persistent reservations

Before each request, its conservative preflight cost is durably reserved in
`data/evaluation/external/cloud-llm-experiment/ledger.json`. This path is independent of output
directories and cannot be replaced through a CLI option. An OS file lock prevents concurrent
executors on the same local ledger, including while awaiting responses. Atomic writes/fsync
preserve reservations across interruption. Keep this ledger and its lock file; do not delete,
reset or copy a fresh ledger to obtain another allowance. Separate checkouts/machines cannot
coordinate this local cap; use one experiment checkout and provider project controls.

The ledger binds provider/model, artifact provenance, original rates, optional usage rates,
prior estimated spend and the 20 USD cap. Its committed balance includes settled usage-derived
costs and every unresolved reservation. The selected pair must fit the remaining balance;
each request is checked/reserved again before sending. Repeated attempts of a question pair,
including partly completed pairs, are rejected even with a fresh output directory, except
an explicit `--retry-reconciled` after every previous attempt of that question has a validated
manual zero-cost reconciliation. Successful or usage-settled requests cannot be repeated.
There is no automatic resume or retry.

Results distinguish four quantities:

1. The previous and updated **dry-run estimates**, including conservative output reservation.
2. **Provider-reported tokens**: prompt, completion, total and optional reasoning/cached/write
   categories, plus actual model, request/completion IDs, latency and completion status.
3. **Usage-derived cost under declared rates**, without calling it an invoice. Reasoning is
   already included in completion tokens and is never added again. Cache/write counts are
   treated as subsets of prompt tokens, validated against their parent counts.
4. **Final provider billing**, which requires reconciliation with the provider dashboard.

Absent optional categories remain unknown. Without explicit cache discounts, the unchanged
uncached input rate is applied to all input, clearly labeled as an assumption. Optional
`usage_pricing` can record dated/source-backed `cached_input_per_million`,
`cache_write_input_per_million` and ascending `tiers` with `above_prompt_tokens` plus explicit
input/output/cache rates. Actual prompt count chooses the highest crossed threshold; equality
does not cross it. These settings affect usage accounting only. Execution rejects rates above
the unchanged reservation rates rather than silently changing preflight assumptions.

Missing/inconsistent mandatory usage, unsupported non-text categories or an unexpected model
leave accounting uncertain and retain the reservation. HTTP errors, timeouts and interrupted
requests never receive an assumed zero charge. Known usage reconciles the reservation; usage
cost above the reservation pauses further execution. Truncated/refused/invalid structured
responses also stop the pair, while preserving known usage. A valid answer with invalid/missing
citations remains an observable result and does not prevent comparing the other arm.
HTTP failures are classified before checking the response model identifier. The original
provider code, type, message, HTTP status and request ID are retained; a no-usage HTTP 429 with
`credit_balance_exhausted` remains `UNCERTAIN`, rather than reporting a missing model or
assuming zero cost. Uncertain/interrupted ledger entries block later continuation. The narrow
offline workflow below can clear only a recorded no-usage credit-exhaustion failure after
explicit human dashboard confirmation; it cannot clear unrelated errors or interrupted requests.

Enable provider-side project budget/usage alerts before authorizing a pilot and confirm how
those controls are enforced. Local estimates and locks are safeguards, **not guaranteed
provider billing caps**. Dashboard reconciliation may differ because of pricing tiers,
discounts, taxes or other charges outside the configured text-token assumptions.

## One-line PowerShell commands in VS Code

Run from the repository root. Reuse the existing verified local config and pinned acquisition/
3B-a `results.json`. The example question ID below is one original eligible question; inspect
its local question first and select the intended original ID explicitly. All output directories
must be fresh. The following are operator commands, not commands executed by Codex.

Set the API key only in this PowerShell session, without placing it in command history or files:

```powershell
$env:TENDERCITE_LLM_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenAI API key' -AsSecureString)).Password
```

Run an updated dry-run for all 18 requests after the payload changes (no key/permission needed):

```powershell
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_comparison --dry-run --config .\data\evaluation\external\cloud-llm-comparison\config.json --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --previous-preflight .\data\evaluation\external\cloud-llm-comparison\preflight-1\preflight.json --output-dir .\data\evaluation\external\cloud-llm-comparison\preflight-sol-1
```

Use the original successful report's actual path for `--previous-preflight`, or omit that option
if unavailable. Add `--question-id` to dry-run just the selected pair before authorizing it.
Exit 0 means `READY_DRY_RUN`; failed context/cost/input checks return 1 without paid calls.

Only after explicit operator authorization and confirmation that transmission is permitted:

```powershell
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_comparison --execute --confirm-transmission --question-id BSGEE_2025-002_School_Information_Systems-dc506b77 --config .\data\evaluation\external\cloud-llm-comparison\config.json --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --output-dir .\data\evaluation\external\cloud-llm-comparison\pilot-1
```

This attempts exactly two requests on success: RAG then full context for that question. Guard,
compatibility, accounting or budget failures may stop before either or before the second.
It never automatically runs all 18. Missing `--execute` always builds an offline dry-run;
execution additionally requires question selection, permission, key and passing checks.

Inspect the local run costs and ledger balance:

```powershell
Get-Content .\data\evaluation\external\cloud-llm-comparison\pilot-1\execution.json -Raw | ConvertFrom-Json | Select-Object status,api_calls_attempted,preflight_estimated_pair_cost_with_margin,accounting_complete,usage_derived_run_cost,remaining_budget,results
```

Inspect per-request usage, original response/errors, structured answer and citation validation:

```powershell
code .\data\evaluation\external\cloud-llm-comparison\pilot-1\e5_rag.json .\data\evaluation\external\cloud-llm-comparison\pilot-1\full_context.json .\data\evaluation\external\cloud-llm-experiment\ledger.json
```

On an interrupted run, inspect the ledger and any `blocked-execution.json` before doing anything
else. Missing response files do not prove a request was unbilled. Exit 0 for execution means
`COMPLETED_PAIR`, not semantic correctness or reconciled provider billing.

## Manual reconciliation and explicitly authorized retry (3B-d2a)

The operator reported HTTP 429, type `insufficient_quota`, code `credit_balance_exhausted`,
request ID `req_d46ba490ffed4cc08a80bea893b31a49`, no token usage and an uncertain reservation
of **0.19928000 USD** for the first RAG request. Full context was never attempted. The operator
reported checking the OpenAI billing dashboard and confirming **0 USD** billed, then adding
credits. This is operator-supplied billing confirmation, not independently measured API usage
or an invoice. **Codex has not reconciled this incident or executed a real retry.**

The reconciliation CLI is entirely offline. It requires the exact reservation key, matching
provider request ID, `--confirm-zero-billed`, a reason and a non-personal operator alias. It
checks the original saved response, status, question/strategy association and absent usage.
Legacy PR #15 entries and their misleading error summary remain intact; matching uses the
original provider error body. Missing response files, different errors or request IDs,
nonzero/unknown billing and already-reconciled entries are rejected.

The original entry, reservation and `UNCERTAIN` historical status are never rewritten.
An exclusively created audit file under
`data/evaluation/external/cloud-llm-experiment/reconciliations/<reservation-key>.json` records
the complete original entry, affected attempt, response hash, operator confirmation, zero
billed amount, reason and UTC timestamp. Its hash is added to the existing ledger; only that
atomic publication releases the reservation. Later reads verify the immutable audit and
original response. Do not edit or delete either. An interrupted publication retains the
reservation; repeating the identical explicit confirmation can publish the existing audit
without overwriting it. A duplicate completed reconciliation is rejected.

Reconciliation and execution share the same OS lock and persistent **20 USD** experiment
budget. `--retry-reconciled` is required to retry this same question pair, in a fresh output
directory and with all normal execution gates. A new UUID identifies the pair attempt; both
requests get new reservation keys while the failed attempt remains in history. Legacy attempts
are identified as `legacy:<original-reservation-key>` without migrating their entries.
Any unresolved reservation anywhere in the ledger still blocks execution. A question with
even one successful/settled previous request is not eligible for a whole-pair retry.
Estimation, pricing, prompts, retrieval, annotations and model selection are unchanged.

Run these one-line PowerShell commands from the same checkout that holds the **existing**
ledger and original output files. Do not copy/reset the ledger. First inspect the incident
and derive its original question ID and reservation key, failing unless exactly one matches:

```powershell
$raw = .\.venv\Scripts\python.exe -m evaluation.reconcile_cloud_llm_ledger inspect; if ($LASTEXITCODE -ne 0) { throw 'Ledger inspection failed' }; $state = $raw | ConvertFrom-Json; $failed = @($state.entries | Where-Object { $_.provider_request_id -eq 'req_d46ba490ffed4cc08a80bea893b31a49' }); if ($failed.Count -ne 1) { throw 'Expected exactly one matching failed request' }; $reservationKey = $failed[0].reservation_key; $questionId = $failed[0].question_id; $failed[0] | Format-List
```

Confirm the inspected HTTP status/code, RAG strategy, original reservation and request ID.
**Only after checking that exact request's provider billing is zero**, explicitly reconcile:

```powershell
.\.venv\Scripts\python.exe -m evaluation.reconcile_cloud_llm_ledger reconcile-zero --reservation-key $reservationKey --request-id req_d46ba490ffed4cc08a80bea893b31a49 --confirm-zero-billed --operator-alias operator-1 --reason 'OpenAI billing dashboard checked: this exact failed request billed 0 USD'
```

Verify the new state before attempting any API request:

```powershell
.\.venv\Scripts\python.exe -m evaluation.reconcile_cloud_llm_ledger inspect
```

Expect the original `historical_status: UNCERTAIN` and `original_reserved_usd: 0.19928000`
to remain, with `effective_status: MANUALLY_RECONCILED_ZERO`, zero committed cost for this
entry and an incremented reconciliation count. Other costs/reservations remain unchanged.
The inspect/reconcile commands always report zero API calls. A missing ledger is an error,
not permission to create a replacement.

Reuse the existing API-key session command and unchanged verified config above. After fresh
operator authorization, run exactly the original question pair with a new output directory:

```powershell
.\.venv\Scripts\python.exe -m evaluation.cloud_llm_comparison --execute --confirm-transmission --retry-reconciled --question-id $questionId --config .\data\evaluation\external\cloud-llm-comparison\config.json --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --output-dir .\data\evaluation\external\cloud-llm-comparison\pilot-retry-1
```

Use a different fresh directory if `pilot-retry-1` exists. This may send two paid requests;
failures stop the pair without retrying. Inspect its `execution.json`, `e5_rag.json` and
`full_context.json` using the commands above with the new directory name. Final billing
still requires provider-dashboard reconciliation; local limits are not provider billing caps.

## Rights, local storage and actual validation

`--confirm-transmission` is an explicit human declaration about these artifact hashes and the
selected provider/model. Check document rights, confidentiality and provider retention/processing
terms first; public availability or a QA dataset license alone does not establish permission
for the underlying PDFs. Prompts, PDF bytes, provider response text and annotations remain under
ignored `data/evaluation/external/`. Do not force-add them. Keys are never recorded/logged, including
in echoed provider errors. No raw text, answers, pricing secrets or real annotations are committed.

Starting from main `763c238` after merged PR #15, the complete suite passed **509 tests**, one
optional live TED test skipped; Ruff lint and formatting passed. The 58 execution tests and
22 reconciliation regression cases use original synthetic PDFs/QA, artificial prices and
mocked HTTP only. They cover strict model payloads,
citations/inability, exact preflight wire reuse, token categories/tiers, missing usage/credentials,
errors/truncation, overspending, interruption/output failure, persistent duplicate prevention,
cross-process locking, malformed ledgers, default dry-run and one-pair CLI execution. The
reconciliation cases additionally cover the legacy quota response, explicit billing confirmation,
wrong request/error rejection, preserved history, duplicate/tampered audits and responses,
interrupted audit publication, a missing ledger, explicit retry and unchanged execution gates.
Linux
locking was executed; Windows commands/locking remain untested in this cloud environment.

The operator's ledger and original incident output are absent from this cloud checkout;
the real incident was not reconciled. No real API requests or live cost comparison were performed
by Codex. The operator's
valid local config/key, explicit transmission permission and provider-side controls remain pilot
prerequisites. Independently reviewed semantic answers and final dashboard billing are later
validation steps; small biased upstream candidate QA labels do not establish general accuracy.
