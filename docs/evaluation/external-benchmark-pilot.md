# Milestone 3A: external procurement benchmark pilot

On **2026-10-10**, starting from main `d79b69a` (merged PR #9), we inspected and acquired
the public [EU Tenders QA dataset](https://huggingface.co/datasets/tmskss/eu-tenders-with-questions-for-agentic-checklist-filling).
This is acquisition/validation only. No retrieval scoring, LLM calls, classification training,
fine-tuning or conversion into human VERIFIED gold was performed.

## Actual upstream and acquired data

The official Hugging Face API resolved immutable revision
**`f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea`**. Its repository inventory contains 65 files:
62 PDFs, the dataset JSON, README/card and `.gitattributes`. The card and actual metadata agree
on **97 QA records across seven document families**, with language declared **EN**. All 97
records pass the implemented schema and filename/reference inventory checks. Presence in the
repository inventory does not mean all 62 PDFs were downloaded or parsed.

| Family | QA records | PDFs in repository inventory |
| --- | ---: | ---: |
| BSGEE_2023_016_School_Trips | 15 | 4 |
| BSGEE_2024-012_Nursery_And_School_Supplies | 15 | 6 |
| **BSGEE_2025-002_School_Information_Systems** | **12** | **4** |
| EC-COMM_VIE_2026_RP_0005 | 15 | 5 |
| EC-MOVE_2026_OP_0001_Road_Safety | 10 | 7 |
| EMSA_2026_CPN_0010_Equipment_Assistance_Service | 15 | 21 |
| ENISA_2026_OP_0007_EU_Cybersecurity | 15 | 15 |

We selected the school information systems family. **Six artifacts were downloaded** using
`huggingface_hub`: README (5,535 bytes), QA JSON (207,412 bytes), and four PDFs (4,803,783 bytes
combined). Metadata Git blob checksums and PDF upstream LFS SHA-256 checks passed. Every local
artifact also has a recorded SHA-256, immutable revision, source URL, filename and retrieval
timestamp. All downloads succeeded through normal TLS and the managed proxy; **no download
hostname was blocked**. The source URLs identify the HF mirror, not a verified original issuer
download. No other family's PDFs or the later sustainability dataset were downloaded.

| Selected PDF | TenderCite parser result | Extracted pages | Text characters |
| --- | --- | ---: | ---: |
| `01.BSGEE 2025-002 Invitation.pdf` | Rejected: encrypted PDF | unavailable | unavailable |
| `02.BSGEE 2025-002 Specifications - FINAL.pdf` | Passed | 50 | 118,157 |
| `03.BSGEE 2025-002 Specifications - ANNEXES A B C D.pdf` | Passed | 37 | 38,759 |
| `09.BSGEE 2025-002 FwC_services - FINAL.pdf` | Passed | 50 | 113,462 |

The existing parser rejects encrypted PDFs; we did not decrypt or change that behavior. Passing
means the PDF opened within evaluation bounds and yielded meaningful text, not that every table,
page or requirement was extracted accurately. Failed parser counters are unavailable, not evidence
that the invitation has zero pages. Acquisition and offline revalidation both exit **1** because
of this document failure, while retaining the successfully acquired files and partial results.

## Schema, references and annotation quality

The JSON root is an array. Each record has `id`, `family`, `question`, `answer`, `question_type`,
`type_number`, `hop_count`, `difficulty`, `relevant_chunks`, `source_documents`,
`documents_in_family` and `reasoning_note`. Questions/answers/notes are strings, references are
string arrays, and type/hop numbers are integers. Types/counts are: single-document lookup 23,
single-document reasoning 20, cross-document factual 20, implicit multi-hop 18 and unanswerable 16.
The card config places everything in `train`; independently curated held-out data is not provided.

All document filenames and chunk filename prefixes resolve to inventory entries. The selected
family contains 24 chunk references, representing 17 unique identifiers. They look like
`filename.pdf_ordinal`; the suffix supplies neither a page nor a TenderCite chunk ID. No original
chunk text/export, tokenizer/version, complete chunking/overlap policy, source offsets or page
mapping is supplied. The card's approximate **512-token** description cannot reconstruct these
boundaries reliably. We retain document-level references and leave chunk page/span fields null.
We did not search AI answers to fabricate gold quotes or guess spans from numeric suffixes.

**9 of 12 selected questions have usable document-level references**: every named source PDF
exists, passes checksums and parses. This is source availability, not a retrieval score or proof
that the reference answer is supported. Three cases depend on the encrypted invitation:

- `BSGEE_2025-002_School_Information_Systems-b0043663`
- `BSGEE_2025-002_School_Information_Systems-aad3fe25`
- `BSGEE_2025-002_School_Information_Systems-d6a6ae3e`

There are **0 unanswerable questions in this selected family**. Across the full metadata there
are 16; 15 have no sources/chunks and zero hops. The remaining case,
`EC-MOVE_2026_OP_0001_Road_Safety-36b82c04`, has a source/chunk reference and one hop. That
combination requires human review; it is flagged rather than silently reclassified.

The card describes chunk-based **LLM question generation** and says answers were extracted and
verified. It supplies no independent human reviewer identifiers, review states or evidence of
such review. All selected cases require human review. **0 cases have supplied exact source-span
ground truth or independently verifiable human-gold status.** None enters the existing human
gold dataset. This English pilot also provides no German retrieval-quality evidence.

## Reproduction and bounds

Use the existing locked development environment, from the repository root. The live run used
Linux, Python **3.12.14**, `huggingface_hub` **1.33.0** and `pypdf` **6.19.0**. No dependency was
added; the optional pilot reuses the already locked HF SDK and reads JSON without `datasets`.
These are exact one-line **PowerShell** commands; equivalent module invocations were executed
on Linux. Windows execution itself was not tested.

```powershell
.\.venv\Scripts\python.exe -m evaluation.acquire_external_benchmark --revision f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea --family BSGEE_2025-002_School_Information_Systems --download-pdfs --output-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems
.\.venv\Scripts\python.exe -m evaluation.acquire_external_benchmark --validate-only --output-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-school-systems
.\.venv\Scripts\python.exe -m evaluation.acquire_external_benchmark --revision f2b0856ef0a5c327afb4f40c7cde5dd44c8d56ea --output-dir .\data\evaluation\external\eu-tenders-qa-f2b0856-metadata
```

The third command is a metadata-only reproduction option, not an additional live run claimed
here. Acquisition accepts a ref or SHA and pins the resolved immutable SHA for downloads. PDFs
require one `--family` plus explicit `--download-pdfs`. The default output is
`data/evaluation/external/eu-tenders-qa-pilot`; custom paths must also remain under that ignored
external root. One output directory is tied to one revision/family. For another, use a new
directory. Symlinks, traversal, unsafe/Windows device filenames and staging escapes are rejected.

Bounds: at most 256 inventory files, 1,000 QA records, 2 MiB per metadata artifact, 25 MiB per
PDF, 32 PDFs / 64 MiB per selected family, and the existing PDF checks of 250 pages / 1,000,000
text characters with at least 100 letters / 20 words. No OCR or original chunk reconstruction
is added. Normal SDK retries/timeouts apply; API metadata/ETag timeouts are 20 seconds. No TLS,
proxy, WAF or authentication bypass is used; public downloads use no API key.

Local `manifest.json` retains artifact provenance and immutable byte checksums; `validation.json`
contains schema failures, parsing outcomes, per-case document/chunk references and review flags.
Reimports verify existing bytes and preserve first retrieval timestamps. Changed or missing
previously acquired files are reported without replacement; raw files are never overwritten.
`--validate-only` makes no network requests. Invalid/unsupported/empty metadata and acquisition
failures yield nonzero exit codes, not successful zero-result reports. Concurrent writers are
unsupported. The [committed small JSON summary](external-benchmark-pilot.json) contains only
our observations, IDs, filenames and hashes; full local validation stays under ignored storage.

## Provenance, reuse and milestone 3B

The card/API **declare MIT** for the dataset; no standalone license file or document-specific
rights inventory appears in this pinned repository. Procurement PDFs originate from European
institutions/organizations, while per-document original download URLs, rights grants and attribution
requirements are not supplied in the QA metadata. The dataset license declaration does not verify
the submitting curator's rights to every underlying document or relicense those documents.
The card's claim of no sensitive information is likewise not an independent privacy review.
Review attribution, original-source permissions, personal data and redistribution terms before
publishing source material. This PR contains no PDFs, raw dataset, questions, answers or PDF text.

**Milestone 3B needs preparation before a valid source-span retrieval benchmark.** A document-level
candidate experiment on nine cases is technically possible, subject to independently auditing
labels, rights, exclusions and tender-level splits. Exact source-span evaluation needs either
the original chunk export/configuration or separate manually reviewed page/quote annotations.
For the invitation-dependent cases, obtain a permitted parseable source or define reviewed
exclusions; no decryption workaround is part of this pilot. Existing manually imported PDFs,
human gold, production API/UI/retrieval and provider configuration remain unchanged.

Offline validation: **36 new synthetic/mocked tests; full suite 256 passed, 1 optional live
TED test skipped; Ruff lint and formatting passed.** Tests cover schema/references, missing files,
HTTP/proxy failures, checksums, paths, repeated/offline runs, unsupported/empty data and encrypted
partial coverage. Live results above are separately recorded and are not mocked evaluation scores.
