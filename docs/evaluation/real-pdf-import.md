# Manual procurement PDF corpus registration

This evaluation-only workflow starts from main `6847a0c` after merged TED pilot PR #7.
It contributes to [issue #4](https://github.com/nak224/tendercite/issues/4): the
[TED pilot](ted-pilot.md) obtained notice metadata but its PDF/XML downloads hit origin WAF
challenges. A curator can now register PDFs obtained through permitted manual downloads.
The importer makes no network requests, changes no production ingestion behavior, and performs
no indexing, benchmark, gold-label generation or LLM evaluation.

## Commands and metadata

Use the repository's existing Python 3.12 development environment. Download permitted PDFs
manually into a separate input directory; preserve the downloaded bytes and record the actual
source URL and download date. The importer scans only top-level `.pdf` files, case-insensitively.

```bash
source .venv/bin/activate
mkdir -p data/evaluation/manual-input
# Place manually downloaded PDFs in that directory and create metadata.json as below.
python -m evaluation.import_pdfs data/evaluation/manual-input \
  --metadata data/evaluation/manual-input/metadata.json \
  --tender-id tender-a \
  --corpus-dir data/evaluation/real-pdf-1 --corpus-version real-pdf-1

# Read-only integrity verification; does not rewrite the manifest:
python -m evaluation.import_pdfs --verify --corpus-dir data/evaluation/real-pdf-1
python -m evaluation.import_pdfs --help
```

The following is a **fictional metadata template**, not evidence of acquired documents.
Replace the filenames, URLs, dates and notes with the actual provenance of your files. Keep
unknown dates null; registration time is recorded separately and never substitutes for a
download date. The optional TED publication number is normalized and validated syntactically;
it triggers no API lookup or verification that a PDF belongs to that notice.

```json
{
  "defaults": {
    "tender_id": "tender-a",
    "declared_language": "de",
    "retrieval_date": null,
    "provenance_status": "unverified",
    "rights_status": "unverified"
  },
  "files": {
    "Bekanntmachung.pdf": {
      "ted_publication_id": "123456-2026",
      "source_urls": ["https://example.invalid/notices/123456-2026"],
      "provenance_notes": "Replace with the actual official source and manual download details.",
      "rights_notes": "Reuse and redistribution rights have not yet been reviewed."
    },
    "Leistungsbeschreibung.pdf": {
      "source_urls": ["https://example.invalid/tender-a/specification.pdf"],
      "provenance_notes": "Replace with the source of this separate tender document.",
      "rights_notes": "Rights review pending."
    }
  }
}
```

`defaults` apply to every input PDF. Shared CLI options `--tender-id` and
`--ted-publication-id` override those defaults; per-filename `files` entries override both.
Multiple PDFs share a tender through `tender_id`; a per-file override supports another tender
in the same import. TED metadata is optional, including for non-TED procurement sources.
Metadata keys must match actual input filenames exactly. Unknown keys, invalid dates,
malformed publication IDs, and source URLs outside HTTP(S) or containing credentials are rejected.
URLs are recorded, never fetched or checked for official-source authenticity.

Missing URLs, download dates, provenance notes, rights notes, tender grouping or declared language
produce explicit flags. Provenance and rights remain `unverified` by default, even when notes
are supplied. A curator may set `provenance_status` to `verified` only with URLs, a retrieval date
and nonblank notes; `rights_status: verified` requires nonblank rights notes. These are curator
declarations, not automatic legal/source verification. Language is declared, not detected; manually
check original German text rather than inferring it from a German interface or translation.
Incomplete provenance does not prevent local registration, but the warnings remain in the manifest.

## Manifest and repeatability

The default output is ignored local storage:

```text
data/evaluation/real-pdf-1/
  manifest.json
  pdfs/<full-sha256>.pdf
```

The machine-readable manifest contains:

| Field | Meaning |
| --- | --- |
| `manifest_version`, `corpus_version` | Schema version 1 and corpus version, default `real-pdf-1` |
| `created_at`, `updated_at` | UTC registration timestamps |
| `documents[]` | Stable `document_id` (`sha256:<full hash>`), `sha256`, relative PDF path, byte size, page count, validation and first registration timestamp |
| `documents[].sources[]` | Original filename, tender ID, optional TED ID, URLs, retrieval date, declared language, provenance/rights notes and statuses, missing/unverified flags |
| `tenders` | Tender IDs mapped to deduplicated document IDs; no automatic grouping by TED ID |
| `import_runs[]` | UTC start/end times, completion status and per-file outcomes, errors, source metadata and hashes/validation where available |

IDs belong to the evaluation corpus and are independent of production ingestion IDs. Identical
PDF bytes share one stored document, while different names, sources and tender associations
remain recorded. Reimporting unchanged bytes/metadata preserves documents and associations and
appends an audit run. Updated notes add an association without overwriting old provenance.
Different bytes under the same filename create a new document ID and retain the earlier version.
Use a separate output directory for another corpus version; version mismatches are rejected.

The importer parses the exact byte snapshot it hashes and copies those original bytes unchanged.
Every existing registered PDF is checked for SHA-256, size, ID and expected path before reimport.
`--verify` performs the same read-only integrity checks. Missing/corrupted files stop imports
instead of being silently replaced. Integrity checks do not re-evaluate extraction quality,
rights or provenance. The JSON manifest is replaced atomically; concurrent writers are unsupported.

## Validation and Git exclusions

The importer reuses the TED pilot's `check_pdf` and production `PyPdfParser`/pypdf. It rejects
unreadable, malformed, encrypted and unparseable PDFs, symlinks, files over 25 MiB, documents
outside 1–250 pages, or extracted text over 1,000,000 characters. At least 100 alphabetic
characters and 20 words are required across a PDF. Sparse/scanned documents fail with an
explicit insufficient-text reason; no OCR is performed. Passing these bounds does not establish
complete or accurate extraction of tables, layouts or every individual page.

Successful PDFs remain registered if another file fails. Rejected input files are named in logs
and recorded in `import_runs`; their bytes are not copied into the corpus. The CLI exits 1 on
rejections, invalid configuration or integrity errors, and 0 for a completed import. Missing or
unverified provenance/rights produce warnings without changing that exit status. An empty input,
absent/empty corpus for verification, or metadata filename typo is an error.

`data/evaluation/` is ignored, including the default manifest and metadata. All PDF extensions
are additionally Git-ignored anywhere in the checkout, including custom directories and mixed
case names. Custom directories' JSON files are not automatically ignored. Never force-add raw
procurement PDFs; Git ignore rules can be overridden. Review metadata and personal information
before deliberately sharing any manifest. Public availability alone does not establish reuse
permission, and TenderCite's Apache-2.0 license does not cover imported third-party documents.

## Tested outcome and remaining work

On 2026-10-09, 28 importer tests passed using only original, self-generated PDF fixtures.
They cover single/multi-PDF imports, tender grouping, hash deduplication, provenance retention,
repeatability, changed content, metadata precedence, integrity verification/tampering, rejected
and unreadable/encrypted/scanned inputs, partial imports and default/custom Git exclusions.
The complete offline suite passed **169 tests**, with one optional live TED test skipped;
`ruff check .` and `ruff format --check .` passed. No new dependencies were added.

**No genuine procurement PDFs were supplied or imported in this milestone.** A real corpus still
requires permitted manual downloads, source/date and rights review, original-language confirmation,
and manual checks of readability, document versions and tender grouping. The TED candidate list
can inform that review but is not a suitable-document shortlist. Later work must define and
independently review manual source-span gold annotations before running a real-tender benchmark.
No automatic labels, new retrieval scores, LLM results, real-tender accuracy or exhaustive
extraction are claimed here.
