# TenderCite capability workflow feature status

| Feature | TenderCite HTTP | Standalone React UI | Remaining work |
| --- | --- | --- | --- |
| Native freelancer/company profiles | Available via separate local profiles flag | Profiles: create, reopen, edit skills/preferences and explicit revision-checked saves | Manual declarations only; no profile matching or snapshot adapter |
| PDF intake / source pages | Available | Available | Rich PDF viewer later |
| Manual requirements / human review | Available | Available | Review UX improvements later |
| Manual Go/No-Go assessment | Available | Available | No automatic compliance claims |
| Workforce snapshot export | Optional Team Capacity Engine producer | External producer only | Not part of standalone TenderCite |
| Workforce snapshot validate/import | Available (local flag) | Available | Full history/export stays CLI-only |
| Imported snapshot metadata list | Available via mapping API (local flag) | Bounded selector (50 records) | No full history/export view |
| Mapping overview/catalog | Available via mapping API (local flag) | Explicit analysis/snapshot scope and requirement inspection | Analyses discovered through current findings; no analysis-list API |
| Create and approve manual mapping | Available via mapping API (local flag) | Manual ALL_OF proposal → DRAFT → separate explicit APPROVE | No automatic mapping, approval or qualification proof |
| Mapping revision/revocation/history | CLI only | Not yet | Later milestone |
| Persisted capability assessment report | Available via separate report API flag | Capability Matching: explicit scope/selection, coverage, missing assignments and expandable pseudonymous details | UUID membership only; no qualification proof; CLI still available |
| Profile extraction / semantic match | Not yet | Not yet | Improve after manual end-to-end workflow |
| External opportunity discovery / acquisition | Not yet | Not yet | Later functional MVP work |

All mapping endpoints and the separately gated report endpoint remain local-development-only
and **disabled by default**. The [report API](capability-report-http.md) includes pseudonymous
individual coverage records; enabling the mapping API does not enable reports.
No source evidence, manual judgment or recorded skill assignment alone proves bidder
qualification. The MVP prioritizes UI feature completeness before sophistication.

Local setup, exact contracts, refresh/conflict behavior and limitations:
[manual skill mapping UI](manual-skill-mapping-ui.md) and
[capability matching UI](capability-matching-ui.md). Snapshot discovery needs the mapping flag;
report computation remains independently gated and supports manual exact-hash entry.
