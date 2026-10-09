# Human annotation of procurement PDFs

This evaluation-only milestone starts from main `11118a3` after merged PR #8. It supplies a
versioned human-annotation format, an offline validator and an explicit gold export. It does not
generate labels from documents or perform model evaluation. No genuine procurement documents
have been labeled in this milestone; tests and the example below use original generated PDFs.

## Labeling genuine documents

1. Obtain permitted manual downloads and [register the PDFs](real-pdf-import.md). Assign one
   stable `tender_id` to all documents in a tender package. Review source/rights notes, document
   versions, original German language and page extraction quality before labeling.
2. Copy the corpus `document_id` and `sha256` from `manifest.json`. Read the original PDF and
   the selected extracted page together. Copy an exact quote from the extracted text, retaining
   case and line breaks. If extraction is unusable or the interpretation is ambiguous, keep the
   annotation DRAFT and explain the problem; do not invent a supporting quote.
3. Write the requirement statement and category/type manually, with a unique `annotation_id`,
   non-personal `author_alias`, timezone-aware `annotated_at` and `review_notes`. Start with
   `review_status: DRAFT`. Use `reviewer_alias: null` until another human has reviewed it.
4. A different human reviews the visual source, quote, interpretation, category/type and tender
   association. Record their different non-personal alias and notes; mark the label VERIFIED
   or REJECTED. Alias syntax cannot prove that two aliases represent different people.
5. Validate and explicitly export the reviewed file:

   ```bash
   python -m evaluation.validate_annotations data/evaluation/real-pdf-1/annotations.json \
     --corpus-dir data/evaluation/real-pdf-1 \
     --report data/evaluation/real-pdf-1/annotation-validation.json \
     --export data/evaluation/real-pdf-1/gold.json
   ```

To inspect extracted pages locally without a model or production ingestion:

```bash
python - <<'PY'
import json
from pathlib import Path
from evaluation.annotations import parse_snapshot

directory = Path("data/evaluation/real-pdf-1")
manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
document = manifest["documents"][0]  # Select the intended document by ID when labeling.
print(document["document_id"])
for page in parse_snapshot(directory, document):
    print(f"\nPAGE {page.page_number}\n{page.text}")
PY
```

Exact matching validates **occurrence, not semantic correctness**. An existing quote can be
misinterpreted, omit an exception or describe an obsolete condition. VERIFIED is a human review
declaration; the validator does not decide whether a requirement statement follows from the
quote. It also does not clear provenance/rights flags in the corpus or grant redistribution rights.

## Versioned format and validation

The file envelope requires `annotation_schema_version: real-tender-annotations-1`, the exact
`corpus_version`, `label_origin: curated_human` and an `annotations` array. The committed
[JSON Schema](../../evaluation/annotation-schema.json) describes every field. Automatically
generated candidate labels are unsupported and must be kept separate; renaming their origin
does not turn them into human gold.

Each annotation requires `annotation_id`, `tender_id`, `document_id` (`sha256:<full hash>`),
`sha256`, 1-based integer `page_number`, nonblank `source_quote` and `requirement_statement`,
`category`, `requirement_type`, `review_status`, `author_alias`, `review_notes` and `annotated_at`.
`reviewer_alias` is optional except for VERIFIED labels, where it must differ from the author.
Aliases use lowercase letters, digits, underscores or hyphens, start with a letter and have at
most 64 characters. Use identifiers such as `annotator_01`, never names or email addresses.

- Categories: MUST, SCORING, INFORMATION, RISK.
- Types: DEADLINE, ELIGIBILITY, REFERENCE, FINANCIAL, INSURANCE, TECHNICAL, CERTIFICATE,
  EVIDENCE, AWARD, PRICE, CONTRACT, LIABILITY, PRIVACY, SECURITY, OTHER.
- Human review states: DRAFT, VERIFIED, REJECTED; unrelated production review states are not used.

Validation checks manifest membership, PDF SHA-256/size/path integrity, tender associations in
both the tender map and document sources, extracted page counts and the literal quote on that
page. It parses the exact byte snapshot checked against the hash using the existing PDF parser.
Quotes are not trimmed, whitespace-normalized, case-folded or matched across pages. Multiple
annotations per PDF and multiple PDFs per tender are supported. PDFs and corpus metadata are
never modified.

Duplicate annotation IDs, or the same tender/document/page/quote/category/type under different
IDs, are reported and **all copies are excluded**, including clashes with invalid review records.
Different statement wording does not resolve that conflict; consolidate the reviewed label.
Keep one current record per annotation ID in a file. Preserve older file versions separately.

