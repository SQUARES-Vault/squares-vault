# Changelog

All notable changes to SQUARES Vault will be documented here (Keep a Changelog format).

---

## [Unreleased]

### M2 — Onboarding (done)
- FSM for `/start` flow (`onboarding_start` -> `consent` -> `invite_code` -> `timezone` -> `done`).
- 18+ and Privacy Policy consent logged in `consent_log` (S9).
- Secure hashed invite code verification (`invite_codes` table).
- Hard limit of 100 users enforced for non-admins.
- Timezone prompt (default: Asia/Kolkata) with python `dateparser` logic.
- Admin bypass for invite gate.
- Bot entrypoint with `aiogram` 3.x long-polling
- PrivateChatOnlyMiddleware (ignores groups)
- RateLimitMiddleware (20 msg/min, 5 uploads/min)
- SqliteStorage for FSM (30 min state expiry)
- Main reply keyboard setup
- `/start`, `/help`, `/menu` handlers
- AdminOnly filter setup
- i18n basic JSON loader

### M0 — Foundation (done)
- Repository scaffolding: `pyproject.toml`, layout, `.env.example`
- `config.py`: typed config loader (env + file)
- `logging_setup.py`: structured logging with scrubbing filter (tokens, file names, doc text)
- `db/migrations/`: migration runner + full schema (Section 5 of blueprint)
- `security/crypto.py`: AES-256-GCM envelope encrypt/decrypt with round-trip tests
- `security/masking.py`: PII masking helpers
- `docs/ASSUMPTIONS.md`: decisions made where the blueprint was silent
