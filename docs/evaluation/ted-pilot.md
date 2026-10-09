# TED evaluation acquisition pilot

This evaluation-only milestone starts from main `0c86c48` (merged PR #3) and contributes to
[issue #4](https://github.com/nak224/tendercite/issues/4). It does not complete v1.1 discovery:
there is no synchronization service, bidder matching, production API route or automatic analysis.

## Actual live outcome — 2026-10-09

The official public Search API returned **20 German competition notices from 1,114 matches**.
All 20 have `official-language=DEU` and `notice-type=cn-standard`. The API accepted country,
CPV prefix, inclusive publication-date filters and sorting. Adding the literal keyword
`Software` returned 925 matches (one metadata result requested). No API key was supplied.
The optional live smoke test passed separately from the offline mocked tests.

After reviewing titles and CPVs, **17 notices are provisional metadata candidates**.
Two hardware/installation notices and an earlier notice with the same ERP title were deferred
for diversity; the last pair's procedure/version relation is unverified. This is metadata
curation, not a review of tender requirements. Selection reasons, original-language evidence,
publication dates, official URLs and each failed download attempt are in the
[machine-readable candidate manifest](../../evaluation/ted-pilot/candidates.json).

| Acquisition/validation | Actual result |
| --- | --- |
| Fixed publication-ID lookups | 20/20 returned metadata; transient HTTP 429 responses recovered through retries |
| PDF downloads | 0/20; all returned HTTP 202 with `x-amzn-waf-action: challenge` |
| XML downloads | 0/20; same origin WAF challenge |
| Real PDF parsing | 0 passed, 0 failed, **20 not attempted** because no PDF bytes were obtained |
| Suitable-document shortlist | Empty; a target of 5–10 remains pending access and manual document review |
| Gold annotations / evaluation scores | None |

The failing document hostname is **`ted.europa.eu`**. The managed proxy initially rejected
all three TED domains with `403 Forbidden`; subsequently the documentation and search API
returned HTTP 200. The final document failures are origin AWS WAF challenges, not observed
proxy denials. No CAPTCHA/challenge handling, alternate-origin workaround or TLS/proxy bypass
was used. Empty HTTP 202 bodies are rejected before storage/parsing; they are not labeled scans.
The original discovery and fixed-ID timestamps and queries are retained in the manifest.
This pilot has obtained real notice **metadata**, but no real PDF/XML documents.

## Commands

Use Python 3.12 and the repository's existing locked development environment. From the checkout:

```bash
source .venv/bin/activate
# Metadata-only smoke; does not call embeddings or an LLM:
python -m evaluation.acquire_ted --smoke --limit 1 --output-dir data/evaluation/ted-smoke

# Bounded discovery/acquisition; optional --keyword Software adds a literal full-text filter:
python -m evaluation.acquire_ted --country DEU --cpv 72 \
  --date-from 2026-07-01 --date-to 2026-10-09 --limit 20 \
  --output-dir data/evaluation/ted-pilot-1 \
  --summary /tmp/ted-candidate-review.json

# Explicit fixed IDs, independent of current discovery ranking:
python -m evaluation.acquire_ted --notice-id 699958-2026 --notice-id 699278-2026 \
  --refresh --output-dir data/evaluation/ted-fixed
```

To reproduce every recorded publication ID after supported document access is available:

```bash
python - <<'PY'
import json
import subprocess
import sys

manifest = json.load(open("evaluation/ted-pilot/candidates.json"))
command = [sys.executable, "-m", "evaluation.acquire_ted", "--refresh",
           "--output-dir", "data/evaluation/ted-pilot-1"]
for notice_id in manifest["notice_ids"]:
    command.extend(["--notice-id", notice_id])
subprocess.run(command, check=True)
PY
```

The recorded fixed-ID attempt ran before reducing request pacing from two starts/second to
one following the observed rate limits. The final client allows at most one sequential request
start/second, including retries/redirects. Counts are acquisition observations, not accuracy scores.

After reading acquired PDFs, explicitly select suitable notices using repeated
`--shortlist-id PUBLICATION_ID`. An ID must pass original-language, title/date and PDF checks.
This marks selection for later annotation, never verified gold labels. `--include-other-forms`
allows award/planning notices; competition notices remain first within that bounded result page.
`--language deu` is the default required original language; another EU language can be requested.

Exit code 1 means an API, document, validation or shortlist error; the manifest preserves
successful acquisitions and failed attempts. A legitimate empty API result is distinguished from
a failed request: failures never manufacture `totalNoticeCount=0`. Existing complete local
acquisitions are reused only after verifying their hashes. `--refresh` fetches the same IDs again.
Dataset-version conflicts require a separate directory.

## Contract and limits

The small typed adapter is `src/tendercite/services/ted.py`; acquisition is an explicit CLI in
`evaluation/acquire_ted.py`, using `evaluation/ted_acquisition.py` and the existing pypdf parser.
It uses existing HTTPX/pypdf dependencies. TLS verification and environment proxy settings stay
enabled. It neither imports an embedding model nor configures/calls an LLM.

The verified production request is `POST https://api.ted.europa.eu/v3/notices/search`, with
`scope=ALL`, `paginationMode=PAGE_NUMBER`, `page=1`, eight requested fields and limit 1–50.
The inspectable expert query is:

```text
(place-of-performance IN (DEU)) AND (classification-cpv = 72*)
AND (publication-date >= 20260701) AND (publication-date <= 20261009)
AND (form-type = competition) SORT BY publication-date DESC
```

Country means **place of performance**, not buyer nationality. CPV accepts 2–8 digits:
shorter values become prefixes; eight-digit codes are exact. Literal keywords use
`AND (FT ~ "Software")`. Fixed lookup uses `publication-number = "699958-2026"`.
No LLM generates queries. These forms were accepted by the live API, and requested fields and
response shapes were checked against the current [official OpenAPI](https://api.ted.europa.eu/api-v3.yaml)
(SHA-256 in the manifest) and [Search API documentation](https://docs.ted.europa.eu/api/latest/search.html).

- One bounded page only; no bulk scroll, comprehensive recall or stable discovery ranking claim.
- Three attempts for transport/decoding failures, HTTP 429 and transient 5xx; 10-second connect
  and 30-second HTTP phase timeouts. `Retry-After` is honored up to 30 seconds; longer waits
  surface an error rather than retrying early. No challenge-response automation.
- Only API-advertised, HTTPS `ted.europa.eu/{lang}/notice/{publication-id}/{pdf|pdfs|xml}` URLs
  are fetched. Each redirect is revalidated against host, publication, format and language;
  at most three redirects. Unknown URL shapes require review. No third-party attachments.
- German PDF link availability or a German translated title does not establish original German:
  selection requires the API's `official-language`. Known XML original-language fields are
  cross-checked when available; conflicts block PDF acquisition/shortlisting. XML missing its
  original-language field remains explicitly unconfirmed against XML.
- Downloads are bounded to 25 MiB each. PDFs are capped at 250 pages / 1,000,000 extracted
  characters; at least 100 alphabetic characters and 20 words are required. These mechanical
  checks do not prove readability, completeness, German text quality or annotation suitability.
  No OCR; encrypted, malformed and low-text/scanned documents are recorded as failures.
- XML inspection supports UTF-8 eForms UBL notice roots and legacy `TED_EXPORT`; DTD/entities
  are rejected. This is well-formedness/metadata inspection, not XSD/Schematron validation.
- Publication IDs and SHA-256 content hashes deduplicate acquisition. Different publications can
  share one hash-addressed file. Related procedures, amendments and deadlines are not synchronized.

Local `manifest.json` records dataset version, queries, metadata/download timestamps, source URLs,
original languages, format, file path, SHA-256, parse outcomes and failures. Raw documents default
to ignored `data/evaluation/`; Docker also excludes `data`. A summary exports JSON metadata only;
review it before committing. Fixed IDs stabilize selection; upstream bytes can still change, so
retain checksums and raw local files when starting annotation.

## XML/eForms references for later manual annotation

The [official SDK 1.15.1 fields reference](https://github.com/OP-TED/eForms-SDK/blob/41adbdae8030f3f557845d477ace04310e1cc01a/fields/fields.json)
was inspected. Match each future document's `cbc:CustomizationID` to its own SDK version before
using these candidate references. None has been checked against an acquired XML in this pilot.

| Candidate reference | eForms field / XML location |
| --- | --- |
| Notice identity/version/type | BT-701-notice `cbc:ID[@schemeName='notice-id']`; `cbc:VersionID`; BT-03-notice `cbc:NoticeTypeCode/@listName` |
| Original language | BT-702(a)-notice `cbc:NoticeLanguageCode`; BT-702(b)-notice `cac:AdditionalNoticeLanguage/cbc:ID` |
| Subject and description | BT-21-Procedure / BT-24-Procedure: `cac:ProcurementProject/cbc:Name` / `cbc:Description`; CPV in `cac:RequiredCommodityClassification` |
| Submission deadline | BT-131(d/t)-Lot: lot `cac:TenderingProcess/cac:TenderSubmissionDeadlinePeriod/cbc:EndDate` / `cbc:EndTime` |
| Participation deadline | BT-1311(d/t)-Lot: lot `cac:TenderingProcess/cac:ParticipationRequestReceptionPeriod/cbc:EndDate` / `cbc:EndTime` |
| References/eligibility | BT-750-Lot: lot tendering-terms eForms extension `efac:SelectionCriteria/cbc:Description` |
| Award criteria/weights | BT-539/540-Lot: `cac:SubordinateAwardingCriterion` type/description; BT-541/542 parameters in `efac:AwardCriterionParameter`; BT-543 calculation expression |
| Performance/additional terms | BT-70-Lot `cac:ContractExecutionRequirement/cbc:Description`; BT-300-Lot procurement project `cbc:Note` |

Lots, units, deadlines/timezones, variants, cross-references and external specifications require
manual interpretation. Notice XML is a candidate reference, not authoritative gold for everything
in the tender package. Annotation should retain complete PDF source quotes with document hash,
page and span, independently review labels, and explicitly record absent/ambiguous requirements.
Do not convert XML fields or title topics into automatically verified labels.

## Provenance and reuse

Source: TED / Publications Office of the European Union and the submitting contracting
authorities. The [official API documentation](https://docs.ted.europa.eu/api/latest/search.html)
expressly supports published-notice analysis/reuse without authentication. Review the
[TED legal notice](https://ted.europa.eu/en/legal-notice) and
[official direct-download guidance](https://docs.ted.europa.eu/reuse/en/download-direct.html)
before redistributing documents. Public access alone is not a blanket copyright or personal-data
permission. TenderCite's Apache-2.0 license does not relicense notice text, attachments or datasets.
Retain attribution, source URLs, publication IDs, retrieval dates, languages and hashes;
review contact/personal data and third-party rights before publishing a corpus. This PR contains
only selected public metadata and original curation notes, no procurement PDF/XML bytes.

## Validation and remaining work

Normal CI runs offline HTTP mocks and generated PDF fixtures, including original-vs-translation
selection, XML language conflicts, unsafe redirects, rate limits, cache/hash deduplication and
failed/scanned/encrypted document handling. The opt-in live test is skipped by default and also
when `HF_HUB_OFFLINE=1`:

```bash
HF_HUB_OFFLINE=1 pytest
ruff check .
ruff format --check .
TENDERCITE_TED_LIVE=1 pytest tests/test_ted_live.py -m live
```

Offline validation: 141 passed, one intentionally skipped live test; Ruff lint/format pass.
Separately, the live search smoke passed once. Offline generated PDFs demonstrate downloader and
parser behavior; **they do not demonstrate successful real TED document acquisition**.

Remaining pilot work: obtain PDF/XML through supported access, confirm parsing/original languages,
review 5–10 suitable notices, check rights, version relations and complete manual gold annotation.
Only then run real-tender retrieval/context and LLM evaluations. No real-tender accuracy,
exhaustive extraction, completed issue #4 or v1.0.0 readiness is claimed.
