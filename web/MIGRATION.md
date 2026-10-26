# Workspace migration provenance

The React TenderCite implementation was migrated from
[`nak224/team-capacity-engine`, branch `work`, commit `da3d0048f56a096a6a4ee7588a33d3a7ce667d7e`](https://github.com/nak224/team-capacity-engine/tree/da3d0048f56a096a6a4ee7588a33d3a7ce667d7e/frontend)
(merged UI-1b.4b.2 / PR #22). This is existing work by the Team Capacity Engine/TenderCite
contributors, maintained by `nak224`, carried forward into TenderCite rather than independently
reimplemented. No source-specific license/notice file was present in that pinned frontend tree;
the project's root LICENSE/NOTICE and dependency notices remain alongside this provenance record.

| Original `frontend/` path                                                                                 | Destination / adaptation                                                                                                                                             |
| --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/api/tendercite.ts`, `tenderciteIntake.ts`, `tenderciteReviews.ts`, `tenderciteAssessments.ts`        | Same filenames under `web/src/api/`, unchanged                                                                                                                       |
| `src/api/tenderciteSnapshots.ts`                                                                          | Same implementation; pure snapshot date utility imported independently                                                                                               |
| `src/pages/ManualIntake.tsx`, `TenderAssessment.tsx`                                                      | Same filenames under `web/src/pages/`, unchanged                                                                                                                     |
| `src/pages/Tenders.tsx`                                                                                   | Same source/analysis/finding/page/review routes; remove external workforce-export page/link only                                                                     |
| `src/pages/ImportWorkforceSnapshot.tsx`                                                                   | Same state machine and checks; add optional-producer instructions                                                                                                    |
| `src/components/FindingReview.tsx`, `ManualRequirementFields.tsx`, `TenderUpload.tsx`                     | Same filenames under `web/src/components/`, unchanged                                                                                                                |
| `src/components/ui.tsx`                                                                                   | Keep Async/Empty/Badge presentation helpers; remove workforce-schema/capacity/planning helpers                                                                       |
| `src/calendar.ts`, `src/api/workforceSnapshot.ts`                                                         | Extract only pure date/period metadata validation into `web/src/utils/period.ts`; do not copy planning state, generated types or export calls                        |
| `src/styles.css`                                                                                          | Retain shared form/panel/status/source text and responsive shell rules; omit workforce-specific controls/tables/cards                                                |
| TenderCite API/component/page tests (12 modules)                                                          | Migrate all modules. Adapt proxy assertions, four removed TCE-route assertions and shell/export navigation; keep evidence, mutation, identity, error and retry cases |
| `src/test/{intake,reviews,assessments,snapshot,snapshotImport,tenderPdf,setup}.ts` and capability fixture | Copy original synthetic fixtures/helpers unchanged                                                                                                                   |
| Package/tooling configuration                                                                             | Compatible exact versions, independent package/lock; omit unused OpenAPI client/generator and TCE browser infrastructure                                             |

The app/layout/query-client configuration is now TenderCite-only. The query cache identity and
30-second stale period remain; all queries/mutations default to no automatic retries, matching
the existing TenderCite queries' explicit retry settings. `/tenders` URLs and every backend
contract/identity remain unchanged. Snapshot metadata keeps the existing 1–366-day validation.
The frontend has no import of Team Capacity's generated schema, API application or database.

Attribution is also recorded in the root NOTICE. Runtime/development dependencies keep their
own licenses; see THIRD_PARTY_NOTICES.md and the committed lockfile. There are no third-party
tender documents, personnel records or paid responses in the migrated fixtures.

The original repository is not edited/deleted by ARCH-1a. Retire that fallback only through a
separate reviewed Team Capacity cleanup PR after operator verification; keep its snapshot
generation service and core workforce functionality. Mapping UI and other new product features
are outside this migration. See [local setup and boundaries](../docs/web-workspace.md).
