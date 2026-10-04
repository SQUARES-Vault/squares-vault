# Changelog

All notable changes to SQUARES Vault will be documented here (Keep a Changelog format).

---

## [Unreleased]

### M0 — Foundation (done)
- Repository scaffolding: `pyproject.toml`, layout, `.env.example`
- `config.py`: typed config loader (env + file)
- `logging_setup.py`: structured logging with scrubbing filter (tokens, file names, doc text)
- `db/migrations/`: migration runner + full schema (Section 5 of blueprint)
- `security/crypto.py`: AES-256-GCM envelope encrypt/decrypt with round-trip tests
- `security/masking.py`: PII masking helpers
- `docs/ASSUMPTIONS.md`: decisions made where the blueprint was silent
