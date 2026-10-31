# TenderCite capability workflow feature status

| Feature | TenderCite HTTP | Standalone React UI | Remaining work |
| --- | --- | --- | --- |
| Unified native dashboard | Existing independently flagged list/detail APIs; no new endpoint | Default `/dashboard`, four-record previews, date-only action labels and saved-identity workflow links | Bounded first pages only; no exact aggregate counts or automatic actions; [guide](native-dashboard.md) |
| Native freelancer/company profiles | Available via separate local profiles flag | Profiles: create, reopen, edit skills/preferences and explicit revision-checked saves | Manual declarations only; no snapshot adapter |
| Native opportunities | Available via separate local opportunities flag | Opportunities: create/reopen/edit manual references or saved TED metadata, skills and date-only deadlines | Unverified; original imported attribution preserved; no analysis association |
| Official TED discovery | Explicit search/import via separate default-off discovery flag | `/discover`: keyword search, pagination, inspect, explicit save and native workflow links | Metadata only; no automatic skills/synchronization; live connectivity checked separately from synthetic tests; [guide](ted-discovery.md) |
| Native profile/opportunity matching | Read-only preview via separate default-off native matching flag | Opportunity Matching: saved-record selectors, explicit compare, original/normalized labels and counts | Exact label equality only; no semantic matching, qualification proof, scores or saved results; [guide](native-matching.md) |
| Native opportunity pipeline | Available via separate default-off pipeline flag | Explicit add, manual stage/priority/notes/next-action/date updates, filtering and reopen | One entry per existing opportunity; current revision only; no external submission/synchronization; [guide](native-pipeline.md) |
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
| Automatic external discovery / acquisition | Not yet | Not yet | Manual tracking exists; automatic actions remain separate work |

All mapping endpoints and the separately gated report endpoint remain local-development-only
and **disabled by default**. The [report API](capability-report-http.md) includes pseudonymous
individual coverage records; enabling the mapping API does not enable reports.
No source evidence, manual judgment or recorded skill assignment alone proves bidder
qualification. The MVP prioritizes UI feature completeness before sophistication.

Local setup, exact contracts, refresh/conflict behavior and limitations:
[manual skill mapping UI](manual-skill-mapping-ui.md) and
[capability matching UI](capability-matching-ui.md). Snapshot discovery needs the mapping flag;
report computation remains independently gated and supports manual exact-hash entry.
