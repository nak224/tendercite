# Security review — 2026-10-07

Validated upload rejection (extension/MIME/header/malformed/encrypted/oversized files),
page/text limits, Unix/Windows filename handling, cleanup on persistence failure, secret
redaction, provider response limits, export injection defenses, and fabricated citations.
The test suite passed after these checks were added.

`pip-audit` was run against the installed development environment. Initial pytest 8.4.2 and
setuptools 78.1.0 advisories were addressed by requiring pytest >=9.0.3 and setuptools >=83
and regenerating hashed locks (installed pytest 9.1.1, setuptools 84.0.0).

The repeated audit still reports these Chroma 1.5.9 advisories, with no fix version supplied:

| Advisory | Reported attack surface | TenderCite deployment |
| --- | --- | --- |
| PYSEC-2026-311 | Server collection creation with attacker-supplied model and trust_remote_code | No Chroma server; explicit embedding_function=None |
| PYSEC-2026-3814 | Server collection update enabling remote model code | No Chroma collection-management endpoint exposed |
| PYSEC-2026-3813 | Server cross-tenant authorization | Embedded single-user store, no Chroma tenants exposed |
| PYSEC-2026-3815 | Server RBAC scope checks | Chroma authorization provider is not used |

These paths are not exposed by the current embedded design; this is an applicability
assessment, not a vendor fix. Reassess before adopting Chroma server mode or multi-tenancy.
The audit skips TenderCite itself and the `torch` CPU wheel version because those exact
versions are not available in its PyPI advisory lookup. This limits audit coverage.
The lockfiles preserve package hashes; they do not prove absence of vulnerabilities.

Remaining risks: unauthenticated local API, parser resource exhaustion, third-party code,
remote-provider privacy, uncalibrated model confidence, prompt injection, incorrect statements
with valid quotes, model-language mismatch and incomplete retrieval coverage. No penetration
test or independent security review is claimed. Do not deploy as a public multi-user service.
