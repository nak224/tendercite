# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories rather than opening a public issue.

## Security boundaries

TenderCite treats uploaded documents as untrusted input.

- Only PDF uploads are accepted by the MVP API.
- Upload size is bounded by configuration.
- Filenames are sanitized before storage.
- Document text is data, not executable instructions.
- API keys belong in environment variables and must not be committed.
- Uploaded files and local databases live under `data/`, which is git-ignored.

The project is not a legal decision system. Findings require human review before use in procurement decisions.
