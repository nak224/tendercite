# Security policy

Report vulnerabilities privately through GitHub Security Advisories.

TenderCite is a **single-user local application without authentication**. Bind to loopback;
do not expose the API or UI directly to the Internet. Compose binds published ports to
127.0.0.1. Deploy behind separately managed authentication/TLS if broader access is needed.

## Boundaries

- Uploaded files are untrusted. PDF extension, MIME, header, parser and size checks apply.
- Default limits: 30 MiB, 500 pages, 2 million extracted characters. These limits are not
  a sandbox against decompression bombs or parser vulnerabilities; use container resource
  limits and trusted inputs. No OCR, embedded script execution or external PDF URL fetches.
- Generated UUID paths store PDFs. Original filenames are display metadata only.
- Parse/persistence failures clean up source files. Index failures retain source data for retry.
- Identical bytes reuse documents. SQLite writes are transactional; Chroma is a rebuildable
  index, not the authority for citations. Deleted-source vectors cannot become search results.
- An analysis retains its sources. Deletion returns 409 for referenced documents. To remove
  an entire confidential workspace, stop services and explicitly remove its data directory
  or named volume and backups. There is no selective analysis/history purge API yet.
- Original model output is immutable through the API. Review events and assessments are
  append-only. This is an application audit trail, **not** a tamper-proof or authenticated log.
- Uploaded text and model responses are not logged by application code. Do not enable HTTP
  body/debug logging for confidential data. Third-party libraries may emit diagnostics.
- Credentials belong in environment variables / secure secret storage. `.env`, uploads,
  SQLite files, model caches and Chroma data are ignored by Git and Docker build contexts.
- Explicitly configuring an LLM permits sending retrieved source passages to that provider.
  The UI requires a send-passages checkbox. API callers are responsible for authorization
  and provider retention/privacy terms. Prefer HTTPS; HTTP is intended for trusted local servers.
- LLM responses are schema-validated, bounded to 2 MB and never executed. Document instructions
  are labeled untrusted. Prompt injection can still produce misleading *statements*;
  deterministic quote matching does not establish semantic entailment or legal correctness.
- Every citation is checked against retrieved chunk identity and stored page text. Human
  confirmation cannot turn an invalid quote into a verified one. Review all findings.
- CSV exports neutralize formula prefixes; Markdown escapes embedded markup.

See [the dependency review](docs/security-review.md). Never describe this prototype as
vulnerability-free or production-hardened based solely on its tests.