The report retains every input index/ID, validation errors, review status/notes where valid,
evaluation readiness and exclusion reasons. Schema/occurrence-invalid, DRAFT and REJECTED labels
are excluded from gold. The CLI exits 1 for invalid records or corpus errors, and can still
write a partial export of unaffected verified labels; inspect the summary/exclusions. It exits 0
for valid input even when no labels are verified, warning that there is no usable gold yet.
Empty annotation arrays produce empty exports. Fatal JSON/header/configuration errors write no
new outputs; do not reuse an older output after a failed command without checking it.

## Gold export and tender splits

`--report` defaults to `CORPUS_DIR/annotation-validation.json`; `--export` is explicit. Gold
version `real-tender-gold-1` contains only validated VERIFIED annotations, their IDs/categories,
quotes, statements and review metadata, corpus/schema versions, document hashes, the annotation
file's SHA-256, validation summary, corpus errors and exclusions. It adds `split` to each label
and a `tender_splits` map. No query generation, benchmark-runner integration or metrics are added.

The `sha256-tender-v1` strategy hashes UTF-8 `seed + NUL + tender_id`, interprets the first eight
digest bytes as a big-endian integer, and takes modulo 100. Buckets 0–69 are `train`, 70–84
`development`, and 85–99 `held_out_evaluation`. The fixed default seed is **224**; an explicit
`--split-seed` override is recorded. Adding/reordering labels or tenders does not change existing
assignments. Every document/page/annotation of a tender stays in the same split. These are
approximate proportions; a tiny dataset may leave some splits empty. Keep tender IDs and the seed
stable, and manually check shared PDFs, notice revisions and related procedures across distinct
tender IDs for leakage. Splitting does not establish that curated tender groups are independent.

All example/real artifacts should remain under ignored `data/evaluation/`; custom JSON output
locations are not universally ignored. Raw PDFs remain ignored anywhere in the repository.
Review personal data, source attribution and reuse rights before publishing annotation text or
manifests, which also contain third-party source quotes.

## Complete synthetic example

Run from the checkout with the existing `.venv` activated. This creates one original synthetic
PDF, registers it and writes a complete annotation file. The statement, quote and review state
are manually specified fixture values, not labels inferred from genuine documents. For real
labeling, start DRAFT and obtain an independent human review before setting VERIFIED.

```bash
python - <<'PY'
import json
from pathlib import Path
from evaluation.generate import make_pdf
from evaluation.local_corpus import MetadataFile, register_directory

root = Path("data/evaluation/annotation-example")
inputs = root / "input"
inputs.mkdir(parents=True, exist_ok=True)
quote = "Die Angebotsfrist endet am 30. November 2026 um 12:00 Uhr."
background = (
    "Die Vergabe betrifft die Bereitstellung und Wartung einer Softwarelösung. "
    "Die Unterlagen erläutern das Verfahren und die Zuständigkeiten. "
    "Unverbindliche Muster dienen nur zur Orientierung."
)
(inputs / "synthetic-notice.pdf").write_bytes(make_pdf([quote + "\n" + background]))
register_directory(inputs, root / "corpus", metadata=MetadataFile(defaults={
    "tender_id": "synthetic-annotation-tender", "declared_language": "de",
    "provenance_notes": "Original generated fixture, no procurement download.",
    "rights_notes": "Original synthetic example; no third-party document content."
}))
manifest = json.loads((root / "corpus/manifest.json").read_text(encoding="utf-8"))
document = manifest["documents"][0]
annotations = {
    "annotation_schema_version": "real-tender-annotations-1",
    "corpus_version": "real-pdf-1", "label_origin": "curated_human",
    "annotations": [{
        "annotation_id": "synthetic-deadline-01",
        "tender_id": "synthetic-annotation-tender",
        "document_id": document["document_id"], "sha256": document["sha256"],
        "page_number": 1, "source_quote": quote,
        "requirement_statement": "Das Angebot ist bis 30.11.2026, 12:00 Uhr einzureichen.",
        "category": "MUST", "requirement_type": "DEADLINE",
        "review_status": "VERIFIED", "author_alias": "annotator_01",
        "reviewer_alias": "reviewer_02", "review_notes": "Synthetic example review declaration.",
        "annotated_at": "2026-10-09T17:30:00+02:00"
    }]
}
(root / "annotations.json").write_text(
    json.dumps(annotations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
PY
python -m evaluation.validate_annotations data/evaluation/annotation-example/annotations.json \
  --corpus-dir data/evaluation/annotation-example/corpus \
  --report data/evaluation/annotation-example/validation.json \
  --export data/evaluation/annotation-example/gold.json
```

Offline validation: **51 annotation tests passed; full suite 220 passed, 1 optional live TED
test skipped; Ruff lint/format passed**. Genuine PDF annotation suitability, independent human
review, an adequate tender-level held-out corpus and subsequent retrieval/LLM evaluation remain
future work. No real-tender accuracy or model-performance result is claimed.
