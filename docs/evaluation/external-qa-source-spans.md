# External QA source-span review (Milestone 3B-b1)

This offline workflow prepares human evidence review for the EU Tenders QA benchmark.
It does not run retrieval, compute new metrics or create requirement-extraction labels.
The original questions and document references remain upstream candidates: exact quote
occurrence establishes location, not the semantic correctness of an answer or interpretation.

## Inputs and local storage

Use the acquisition manifest and validation report from the
[acquisition pilot](external-benchmark-pilot.md), plus the completed local `results.json`
from [document retrieval](external-document-retrieval.md). The default dataset revision is
`f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea`; the family is
`BSGEE_2025-002_School_Information_Systems`.

Preparation revalidates local artifact bytes and parsing, and checks 3B-a question IDs,
questions, types, original references, exclusions and PDF hashes against the acquisition.
There are no downloads, model calls or inferred upstream chunk/page mappings. Missing or
changed inputs fail explicitly. If this environment has no local artifacts, first reproduce
the two linked workflows; do not substitute fabricated results or silently repair labels.

All source-containing input/output paths must be under ignored `data/evaluation/external/`.
Symlink paths and paths outside this root are rejected. PDFs, questions, source text, review
files and validation reports remain local. Fresh output paths are required: generation and
validation never overwrite an existing human review file or report.

The dataset card declares MIT, but this does not independently establish reuse rights for
underlying procurement PDFs, personal information or their derived text. Review the source
terms, provenance and redistribution rights before sharing any artifacts. Git ignoring and
path checks prevent ordinary accidental inclusion; deliberately force-adding files must be
avoided. Code and original synthetic tests do not redistribute benchmark content.

## Generate the template and locate passages

Run these **one-line PowerShell commands** in the repository root using its configured venv.
The input paths are the local pilot paths; substitute your own acquisition/run directory if
needed. Use a fresh `external-qa-source-spans-1` directory when reproducing preparation.

```powershell
.\.venv\Scripts\python.exe -m evaluation.prepare_external_qa_spans --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --template .\data\evaluation\external\external-qa-source-spans-1\review.json --pages-dir .\data\evaluation\external\external-qa-source-spans-1\pages
code .\data\evaluation\external\external-qa-source-spans-1\review.json .\data\evaluation\external\external-qa-source-spans-1\pages\index.json
code .\data\evaluation\external\external-qa-source-spans-1\pages
```

The separate [JSON Schema](../../evaluation/external-qa-source-span-schema.json) is version
`external-qa-source-spans-1`. Each question preserves its ID, original question/type and
ordered references with PDF SHA-256 hashes. Header hashes bind the acquisition manifest and
3B-a results. The initial `evidence` arrays are empty; no reviewer labels are generated.

`pages/index.json` maps each referenced filename/hash to numbered text files. Each
`<pdf-sha256>/page-0001.txt` contains **exact production parser output**, with no added headers
or newline conversion, and an accompanying text checksum. Page numbers are 1-based PDF page
indices, not printed page labels. Use VS Code Find in Files (`Ctrl+Shift+F`) to locate terms,
then check the original PDF and full question. No automatic suggestions are generated.
The production parser's extracted/normalized text can differ from rendered tables or layout.

## Add and independently review evidence

Add one or more records to the relevant question's `evidence` array. Copy a contiguous quote
from the exported page text without trimming, case changes or rewording. Encode embedded
newlines as `\n` in JSON. Do not edit question metadata, referenced files or hashes.
This complete evidence-record example is **original synthetic content**, not a pilot label:

```json
{
  "source_document": "notice.pdf",
  "pdf_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "page_number": 1,
  "exact_source_quote": "The contractor must submit a maintenance plan.",
  "interpretation_note": "Check whether this passage supports the whole question.",
  "review_status": "DRAFT",
  "author_alias": "curator-a",
  "reviewer_alias": null,
  "review_notes": ""
}
```

Use non-personal aliases matching `[a-z][a-z0-9_-]{0,63}`. A second human must check the
source location **and semantic support**, then declare `VERIFIED`, set a reviewer alias
distinct from the author and add nonblank review notes. Aliases declare independent human
review; software cannot authenticate the humans or prove semantic correctness. `REJECTED`
also requires nonblank review notes and contributes no verified evidence. DRAFT is excluded.

For reasoning/multi-hop questions, review the complete inference and all original document
references. A question is evidence-complete only when **every required document** has at least
one valid independently VERIFIED supporting span and no invalid evidence records remain.
Multiple distinct spans per document are supported. Repeating the same filename/hash/page/
quote within a question excludes all copies, even if notes/status differ. A passage can
legitimately support several different questions.

If references are ambiguous or unsupported, retain the original question and every reference.
Describe the issue in `annotation_notes`; leave missing evidence empty or reject an actually
located candidate with a reason. Do not invent quotes, reduce the reference set or treat the
upstream reference answer as trusted gold. Questions remain incomplete until independently
reviewed supporting evidence exists for each required document.

The encrypted invitation PDF is unsupported. The three excluded original case IDs are
recorded with their acquisition reasons in validation reports and remain outside the review
template. Do not decrypt, infer spans from ordinal chunk labels or silently make these cases
eligible. Changing the upstream reference policy requires a separate explicit decision.

## Validate

Use a fresh report filename on each run:

```powershell
.\.venv\Scripts\python.exe -m evaluation.validate_external_qa_spans .\data\evaluation\external\external-qa-source-spans-1\review.json --acquisition-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems --retrieval-results .\data\evaluation\external\document-retrieval-run-2\results.json --report .\data\evaluation\external\external-qa-source-spans-1\validation-reviewed-1.json
```

Validation reparses hash-checked PDF snapshots, rather than trusting editable text exports.
It checks original question metadata, correct family/question references, SHA-256, page bounds,
verbatim nonempty quotes, duplicates, schema/review metadata and independent reviewer aliases.
The local report preserves per-record errors/exclusion reasons, review notes, per-document
verified-span counts and per-question completeness. Missing eligible questions are invalid.
Only valid VERIFIED spans from valid questions appear in `verified_source_spans`.

`COMPLETE` returns exit code 0; `INCOMPLETE` and `INVALID` return 1. An empty initial template
is intentionally INCOMPLETE, not a failed preparation. Invalid/missing source artifacts fail
with an explicit error; acquisition `validation.json` retains artifact-level diagnostics.
JSON Schema helps editing, but the CLI additionally enforces contextual and reviewer rules.
There is no evaluation-ready accuracy score or requirement-gold export in this milestone.

## Actual local pilot and remaining work

On 2026-10-11, the retained acquisition and completed 3B-a results were revalidated locally.
Preparation retained all **9 eligible questions** from the 12-case family and all **3 original
encrypted-source exclusions**. It exported **100 pages from 2 referenced PDFs** (specifications
and framework contract); the parseable annex is not referenced by any eligible question.
The generated template has **0 evidence records** and **0 VERIFIED annotations**. Validation
returned **INCOMPLETE, 0/9 evidence-complete**, exit code 1, as expected. No downloads, embeddings,
LLM calls, source-span scores or semantic QA judgments were performed.

Humans still need to review rights, locate supporting passages, author quotes, independently
review their semantics and resolve unsupported references. These QA evidence annotations stay
separate from [procurement requirement annotations](real-tender-annotations.md): question types
and supporting passages are not MUST/SCORING/INFORMATION/RISK requirement-extraction labels.
The existing German/synthetic requirement gold is unchanged. A subsequent milestone can define
source-span retrieval evaluation after suitable human review; none is implemented here.
